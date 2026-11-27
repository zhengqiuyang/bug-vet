"""Parser metadata-as-data, delimiter, and deserialization guard oracle."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from bug_vet.oracles.base import Candidate, iter_python_files, rel_posix
from bug_vet.paths import ensure_run

ADVERSARIAL_CORPUS: dict[str, list[Any]] = {
    "code_fences": [
        "```markdown\n- Handle `None` in `parse()`\n```",
        "```markdown\n- Ends with inline `code`\n",
        "````markdown\n```python\nprint('nested')\n```\n````",
        "~~~python\nx = 'tilde fence'\n~~~",
    ],
    "sse_terminators": [
        b"data: one\n\ndata: two\n\n",
        b"data: one\r\n\r\ndata: two\r\n\r\n",
        b"data: one\r\rdata: two\r\r",
        b"data: one\r\ndata: line2\n\n",
        b"data: one\r\r\ndata: two\n\r",
    ],
    "markdown_markers": [
        "- literal footnote [^1] in body",
        "- multiple [^7], [^8] markers",
        "- escaped \\[^1] token",
    ],
    "container_strings": [
        "[1, 2, 3]",
        '{"a": 1}',
        "null",
        "true",
    ],
    "diff_headers": [
        "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,2 @@\n--- literal line\n+++ another",
    ],
}

DESERIALIZER_NAME_HINTS = ("from_dict", "deserialize", "loads", "load_", "parse")
FENCE_STRIP_METHODS = frozenset({"strip", "lstrip", "rstrip"})
FOOTNOTE_REGEX_HINT_RE = re.compile(r"\\\[\\\^")
HARDCODED_TRIPLE_BACKTICK_RE = re.compile(r"```")


def _enclosing_symbols(tree: ast.Module) -> dict[int, str]:
    """Map line number -> enclosing function/method name."""
    line_owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            end = getattr(node, "end_lineno", node.lineno)
            for ln in range(node.lineno, end + 1):
                line_owner[ln] = node.name
    return line_owner


def _scan_naive_fence_stripping(
    rel: str,
    tree: ast.Module,
    owners: dict[int, str],
) -> list[Candidate]:
    """Rule 2A: Flag ``.strip('`')`` and hardcoded literal ````` ``` ````` fence regexes."""
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # 1. .strip("`") / .lstrip("`") / .rstrip("`") / .strip("```") / .strip("~")
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in FENCE_STRIP_METHODS
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            arg_val = node.args[0].value
            receiver = ast.unparse(node.func.value).lower()
            # .strip("`") on a filename is normalizing a path, not removing a fence.
            if arg_val and set(arg_val) <= {"`", "~"} and not any(
                token in receiver for token in ("file", "path", "filename", "repo", "branch")
            ) and receiver.strip() not in {"fence", "line", "stripped"}:
                candidates.append(
                    Candidate(
                        oracle="parser_meta",
                        family="parser-metadata",
                        file=rel,
                        line=node.lineno,
                        symbol=owners.get(node.lineno),
                        detail=(
                            f"calls .{node.func.attr}({arg_val!r}), which strips individual characters "
                            "and mangles trailing inline code spans instead of removing a balanced fence"
                        ),
                        confidence="high",
                        evidence={"rule": "naive-fence-strip", "method": node.func.attr, "chars": arg_val},
                        playbook_id="parser-metadata",
                    )
                )
                continue

        # 2. re.compile(...) / re.sub(...) with hardcoded literal ``` and no backreference
        func_name = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if func_name not in {"compile", "sub", "match", "search"}:
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        pat = node.args[0].value
        fence_literal = "```" if "```" in pat else "~~~" if "~~~" in pat else None
        if fence_literal is None:
            continue
        # Ignore language-specific info-string opening matchers like ```mermaid without a closing fence
        if re.search(rf"{re.escape(fence_literal)}[A-Za-z]+", pat) and r"\Z" not in pat and pat.count(fence_literal) == 1:
            continue
        # Look for wrapping-fence or line-fence patterns that hardcode ``` without backreferences
        has_anchor_or_fence = any(tok in pat for tok in (r"\A", r"\Z", "^", "$"))
        has_backref = any(tok in pat for tok in (r"\1", r"\2", "(?P="))
        if has_anchor_or_fence and not has_backref:
            candidates.append(
                Candidate(
                    oracle="parser_meta",
                    family="parser-metadata",
                    file=rel,
                    line=node.lineno,
                    symbol=owners.get(node.lineno),
                    detail=(
                        f"regex matches literal {fence_literal} code fence without length/character backreference; "
                        "breaks on nested/longer or mixed-character CommonMark fences"
                    ),
                    confidence="high",
                    evidence={
                        "rule": "hardcoded-triple-fence-regex",
                        "pattern": pat[:120],
                        "fence": "tilde" if fence_literal == "~~~" else "backtick",
                    },
                    playbook_id="parser-metadata",
                )
            )
    return candidates


def _literal_fence_run(value: str) -> str | None:
    """Return the fence character if *value* starts with a run of 3+ backticks/tildes."""
    if not value:
        return None
    char = value[0]
    if char not in {"`", "~"}:
        return None
    run = len(value) - len(value.lstrip(char))
    return char if run >= 3 else None


def _yaml_fence_context(symbol: str | None) -> bool:
    low = (symbol or "").lower()
    return any(token in low for token in ("yaml", "yml", "fence"))


def _scan_literal_fence_checks(
    rel: str,
    source: str,
    tree: ast.Module,
    owners: dict[int, str],
) -> list[Candidate]:
    """Flag literal `.startswith('```yaml')` / `== '```'` fence checks in YAML/fence helpers."""
    candidates: list[Candidate] = []
    segments = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call | ast.Compare):
            continue
        symbol = owners.get(getattr(node, "lineno", 0))
        if not _yaml_fence_context(symbol):
            continue
        literals: list[str] = []
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"startswith", "endswith"}
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            literals.append(node.args[0].value)
        elif isinstance(node, ast.Compare):
            literals.extend(
                value.value
                for value in [node.left, *node.comparators]
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
        if "`" in segments.get(symbol or "", "") and "~" in segments.get(symbol or "", ""):
            continue
        for literal in literals:
            char = _literal_fence_run(literal)
            if char is None:
                continue
            candidates.append(
                Candidate(
                    oracle="parser_meta",
                    family="parser-metadata",
                    file=rel,
                    line=node.lineno,
                    symbol=symbol,
                    detail=(
                        f"checks a literal {literal!r} fence without accepting the other fence character "
                        "or a longer run; a CommonMark fence of a different character/length is missed"
                    ),
                    confidence="high",
                    evidence={
                        "rule": "literal-fence-check",
                        "literal": literal[:40],
                        "fence": "tilde" if char == "~" else "backtick",
                    },
                    playbook_id="parser-metadata",
                )
            )
    return candidates


def _scan_incomplete_event_terminators(
    rel: str,
    tree: ast.Module,
    owners: dict[int, str],
) -> list[Candidate]:
    """Rule 2B: Flag terminator collections having LF/CRLF pairs while missing CR-only or mixed pairs."""
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple | ast.List | ast.Set):
            continue
        elts = [
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, (bytes, str))
        ]
        if len(elts) < 2 or len(elts) != len(node.elts):
            continue
        is_bytes = all(isinstance(x, bytes) for x in elts)
        is_str = all(isinstance(x, str) for x in elts)
        if not (is_bytes or is_str):
            continue

        if is_bytes:
            vals = set(elts)
            has_lf_crlf = b"\n\n" in vals and b"\r\n\r\n" in vals
            has_cr_cr = b"\r\r" in vals
            has_mixed = b"\r\n\n" in vals or b"\n\r\n" in vals
        else:
            vals = set(elts)
            has_lf_crlf = "\n\n" in vals and "\r\n\r\n" in vals
            has_cr_cr = "\r\r" in vals
            has_mixed = "\r\n\n" in vals or "\n\r\n" in vals

        if has_lf_crlf and (not has_cr_cr or not has_mixed):
            missing = []
            if not has_cr_cr:
                missing.append("CR-only (\\r\\r)")
            if not has_mixed:
                missing.append("mixed (\\r\\n\\n / \\n\\r\\n)")
            candidates.append(
                Candidate(
                    oracle="parser_meta",
                    family="parser-metadata",
                    file=rel,
                    line=node.lineno,
                    symbol=owners.get(node.lineno),
                    detail=(
                        "event/line-ending terminator set supports LF and CRLF pairs but omits "
                        + " and ".join(missing)
                    ),
                    confidence="high",
                    evidence={
                        "rule": "incomplete-event-terminators",
                        "missing": missing,
                        "count": len(vals),
                    },
                    playbook_id="parser-metadata",
                )
            )
    return candidates


def _scan_unescaped_marker_roundtrip(
    rel: str,
    source: str,
    tree: ast.Module,
    owners: dict[int, str],
) -> list[Candidate]:
    """Rule 2C: Flag unescaped Markdown/footnote marker regexes in parse+serialize modules."""
    fn_names = [
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    has_parse = any(n.startswith(("parse", "load", "decode")) for n in fn_names)
    has_serialize = any(n.startswith(("serialize", "dump", "encode")) for n in fn_names)
    if not (has_parse and has_serialize):
        return []

    has_escape_handling = (
        "(?<!\\\\)" in source
        or "\\\\[^" in source
        or "escape" in source.lower()
        and "\\[" in source
    )
    if "(?<!\\\\)" in source:
        return []

    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_re_compile = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "compile"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
        )
        if not is_re_compile or not node.args:
            continue
        pat = "".join(
            sub.value
            for sub in ast.walk(node.args[0])
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        ) or ast.unparse(node.args[0])
        if r"\[\^" in pat or r"\[^" in pat or "[^" in pat:
            # Skip character-class negations like [^\]]; target Markdown footnote token [^...]
            if r"\[\^" not in pat and r"\[^" not in pat:
                continue
            if r"(?<!\\)" not in pat and not has_escape_handling:
                candidates.append(
                    Candidate(
                        oracle="parser_meta",
                        family="parser-metadata",
                        file=rel,
                        line=node.lineno,
                        symbol=owners.get(node.lineno) or "parse",
                        detail=(
                            "parse+serialize module compiles unescaped footnote/marker regex without "
                            "escape lookbehind (?<!\\\\); literal marker tokens in body text collide "
                            "with structural markers on round-trip"
                        ),
                        confidence="high",
                        evidence={"rule": "unescaped-marker-regex", "pattern": pat[:120]},
                        playbook_id="parser-metadata",
                    )
                )
    return candidates


def _scan_unsafe_dict_get_items(
    rel: str,
    tree: ast.Module,
) -> list[Candidate]:
    """Rule 2D: Flag ``x.get(key, {}).items()`` in deserializers when ``x[key]`` may be non-mapping."""
    candidates: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        lname = node.name.lower()
        if not any(hint in lname for hint in DESERIALIZER_NAME_HINTS):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            if not isinstance(sub.func, ast.Attribute) or sub.func.attr not in {"items", "keys", "values"}:
                continue
            inner = sub.func.value
            if not isinstance(inner, ast.Call):
                continue
            if not isinstance(inner.func, ast.Attribute) or inner.func.attr != "get":
                continue
            if len(inner.args) < 2:
                continue
            default_arg = inner.args[1]
            if isinstance(default_arg, ast.Dict) and not default_arg.keys:
                key_repr = ast.unparse(inner.args[0])
                candidates.append(
                    Candidate(
                        oracle="parser_meta",
                        family="deserialization-guard",
                        file=rel,
                        line=sub.lineno,
                        symbol=node.name,
                        detail=(
                            f"calls .get({key_repr}, {{}}).{sub.func.attr}() in {node.name} without "
                            "validating that the present value is a mapping (null/list/scalar raises AttributeError)"
                        ),
                        confidence="high",
                        evidence={
                            "rule": "unsafe-dict-get-items",
                            "key": key_repr,
                            "method": sub.func.attr,
                        },
                        playbook_id="parser-metadata",
                    )
                )
    return candidates


def scan_parser_meta(root: str | Path) -> list[Candidate]:
    """Run all parser metadata-as-data, terminator, and deserialization guard checks."""
    target = Path(root).resolve()
    if not target.exists():
        raise FileNotFoundError(f"target path does not exist: {target}")

    candidates: list[Candidate] = []
    for file in iter_python_files(target):
        try:
            source = file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        rel = rel_posix(file, target)
        owners = _enclosing_symbols(tree)
        candidates.extend(_scan_naive_fence_stripping(rel, tree, owners))
        candidates.extend(_scan_literal_fence_checks(rel, source, tree, owners))
        candidates.extend(_scan_incomplete_event_terminators(rel, tree, owners))
        candidates.extend(_scan_unescaped_marker_roundtrip(rel, source, tree, owners))
        candidates.extend(_scan_unsafe_dict_get_items(rel, tree))

    seen: set[tuple] = set()
    unique: list[Candidate] = []
    for cand in candidates:
        k = (cand.file, cand.line, cand.symbol, cand.detail)
        if k not in seen:
            seen.add(k)
            unique.append(cand)
    return unique


def run_parser_meta_scan(
    run_name: str,
    target_path: str | Path,
    *,
    runs_dir: str | Path | None = None,
    register_obligations: bool = False,
) -> dict[str, Any]:
    """Execute ``scan_parser_meta`` and persist ``parser-meta-results.json``."""
    from bug_vet import ledger as ledger_mod

    cands = scan_parser_meta(target_path)
    run_path = ensure_run(run_name, runs_dir=runs_dir)
    if register_obligations:
        for idx, cand in enumerate(cands, start=1):
            slug = ledger_mod.digest(cand.file, cand.line, cand.symbol, cand.family)[:8]
            ledger_mod.add_obligation(
                run_path,
                {
                    "id": f"pm-{slug}-{idx}",
                    "origin": "oracle:parser_meta",
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
        "oracle": "parser_meta",
        "count": len(cands),
        "candidates": [c.to_dict() for c in cands],
    }
    artifact = run_path / "parser-meta-results.json"
    artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload["artifact"] = str(artifact)
    return payload
