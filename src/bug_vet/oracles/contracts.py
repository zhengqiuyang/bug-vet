"""Type-contract narrowing and lifecycle safety oracle."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from bug_vet.oracles.base import Candidate, iter_python_files, rel_posix
from bug_vet.paths import ensure_run

SEQUENCE_TYPE_NAMES = frozenset({"Sequence", "MutableSequence", "Iterable", "Collection"})
MAPPING_TYPE_NAMES = frozenset({"Mapping", "MutableMapping"})
DICT_MUTATION_METHODS = frozenset({"clear", "update", "pop", "popitem", "setdefault"})
REREGISTER_METHOD_PREFIXES = ("register", "attach", "bind", "upsert", "add_session")
DESTRUCTIVE_CALLS = frozenset({"clear", "close", "cancel", "discard", "shutdown", "stop"})


def _is_isinstance_dict_call(node: ast.AST, map_params: set[str]) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Name) or node.func.id != "isinstance" or len(node.args) != 2:
        return None
    target_arg, classinfo = node.args
    if isinstance(target_arg, ast.Name) and target_arg.id in map_params:
        if isinstance(classinfo, ast.Name) and classinfo.id == "dict":
            return target_arg.id
    return None


def _mutates_dict_in_place(body: list[ast.stmt], pname: str) -> bool:
    mod = ast.Module(body=body, type_ignores=[])
    for sub in ast.walk(mod):
        if isinstance(sub, (ast.Return, ast.Raise)):
            return False
    for sub in ast.walk(mod):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == pname
            and sub.func.attr in DICT_MUTATION_METHODS
        ):
            return True
        targets: list[ast.expr] = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, ast.AugAssign):
            targets = [sub.target]
        elif isinstance(sub, ast.Delete):
            targets = list(sub.targets)
        for tgt in targets:
            if (
                isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name)
                and tgt.value.id == pname
            ):
                return True
    return False


def _collect_inplace_dict_guard_calls(fn: ast.AST, map_params: set[str]) -> set[int]:
    """Identify intentional ``if isinstance(m, dict): <mutate in place>`` guards with no else/return/raise."""
    if not map_params:
        return set()
    ignored: set[int] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or node.orelse:
            continue
        test_terms = (
            list(node.test.values)
            if isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.And)
            else [node.test]
        )
        for term in test_terms:
            pname = _is_isinstance_dict_call(term, map_params)
            if pname and _mutates_dict_in_place(node.body, pname):
                ignored.add(id(term))
    return ignored


def _annotation_names(ann: ast.AST | None) -> set[str]:
    if ann is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(ann):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _scan_type_narrowing(rel: str, tree: ast.Module) -> list[Candidate]:
    """Rule 3A: Flag ``isinstance(param, list)`` / ``dict`` when ``param`` is typed as ``Sequence`` / ``Mapping``."""
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        all_args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        seq_params: set[str] = set()
        map_params: set[str] = set()
        for arg in all_args:
            names = _annotation_names(arg.annotation)
            if names & SEQUENCE_TYPE_NAMES:
                seq_params.add(arg.arg)
            if names & MAPPING_TYPE_NAMES:
                map_params.add(arg.arg)
        if not seq_params and not map_params:
            continue

        ignored_dict_guards = _collect_inplace_dict_guard_calls(node, map_params)
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            if not isinstance(sub.func, ast.Name) or sub.func.id != "isinstance" or len(sub.args) != 2:
                continue
            target_arg, classinfo = sub.args
            if not isinstance(target_arg, ast.Name):
                continue
            pname = target_arg.id
            if not isinstance(classinfo, ast.Name):
                continue
            checked_type = classinfo.id
            if pname in seq_params and checked_type == "list":
                candidates.append(
                    Candidate(
                        oracle="contracts",
                        family="type-contract",
                        file=rel,
                        line=sub.lineno,
                        symbol=node.name,
                        detail=(
                            f"{node.name} annotates '{pname}' as Sequence/Iterable but narrows with "
                            "isinstance(..., list), rejecting tuples and other valid sequences"
                        ),
                        confidence="high",
                        evidence={"rule": "sequence-isinstance-list", "param": pname},
                        playbook_id="sibling-asymmetry",
                    )
                )
            elif pname in map_params and checked_type == "dict":
                if id(sub) in ignored_dict_guards:
                    continue
                candidates.append(
                    Candidate(
                        oracle="contracts",
                        family="type-contract",
                        file=rel,
                        line=sub.lineno,
                        symbol=node.name,
                        detail=(
                            f"{node.name} annotates '{pname}' as Mapping but narrows with "
                            "isinstance(..., dict), rejecting other Mapping types"
                        ),
                        confidence="high",
                        evidence={"rule": "mapping-isinstance-dict", "param": pname},
                        playbook_id="sibling-asymmetry",
                    )
                )
    return candidates


def _has_identity_guard(body: list[ast.stmt], existing_var: str, param_names: set[str]) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Compare):
                continue
            if any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                if existing_var in names and (names & param_names):
                    return True
    return False


def _find_destructive_call_on(node: ast.AST, var_name: str) -> str | None:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
            continue
        if sub.func.attr not in DESTRUCTIVE_CALLS:
            continue
        # match existing.clear() or existing.attr.clear()
        root_expr = sub.func.value
        while isinstance(root_expr, ast.Attribute):
            root_expr = root_expr.value
        if isinstance(root_expr, ast.Name) and root_expr.id == var_name:
            return sub.func.attr
    return None


def _scan_reregistration_eviction(rel: str, tree: ast.Module) -> list[Candidate]:
    """Rule 3B: Flag register* methods mutating/clearing ``existing`` without an ``existing is handle`` check."""
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.lower().startswith(REREGISTER_METHOD_PREFIXES):
            continue
        param_names = {a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs] if a.arg != "self"}
        if not param_names:
            continue

        existing_vars: dict[str, int] = {}
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and stmt.value.func.attr == "get"
            ):
                existing_vars[stmt.targets[0].id] = stmt.lineno

        if not existing_vars:
            continue

        for var_name in existing_vars:
            if _has_identity_guard(node.body, var_name, param_names):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.If):
                    continue
                test_names = {n.id for n in ast.walk(stmt.test) if isinstance(n, ast.Name)}
                if var_name not in test_names:
                    continue
                destructive = _find_destructive_call_on(ast.Module(body=stmt.body, type_ignores=[]), var_name)
                if destructive:
                    candidates.append(
                        Candidate(
                            oracle="contracts",
                            family="lifecycle-reregister",
                            file=rel,
                            line=stmt.lineno,
                            symbol=node.name,
                            detail=(
                                f"{node.name} mutates/clears '{var_name}' (.{destructive}()) on lookup "
                                "without checking `existing is <handle>` first; idempotent re-registration "
                                "wipes live state"
                            ),
                            confidence="high",
                            evidence={
                                "rule": "reregister-self-eviction",
                                "existing_var": var_name,
                                "destructive_call": destructive,
                            },
                            playbook_id="split-brain-dual-path",
                        )
                    )
                    break
    return candidates


def _is_coordinator_data_expr(expr: ast.AST) -> bool:
    """Match ``<x>.coordinator.data`` or ``<x>._coordinator.data``."""
    return (
        isinstance(expr, ast.Attribute)
        and expr.attr == "data"
        and isinstance(expr.value, ast.Attribute)
        and expr.value.attr in {"coordinator", "_coordinator"}
    )


def _walk_without_keyerror_try(stmt: ast.AST, *, in_keyerror_try: bool = False) -> list[ast.Subscript]:
    found: list[ast.Subscript] = []
    if isinstance(stmt, ast.Try):
        catches_keyerror = any(
            h.type is None or "KeyError" in ast.unparse(h.type) for h in stmt.handlers
        )
        for child in stmt.body:
            found.extend(_walk_without_keyerror_try(child, in_keyerror_try=in_keyerror_try or catches_keyerror))
        for handler in stmt.handlers:
            for child in handler.body:
                found.extend(_walk_without_keyerror_try(child, in_keyerror_try=in_keyerror_try))
        for child in stmt.orelse + stmt.finalbody:
            found.extend(_walk_without_keyerror_try(child, in_keyerror_try=in_keyerror_try))
        return found

    if isinstance(stmt, ast.Subscript) and isinstance(stmt.ctx, ast.Load):
        if not in_keyerror_try and _is_coordinator_data_expr(stmt.value):
            found.append(stmt)

    for child in ast.iter_child_nodes(stmt):
        found.extend(_walk_without_keyerror_try(child, in_keyerror_try=in_keyerror_try))
    return found


def _has_coordinator_guard(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if _is_coordinator_data_expr(node.func.value):
                return True
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            if any(_is_coordinator_data_expr(comp) for comp in node.comparators):
                return True
    return False


def _scan_bare_snapshot_subscript(rel: str, tree: ast.Module) -> list[Candidate]:
    """Rule 3C: Flag unguarded ``coordinator.data[key]`` subscripts that crash if a snapshot row disappears."""
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if _has_coordinator_guard(node):
            continue
        subscripts = _walk_without_keyerror_try(node)
        for sub in subscripts:
            key_expr = ast.unparse(sub.slice)
            candidates.append(
                Candidate(
                    oracle="contracts",
                    family="lifecycle-missing-key",
                    file=rel,
                    line=sub.lineno,
                    symbol=node.name,
                    detail=(
                        f"{node.name} indexes coordinator.data[{key_expr}] directly without .get(), "
                        "membership check, or KeyError guard; raises KeyError if snapshot row disappears"
                    ),
                    confidence="high",
                    evidence={"rule": "bare-coordinator-subscript", "key": key_expr},
                    playbook_id="lifecycle-missing-key",
                )
            )
    return candidates


def scan_contracts(root: str | Path) -> list[Candidate]:
    """Run all type-contract and lifecycle safety checks over ``root``."""
    target = Path(root).resolve()
    if not target.exists():
        raise FileNotFoundError(f"target path does not exist: {target}")

    candidates: list[Candidate] = []
    for file in iter_python_files(target):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        rel = rel_posix(file, target)
        candidates.extend(_scan_type_narrowing(rel, tree))
        candidates.extend(_scan_reregistration_eviction(rel, tree))
        candidates.extend(_scan_bare_snapshot_subscript(rel, tree))

    seen: set[tuple] = set()
    unique: list[Candidate] = []
    for cand in candidates:
        k = (cand.file, cand.line, cand.symbol, cand.detail)
        if k not in seen:
            seen.add(k)
            unique.append(cand)
    return unique


def run_contracts_scan(
    run_name: str,
    target_path: str | Path,
    *,
    runs_dir: str | Path | None = None,
    register_obligations: bool = False,
) -> dict[str, Any]:
    """Execute ``scan_contracts`` and persist ``contracts-results.json``."""
    from bug_vet import ledger as ledger_mod

    cands = scan_contracts(target_path)
    run_path = ensure_run(run_name, runs_dir=runs_dir)
    if register_obligations:
        for idx, cand in enumerate(cands, start=1):
            slug = ledger_mod.digest(cand.file, cand.line, cand.symbol, cand.family)[:8]
            ledger_mod.add_obligation(
                run_path,
                {
                    "id": f"ct-{slug}-{idx}",
                    "origin": "oracle:contracts",
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
        "oracle": "contracts",
        "count": len(cands),
        "candidates": [c.to_dict() for c in cands],
    }
    artifact = run_path / "contracts-results.json"
    artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload["artifact"] = str(artifact)
    return payload
