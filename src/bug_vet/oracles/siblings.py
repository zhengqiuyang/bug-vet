"""Sibling implementation asymmetry oracle (ABC subclasses, provider functions, env vars, TS commands)."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from bug_vet.oracles.base import Candidate, iter_python_files, iter_source_files, rel_posix
from bug_vet.paths import ensure_run

TRIVIAL_BASES = frozenset(
    {
        "object",
        "Exception",
        "BaseException",
        "Enum",
        "IntEnum",
        "StrEnum",
        "Flag",
        "IntFlag",
        "Protocol",
        "ABC",
        "BaseModel",
        "TypedDict",
        "NamedTuple",
        "Generic",
        "dict",
        "list",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
    }
)

SIBLING_FUNC_RE = re.compile(r"^(?P<entity>[a-z]+)_to_(?P<provider>[a-z0-9_]+)$")
API_ENV_RE = re.compile(r"^([A-Z][A-Z0-9_]*)_API_(BASE|KEY)$")
HTTP_TUPLE_PREFIXES = ("http://", "https://")
TS_IMPORT_RE = re.compile(
    r"""import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]""",
    re.MULTILINE | re.DOTALL,
)
VALIDATOR_NAME_RE = re.compile(r"^(?:assert|validate)[A-Z]\w*Options$")


def _attr_or_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _base_name(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Subscript):
        return _base_name(expr.value)
    return _attr_or_name(expr)


def _camel_callee(node: ast.Call) -> str | None:
    name = _attr_or_name(node.func)
    if name and name[0].isupper():
        return name
    return None


def _is_fallback_constructor_call(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "content_fetch_failed" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
        if kw.arg == "patch" and isinstance(kw.value, ast.Constant) and kw.value.value == "":
            return True
    if len(call.args) >= 3 and isinstance(call.args[2], ast.Constant) and call.args[2].value == "":
        return True
    return False


def _scan_abc_constructor_asymmetry(
    parsed_files: list[tuple[Path, str, ast.Module]],
) -> list[Candidate]:
    """Rule 1A: Detect constructor kwargs passed by >=50% sibling subclasses but omitted by a minority."""
    by_base: dict[str, list[tuple[str, ast.ClassDef]]] = {}
    for _path, rel, tree in parsed_files:
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for base_expr in node.bases:
                bname = _base_name(base_expr)
                if not bname or bname in TRIVIAL_BASES:
                    continue
                by_base.setdefault(bname, []).append((rel, node))

    candidates: list[Candidate] = []
    for base_name, subclasses in sorted(by_base.items()):
        if len(subclasses) < 3:
            continue

        # Collect methods present in >= 3 subclasses
        methods: dict[str, list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
        for rel, cls in subclasses:
            for stmt in cls.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and not stmt.name.startswith("__"):
                    methods.setdefault(stmt.name, []).append((rel, cls.name, stmt))

        for method_name, impls in sorted(methods.items()):
            if len(impls) < 3:
                continue

            # callee -> [(rel, cls_name, method_line, calls_info)]
            callee_impls: dict[str, list[tuple[str, str, int, list[tuple[int, set[str], bool]]]]] = {}
            for rel, cls_name, fn in impls:
                calls_info: dict[str, list[tuple[int, set[str], bool]]] = {}
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    cname = _camel_callee(node)
                    if not cname or cname.endswith(("Error", "Exception", "Warning")):
                        continue
                    if _is_fallback_constructor_call(node):
                        continue
                    has_varkw = any(kw.arg is None for kw in node.keywords)
                    kws = {kw.arg for kw in node.keywords if kw.arg is not None}
                    calls_info.setdefault(cname, []).append((node.lineno, kws, has_varkw))
                for cname, clist in calls_info.items():
                    callee_impls.setdefault(cname, []).append((rel, cls_name, fn.lineno, clist))

            for cname, records in sorted(callee_impls.items()):
                if len(records) < 3:
                    continue
                kw_counts: Counter[str] = Counter()
                per_subclass_kws: list[tuple[str, str, int, set[str], bool]] = []
                for rel, cls_name, first_line, clist in records:
                    union_kws: set[str] = set()
                    any_varkw = False
                    best_line = clist[0][0] if clist else first_line
                    for lineno, kws, has_varkw in clist:
                        union_kws.update(kws)
                        if has_varkw:
                            any_varkw = True
                    for kw in union_kws:
                        kw_counts[kw] += 1
                    per_subclass_kws.append((rel, cls_name, best_line, union_kws, any_varkw))

                n_impls = len(per_subclass_kws)
                majority_kws = sorted(
                    kw for kw, cnt in kw_counts.items() if cnt >= 2 and cnt * 2 >= n_impls
                )
                if not majority_kws:
                    continue

                for rel, cls_name, call_line, union_kws, any_varkw in per_subclass_kws:
                    if any_varkw:
                        continue
                    missing = [kw for kw in majority_kws if kw not in union_kws]
                    if not missing:
                        continue
                    candidates.append(
                        Candidate(
                            oracle="siblings",
                            family="sibling-asymmetry",
                            file=rel,
                            line=call_line,
                            symbol=method_name,
                            detail=(
                                f"{cls_name}.{method_name} calls {cname}(...) omitting majority kwarg(s) "
                                f"{', '.join(missing)} passed by >=50% of {base_name} siblings ({n_impls} checked)"
                            ),
                            confidence="high",
                            evidence={
                                "rule": "abc-kwarg-asymmetry",
                                "base": base_name,
                                "class": cls_name,
                                "callee": cname,
                                "missing_kwargs": missing,
                                "majority_kwargs": majority_kws,
                                "sibling_count": n_impls,
                            },
                            playbook_id="sibling-asymmetry",
                        )
                    )
    return candidates


def _has_raise(node: ast.AST) -> bool:
    return any(isinstance(sub, ast.Raise) for sub in ast.walk(node))


def _dict_returned_param_attrs(fn: ast.AST, param_name: str) -> set[str]:
    attrs: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            for sub in ast.walk(node.value):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == param_name
                ):
                    attrs.add(sub.attr)
    return attrs


def _checks_http_startswith(fn: ast.AST) -> tuple[bool, bool]:
    """Return ``(has_startswith_http, case_sensitive_startswith)``."""
    found = False
    case_sensitive = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            arg_text = ast.unparse(node.args[0]) if node.args else ""
            if "http://" in arg_text and "https://" in arg_text:
                found = True
                target_text = ast.unparse(node.func.value)
                if ".lower()" not in target_text and ".casefold()" not in target_text:
                    case_sensitive = True
    return found, case_sensitive


def _scan_sibling_provider_guards(
    parsed_files: list[tuple[Path, str, ast.Module]],
) -> list[Candidate]:
    """Rule 1B: Detect ``<entity>_to_<provider>`` functions returning unfilled attributes without raising."""
    groups: dict[str, list[tuple[str, str, ast.FunctionDef]]] = {}
    for _path, rel, tree in parsed_files:
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            match = SIBLING_FUNC_RE.match(node.name)
            if not match:
                continue
            groups.setdefault(match.group("entity"), []).append((match.group("provider"), rel, node))

    candidates: list[Candidate] = []
    for entity, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        info: list[tuple[str, str, ast.FunctionDef, bool, bool, bool, set[str]]] = []
        for provider, rel, fn in members:
            params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
            if not params:
                continue
            first_param = params[0]
            raises = _has_raise(fn)
            has_http, case_sens = _checks_http_startswith(fn)
            ret_attrs = _dict_returned_param_attrs(fn, first_param)
            info.append((provider, rel, fn, raises, has_http, case_sens, ret_attrs))

        raising_peers = [p for p, _, _, raises, _, _, _ in info if raises]
        if not raising_peers:
            continue

        for provider, rel, fn, raises, has_http, case_sens, ret_attrs in info:
            if not raises and has_http and ret_attrs:
                attrs = ", ".join(sorted(ret_attrs))
                cs_note = " (case-sensitive startswith)" if case_sens else ""
                candidates.append(
                    Candidate(
                        oracle="siblings",
                        family="sibling-asymmetry",
                        file=rel,
                        line=fn.lineno,
                        symbol=fn.name,
                        detail=(
                            f"{fn.name} conditionally handles URL prefix{cs_note} and returns payload "
                            f"attribute(s) ({attrs}) without raising on missing data, unlike siblings "
                            f"({', '.join(sorted(raising_peers))})"
                        ),
                        confidence="high",
                        evidence={
                            "rule": "sibling-guard-asymmetry",
                            "entity": entity,
                            "provider": provider,
                            "raising_siblings": sorted(raising_peers),
                            "returned_attrs": sorted(ret_attrs),
                        },
                        playbook_id="sibling-asymmetry",
                    )
                )
    return candidates


def _env_reads_in_node(node: ast.AST) -> dict[str, set[str]]:
    """Return ``{'BASE': {PREFIX...}, 'KEY': {PREFIX...}}`` for ``*_API_(BASE|KEY)`` literals."""
    found: dict[str, set[str]] = {"BASE": set(), "KEY": set()}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            m = API_ENV_RE.match(sub.value)
            if m:
                found[m.group(2)].add(m.group(1))
    return found


def _passes_kwarg(node: ast.AST, kw_name: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            for kw in sub.keywords:
                if kw.arg == kw_name:
                    return True
    return False


def _flatten_elif_chain(stmt: ast.If) -> list[ast.If]:
    arms = [stmt]
    cur = stmt
    while cur.orelse and len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
        cur = cur.orelse[0]
        arms.append(cur)
    return arms


def _scan_env_asymmetry(
    parsed_files: list[tuple[Path, str, ast.Module]],
) -> list[Candidate]:
    """Rule 1C: Flag dispatch branches reading ``P_API_KEY`` while omitting ``P_API_BASE`` used elsewhere."""
    all_base_prefixes: set[str] = set()
    for _path, _rel, tree in parsed_files:
        all_base_prefixes.update(_env_reads_in_node(tree)["BASE"])

    if not all_base_prefixes:
        return []

    candidates: list[Candidate] = []
    seen_locs: set[tuple[str, int, str]] = set()

    for _path, rel, tree in parsed_files:
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            # Inspect top-level and nested If/elif chains inside each function
            for sub in ast.walk(node):
                if not isinstance(sub, ast.If):
                    continue
                arms = _flatten_elif_chain(sub)
                if len(arms) < 3:
                    continue

                # Count how many sibling arms read any *_API_BASE
                arm_envs = [_env_reads_in_node(ast.Module(body=arm.body, type_ignores=[])) for arm in arms]
                arms_with_base = sum(1 for item in arm_envs if item["BASE"])
                if arms_with_base < 2:
                    continue

                for arm, envs in zip(arms, arm_envs):
                    for prefix in sorted(envs["KEY"]):
                        if prefix in envs["BASE"]:
                            continue
                        if prefix not in all_base_prefixes:
                            continue
                        body_mod = ast.Module(body=arm.body, type_ignores=[])
                        if not _passes_kwarg(body_mod, "api_base"):
                            continue
                        env_base = f"{prefix}_API_BASE"
                        env_key = f"{prefix}_API_KEY"
                        key = (rel, arm.lineno, env_base)
                        if key in seen_locs:
                            continue
                        seen_locs.add(key)
                        candidates.append(
                            Candidate(
                                oracle="siblings",
                                family="sibling-asymmetry",
                                file=rel,
                                line=arm.lineno,
                                symbol=node.name,
                                detail=(
                                    f"branch in {node.name} reads {env_key} and passes api_base without "
                                    f"reading {env_base}, unlike sibling branches ({arms_with_base} read "
                                    f"*_API_BASE and {env_base} exists elsewhere)"
                                ),
                                confidence="medium",
                                evidence={
                                    "rule": "env-asymmetry",
                                    "env_var": env_base,
                                    "peer_env": env_key,
                                    "provider_prefix": prefix.lower(),
                                    "sibling_base_arms": arms_with_base,
                                },
                                playbook_id="sibling-asymmetry",
                            )
                        )
    return candidates


def _scan_ts_command_validation_asymmetry(root: Path) -> list[Candidate]:
    """Rule 1D: Flag TS/JS command modules checking shared option flags without the shared validator."""
    ts_files = iter_source_files(root, suffixes=(".ts", ".js", ".tsx", ".jsx"))
    if len(ts_files) < 2:
        return []

    by_dir: dict[Path, list[tuple[Path, str, str]]] = {}
    for file in ts_files:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        by_dir.setdefault(file.parent, []).append((file, rel_posix(file, root), text))

    candidates: list[Candidate] = []
    for _folder, entries in sorted(by_dir.items()):
        if len(entries) < 2:
            continue

        # Find shared validators: (module_suffix, validator_name) -> {source_files, flag_tokens}
        validator_users: dict[tuple[str, str], list[str]] = {}
        file_imports: dict[str, dict[str, set[str]]] = {}  # rel -> {mod_basename: set(imported_names)}

        for _file, rel, text in entries:
            mods: dict[str, set[str]] = {}
            for m in TS_IMPORT_RE.finditer(text):
                names = {
                    part.strip().split(" as ")[0].removeprefix("type ").strip()
                    for part in m.group(1).split(",")
                    if part.strip()
                }
                mod_base = Path(m.group(2)).stem
                mods.setdefault(mod_base, set()).update(names)
                for name in names:
                    if VALIDATOR_NAME_RE.match(name):
                        validator_users.setdefault((mod_base, name), []).append(rel)
            file_imports[rel] = mods

        for (mod_base, validator_name), users in sorted(validator_users.items()):
            # Derive domain tokens from validator name, e.g. assertJUnitReportOptions -> {'junit', 'report'}
            camel_tokens = {
                tok.lower()
                for tok in re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+", validator_name)
                if tok.lower() not in {"assert", "validate", "check", "ensure", "no", "options", "opts"}
                and len(tok) >= 3
            }
            # Find files that import from the same helper module AND check matching domain options manually
            for file, rel, text in entries:
                if rel in users:
                    continue
                if mod_base not in file_imports.get(rel, {}):
                    continue
                if re.search(rf"\b{re.escape(validator_name)}\s*\(", text):
                    continue
                if camel_tokens:
                    token_pattern = "|".join(re.escape(tok) for tok in sorted(camel_tokens))
                    match = re.search(rf"""(?:opts\.|\b)(?:{token_pattern})\w*\s*[!=]==|['"](?:{token_pattern})['"]""", text, re.I)
                else:
                    match = re.search(r"""\.report\s*===|['"]report-file['"]|['"]junit['"]""", text)
                if not match:
                    continue
                line_no = text[: match.start()].count("\n") + 1
                candidates.append(
                    Candidate(
                        oracle="siblings",
                        family="sibling-asymmetry",
                        file=rel,
                        line=line_no,
                        symbol=file.stem,
                        detail=(
                            f"imports from '{mod_base}' and checks ({', '.join(sorted(camel_tokens))}) "
                            f"options manually without calling shared validator {validator_name} "
                            f"(used by {', '.join(users)})"
                        ),
                        confidence="high",
                        evidence={
                            "rule": "ts-validator-asymmetry",
                            "shared_module": mod_base,
                            "validator": validator_name,
                            "validator_users": users,
                        },
                        playbook_id="sibling-asymmetry",
                    )
                )
    return candidates


def scan_siblings(root: str | Path, *, include_ts: bool = True) -> list[Candidate]:
    """Run all sibling asymmetry rules over ``root``."""
    target = Path(root).resolve()
    if not target.exists():
        raise FileNotFoundError(f"target path does not exist: {target}")

    parsed_files: list[tuple[Path, str, ast.Module]] = []
    for file in iter_python_files(target):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        parsed_files.append((file, rel_posix(file, target), tree))

    candidates: list[Candidate] = []
    candidates.extend(_scan_abc_constructor_asymmetry(parsed_files))
    candidates.extend(_scan_sibling_provider_guards(parsed_files))
    candidates.extend(_scan_env_asymmetry(parsed_files))
    if include_ts:
        candidates.extend(_scan_ts_command_validation_asymmetry(target))

    seen: set[tuple] = set()
    unique: list[Candidate] = []
    for cand in candidates:
        k = (cand.file, cand.line, cand.symbol, cand.detail)
        if k not in seen:
            seen.add(k)
            unique.append(cand)
    return unique


def run_siblings_scan(
    run_name: str,
    target_path: str | Path,
    *,
    runs_dir: str | Path | None = None,
    include_ts: bool = True,
    register_obligations: bool = False,
) -> dict[str, Any]:
    """Execute ``scan_siblings``, write ``siblings-results.json``, and optionally register obligations."""
    from bug_vet import ledger as ledger_mod

    cands = scan_siblings(target_path, include_ts=include_ts)
    run_path = ensure_run(run_name, runs_dir=runs_dir)
    if register_obligations:
        for idx, cand in enumerate(cands, start=1):
            slug = ledger_mod.digest(cand.file, cand.line, cand.symbol, cand.family)[:8]
            ledger_mod.add_obligation(
                run_path,
                {
                    "id": f"sib-{slug}-{idx}",
                    "origin": "oracle:siblings",
                    "kind": cand.family,
                    "text": cand.detail,
                    "source": cand.file,
                    "line": cand.line,
                    "symbol": cand.symbol,
                    "visibility": "public",
                },
            )

    payload = {
        "run": run_name,
        "oracle": "siblings",
        "count": len(cands),
        "candidates": [c.to_dict() for c in cands],
    }
    artifact = run_path / "siblings-results.json"
    artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload["artifact"] = str(artifact)
    return payload
