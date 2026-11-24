"""Safe agent onboarding configuration generator for Claude Code, Cursor, Codex, and dsh."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

SUPPORTED_CLIENTS = ("claude", "cursor", "codex", "dsh")


def generate_client_configs(command: str = "bug-vet", args: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Return client configuration snippets without modifying any host files."""
    cmd_args = list(args) if args is not None else ["mcp"]
    mcp_entry = {"command": command, "args": cmd_args}
    json_payload = {"mcpServers": {"bug-vet": mcp_entry}}
    toml_args = ", ".join(json.dumps(item) for item in cmd_args)
    toml_snippet = (
        "[mcp_servers.bug_vet]\n"
        f"command = {json.dumps(command)}\n"
        f"args = [{toml_args}]\n"
    )
    return {
        "claude": {
            "target": ".mcp.json (project) or ~/.claude.json (user)",
            "format": "json",
            "payload": json_payload,
        },
        "cursor": {
            "target": ".cursor/mcp.json (project) or ~/.cursor/mcp.json (user)",
            "format": "json",
            "payload": json_payload,
        },
        "codex": {
            "target": "~/.codex/config.toml",
            "format": "toml",
            "snippet": toml_snippet,
        },
        "dsh": {
            "target": "DeepSeek Harness MCP config",
            "format": "json",
            "payload": json_payload,
        },
    }


def _merge_json_mcp(target: Path, server_name: str, entry: dict[str, Any]) -> dict[str, str]:
    existing: dict[str, Any] = {}
    backup_path = ""
    if target.exists():
        backup = target.with_name(target.name + ".bak")
        shutil.copy2(target, backup)
        backup_path = str(backup)
        raw = target.read_text(encoding="utf-8", errors="replace").strip()
        if raw:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError(f"existing config {target} is not a JSON object")
            existing = parsed
    servers = existing.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise ValueError(f"{target} has non-object mcpServers")
    servers[server_name] = entry
    existing["mcpServers"] = servers
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"file": str(target), "backup": backup_path}


def run_setup(
    *,
    client: str = "all",
    apply: bool = False,
    project_dir: str | Path | None = None,
    command: str = "bug-vet",
    args: list[str] | None = None,
) -> dict[str, Any]:
    """Preview or safely apply project-local MCP configurations."""
    selected = list(SUPPORTED_CLIENTS) if client == "all" else [client]
    configs = generate_client_configs(command=command, args=args)
    filtered = {name: configs[name] for name in selected}

    applied: list[dict[str, str]] = []
    if apply:
        if project_dir is None:
            raise ValueError("--apply requires --project-dir so global user files are never silently altered")
        root = Path(project_dir).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"project_dir is not a directory: {root}")
        entry = {"command": command, "args": list(args) if args is not None else ["mcp"]}
        if "claude" in selected:
            applied.append(_merge_json_mcp(root / ".mcp.json", "bug-vet", entry))
        if "cursor" in selected:
            applied.append(_merge_json_mcp(root / ".cursor" / "mcp.json", "bug-vet", entry))

    return {
        "client": client,
        "apply": apply,
        "configs": filtered,
        "applied": applied,
    }
