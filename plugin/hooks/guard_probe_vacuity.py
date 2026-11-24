"""PreToolUse hook: block vacuous or missing-self_check probes written to probes/*.py."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


def _load_audit_fn():
    try:
        from bug_vet.falsifier import audit_source_vacuity

        return audit_source_vacuity
    except Exception:
        repo_src = Path(__file__).resolve().parents[2] / "src"
        if repo_src.is_dir():
            sys.path.insert(0, str(repo_src))
            try:
                from bug_vet.falsifier import audit_source_vacuity

                return audit_source_vacuity
            except Exception:
                return None
    return None


def _has_top_level_self_check(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "self_check"
        for node in tree.body
    )


def _fallback_vacuity(tree: ast.Module) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name == "self_check":
            if not any(isinstance(sub, (ast.If, ast.Assert, ast.Raise)) for sub in ast.walk(node)):
                findings.append({"kind": "empty-self-check", "function": "self_check", "line": node.lineno})
        elif node.name == "check":
            has_record = any(
                isinstance(sub, (ast.Assert, ast.Raise))
                or (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in {"append", "extend"}
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "failures"
                )
                or (
                    isinstance(sub, ast.AugAssign)
                    and isinstance(sub.op, ast.Add)
                    and isinstance(sub.target, ast.Name)
                    and sub.target.id == "failures"
                )
                for sub in ast.walk(node)
            )
            if not has_record:
                findings.append({"kind": "no-assertions", "function": "check", "line": node.lineno})
    return findings


def evaluate_write_args(args: dict[str, Any]) -> dict[str, str]:
    path_str = ""
    for key in ("Path", "TargetFile", "path", "file_path"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            path_str = val.strip()
            break
    if not path_str:
        return {"decision": "allow"}
    if bool(args.get("Append") or args.get("append")):
        return {"decision": "allow"}

    target = Path(path_str)
    if target.suffix.lower() != ".py" or target.parent.name.lower() != "probes":
        return {"decision": "allow"}

    content = None
    for key in ("Content", "CodeContent", "content"):
        val = args.get(key)
        if isinstance(val, str):
            content = val
            break
    if not content:
        return {"decision": "allow"}

    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return {
            "decision": "deny",
            "reason": f"bug-vet hook: probe {target.name} has SyntaxError at line {exc.lineno}: {exc.msg}",
        }

    if not _has_top_level_self_check(tree):
        return {
            "decision": "deny",
            "reason": f"bug-vet hook: probe {target.name} must define a top-level self_check() function.",
        }

    audit_fn = _load_audit_fn()
    if audit_fn is not None:
        res = audit_fn(content, filename=str(target))
        issues = res.get("findings", [])
    else:
        issues = _fallback_vacuity(tree)

    if issues:
        kinds = ", ".join(sorted({str(item.get("kind", "vacuous")) for item in issues}))
        return {
            "decision": "deny",
            "reason": f"bug-vet hook rejected vacuous probe {target.name} ({kinds}).",
        }
    return {"decision": "allow"}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
        payload = json.loads(raw) if raw else {}
        tool_call = payload.get("toolCall") if isinstance(payload, dict) else None
        args = tool_call.get("args") if isinstance(tool_call, dict) else None
        result = evaluate_write_args(args) if isinstance(args, dict) else {"decision": "allow"}
    except Exception:
        result = {"decision": "allow"}
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
