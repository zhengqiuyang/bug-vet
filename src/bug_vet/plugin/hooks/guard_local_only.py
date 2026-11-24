"""PreToolUse hook: require explicit confirmation before remote push, PR, or publish commands."""

from __future__ import annotations

import json
import os
import re
import sys

# ``git -C <path> push``, ``git -c k=v push``, ``git --no-pager push`` all count.
GIT_PUSH_RE = re.compile(
    r"\bgit\b(?:\s+(?:-C\s+(?:\"[^\"]*\"|'[^']*'|\S+)|-c\s+\S+|--[\w-]+(?:=\S+)?|-[A-Za-z]))*\s+push\b",
    re.IGNORECASE,
)
GIT_SEND_EMAIL_RE = re.compile(r"\bgit\s+send-email\b", re.IGNORECASE)
GH_REMOTE_CREATE_RE = re.compile(r"\bgh\s+(?:pr|issue|release)\s+(?:create|new)\b", re.IGNORECASE)
HUB_PUSH_RE = re.compile(r"\bhub\s+push\b", re.IGNORECASE)
NODE_PUBLISH_RE = re.compile(r"\b(?:npm|pnpm|bun)\s+publish\b|\byarn\s+(?:npm\s+)?publish\b", re.IGNORECASE)
OTHER_PUBLISH_RE = re.compile(
    r"\b(?:cargo|poetry|uv)\s+publish\b"
    r"|\btwine\s+upload\b"
    r"|\bdotnet\s+nuget\s+push\b"
    r"|\bnuget\s+push\b",
    re.IGNORECASE,
)

REMOTE_WRITE_PATTERNS = (
    GIT_PUSH_RE,
    GIT_SEND_EMAIL_RE,
    GH_REMOTE_CREATE_RE,
    HUB_PUSH_RE,
    NODE_PUBLISH_RE,
    OTHER_PUBLISH_RE,
)

COMMAND_KEYS = ("CommandLine", "commandLine", "Command", "command", "cmd", "cmdLine")


def _extract_command(payload: dict) -> str:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return ""
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return ""
    for key in COMMAND_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def evaluate_command(command: str) -> dict[str, str]:
    if os.environ.get("BUG_VET_ALLOW_REMOTE") == "1":
        return {"decision": "allow"}
    for pattern in REMOTE_WRITE_PATTERNS:
        if command and pattern.search(command):
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
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
        cmd = _extract_command(payload)
        result = evaluate_command(cmd)
    except Exception as exc:  # noqa: BLE001 - fail closed on an unparsable command
        result = {
            "decision": "ask",
            "reason": f"bug-vet local-only guard could not parse the command ({exc}); confirm manually.",
        }
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
