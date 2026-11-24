"""PreToolUse hook: block vacuous or missing-self_check probes written to probes/*.py."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

WRITE_TOOLS = {"write_file", "edit_file", "multi_edit_file", "replace_file_content"}
CONTENT_KEYS = ("Content", "CodeContent", "content")
PATCH_KEYS = ("Replacement", "CodeEdit", "replacement")
TARGET_KEYS = ("Target", "target", "OldContent", "old_content")
PATH_KEYS = ("Path", "TargetFile", "path", "file_path")
APPEND_KEYS = ("Append", "append")

SELF_CHECK_SIGNALS = (
    ast.Assert,
    ast.Raise,
    ast.If,
    ast.IfExp,
    ast.While,
    ast.Try,
    ast.Call,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Await,
)


def _load_audit_fn():
    try:
        from bug_vet.falsifier import audit_source_vacuity

        return audit_source_vacuity
    except Exception:
        pass
    hook = Path(__file__).resolve()
    try:
        candidates = (hook.parents[3] / "src", hook.parents[2] / "src")
    except IndexError:
        candidates = ()
    for candidate in candidates:
        if (candidate / "bug_vet" / "falsifier.py").is_file():
            sys.path.insert(0, str(candidate))
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


def _self_check_has_signal(node: ast.AST) -> bool:
    return any(isinstance(sub, SELF_CHECK_SIGNALS) for sub in ast.walk(node))


def _fallback_vacuity(tree: ast.Module) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name == "self_check":
            if not _self_check_has_signal(node):
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


def _first_str(args: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _clean_path(path_str: str) -> str:
    return path_str.strip().strip('"').strip("'")


def _is_probe_path(path_str: str | None) -> bool:
    if not path_str:
        return False
    target = Path(_clean_path(path_str))
    if target.suffix.lower() != ".py":
        return False
    return any(part.lower() == "probes" for part in target.parts[:-1])


def _is_append(args: dict[str, Any]) -> bool:
    for key in APPEND_KEYS:
        val = args.get(key)
        if val is True:
            return True
        if isinstance(val, str) and val.strip().lower() in {"1", "true", "yes"}:
            return True
    return False


def _read_existing(path_str: str) -> str | None:
    try:
        return Path(_clean_path(path_str)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _compose_source(args: dict[str, Any], path_str: str) -> tuple[str | None, str]:
    """Reconstruct the probe body a write/edit would leave on disk."""
    existing = _read_existing(path_str)
    content = _first_str(args, CONTENT_KEYS)
    patch = _first_str(args, PATCH_KEYS)

    if _is_append(args):
        if content is None:
            return None, "append call carries no content"
        if existing is None:
            return None, "cannot read the existing probe before appending"
        return existing + "\n" + content, ""

    if patch is not None:
        if existing is None:
            return patch, ""
        target = _first_str(args, TARGET_KEYS)
        if target and target in existing:
            return existing.replace(target, patch, 1), ""
        return existing + "\n" + patch, ""

    if content is not None:
        return content, ""

    return None, "no writable content in tool arguments"


def evaluate_write_args(args: dict[str, Any]) -> dict[str, str]:
    path_str = _first_str(args, PATH_KEYS)
    if not _is_probe_path(path_str):
        return {"decision": "allow"}
    if path_str is None:  # defensive: _is_probe_path already guarantees non-None
        return {"decision": "allow"}

    name = Path(_clean_path(path_str)).name
    source, problem = _compose_source(args, path_str)
    if source is None:
        return {
            "decision": "force_ask",
            "reason": f"bug-vet hook cannot inspect probe {name}: {problem}. Confirm the write manually.",
        }

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "decision": "deny",
            "reason": f"bug-vet hook: probe {name} has SyntaxError at line {exc.lineno}: {exc.msg}",
        }

    if not _has_top_level_self_check(tree):
        if _first_str(args, PATCH_KEYS) is not None:
            return {
                "decision": "force_ask",
                "reason": f"bug-vet hook: edited fragment of {name} does not rebuild a top-level self_check(); confirm manually.",
            }
        return {
            "decision": "deny",
            "reason": f"bug-vet hook: probe {name} must define a top-level self_check() function.",
        }

    audit_fn = _load_audit_fn()
    if audit_fn is not None:
        try:
            res = audit_fn(source, filename=str(path_str))
        except Exception as exc:  # noqa: BLE001 - fail closed on an unauditable probe
            return {
                "decision": "force_ask",
                "reason": f"bug-vet hook could not audit probe {name}: {exc}. Confirm the write manually.",
            }
        issues = res.get("findings", [])
    else:
        issues = _fallback_vacuity(tree)

    if issues:
        kinds = ", ".join(sorted({str(item.get("kind", "vacuous")) for item in issues}))
        return {
            "decision": "deny",
            "reason": f"bug-vet hook rejected vacuous probe {name} ({kinds}).",
        }
    return {"decision": "allow"}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = None

    result: dict[str, str]
    tool_call = payload.get("toolCall") if isinstance(payload, dict) else None
    args = tool_call.get("args") if isinstance(tool_call, dict) else None
    tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
    if not isinstance(args, dict) or (isinstance(tool_name, str) and tool_name not in WRITE_TOOLS):
        result = {"decision": "allow"}
    else:
        try:
            result = evaluate_write_args(args)
        except Exception as exc:  # noqa: BLE001 - never silently allow an uninspected probe
            if _is_probe_path(_first_str(args, PATH_KEYS)):
                result = {
                    "decision": "force_ask",
                    "reason": f"bug-vet probe guard crashed while inspecting a probe write: {exc}. Confirm manually.",
                }
            else:
                result = {"decision": "allow"}

    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
