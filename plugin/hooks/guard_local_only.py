"""PreToolUse hook: require explicit confirmation before remote push, PR, or publish commands."""

from __future__ import annotations

import json
import os
import re
import sys

REMOTE_WRITE_RE = re.compile(
    r"(?:\bgit\s+(?:-[^\s]+\s+)*push\b|\bgh\s+(?:pr|issue)\s+create\b|\btwine\s+upload\b|\buv\s+publish\b)",
    re.IGNORECASE,
)


def _extract_command(payload: dict) -> str:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return ""
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return ""
    for key in ("CommandLine", "commandLine", "command", "cmd"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def evaluate_command(command: str) -> dict[str, str]:
    if os.environ.get("BUG_VET_ALLOW_REMOTE") == "1":
        return {"decision": "allow"}
    if command and REMOTE_WRITE_RE.search(command):
        return {
            "decision": "force_ask",
            "reason": (
                "bug-vet local-only guard: detected remote push / PR / package publish command. "
                "Confirm explicitly or set BUG_VET_ALLOW_REMOTE=1."
            ),
        }
    return {"decision": "allow"}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace").strip()
        payload = json.loads(raw) if raw else {}
        cmd = _extract_command(payload) if isinstance(payload, dict) else ""
        result = evaluate_command(cmd)
    except Exception:
        result = {"decision": "allow"}
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
