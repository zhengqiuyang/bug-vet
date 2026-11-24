"""Safe agent onboarding configuration generator for Claude Code, Cursor, Codex, dsh, and Antigravity."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

SUPPORTED_CLIENTS = ("claude", "cursor", "codex", "dsh", "antigravity")


def _default_plugin_source() -> Path:
    candidate = Path(__file__).resolve().parents[2] / "plugin"
    if not candidate.is_dir():
        raise FileNotFoundError(f"canonical plugin bundle directory not found: {candidate}")
    return candidate


def _hook_python_prefix(python_bin: str) -> str:
    if " " not in python_bin:
        return python_bin
    if os.name == "nt":
        return f'call "{python_bin}"'
    return shlex.quote(python_bin)


def _render_hooks_config(python_bin: str) -> dict[str, Any]:
    prefix = _hook_python_prefix(python_bin)
    return {
        "bug-vet-local-guard": {
            "PreToolUse": [
                {
                    "matcher": "run_command",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{prefix} hooks/guard_local_only.py",
                            "timeout": 10,
                        }
                    ],
                }
            ]
        },
        "bug-vet-probe-vacuity-guard": {
            "PreToolUse": [
                {
                    "matcher": "write_file",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{prefix} hooks/guard_probe_vacuity.py",
                            "timeout": 10,
                        }
                    ],
                }
            ]
        },
    }


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
    antigravity_payload = {
        "mcpServers": {"bug-vet-mcp": mcp_entry},
        "pluginBundle": "plugin.json + skills/ (3) + agents/ (2) + rules/AGENTS.md + hooks.json (2)",
    }
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
        "antigravity": {
            "target": "~/.gemini/config/plugins/bug-vet/ (or <project>/.agents/plugins/bug-vet/)",
            "format": "json",
            "payload": antigravity_payload,
        },
    }


def install_antigravity_plugin(
    dest_dir: Path,
    *,
    command: str = "bug-vet",
    args: list[str] | None = None,
    python_bin: str | None = None,
    source_dir: Path | None = None,
) -> dict[str, str]:
    """Install the canonical Antigravity plugin bundle into ``dest_dir`` and render local paths."""
    src = Path(source_dir).expanduser().resolve() if source_dir else _default_plugin_source()
    dest = Path(dest_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    for rel_file in ("plugin.json", "README.md"):
        src_file = src / rel_file
        if src_file.is_file():
            shutil.copy2(src_file, dest / rel_file)

    for rel_dir in ("assets", "skills", "agents", "rules", "hooks"):
        src_sub = src / rel_dir
        if not src_sub.is_dir():
            continue
        dst_sub = dest / rel_dir
        if dst_sub.exists():
            shutil.rmtree(dst_sub)
        shutil.copytree(src_sub, dst_sub)

    cmd_args = list(args) if args is not None else ["mcp"]
    mcp_path = dest / "mcp_config.json"
    mcp_path.write_text(
        json.dumps({"mcpServers": {"bug-vet-mcp": {"command": command, "args": cmd_args}}}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    py = python_bin or sys.executable
    hooks_path = dest / "hooks.json"
    hooks_path.write_text(
        json.dumps(_render_hooks_config(py), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "file": str(dest),
        "backup": "",
        "mcp_config": str(mcp_path),
        "hooks": str(hooks_path),
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
    plugin_dir: str | Path | None = None,
    command: str = "bug-vet",
    args: list[str] | None = None,
    python_bin: str | None = None,
) -> dict[str, Any]:
    """Preview or safely apply project-local MCP configurations or Antigravity plugin bundles."""
    selected = list(SUPPORTED_CLIENTS) if client == "all" else [client]
    configs = generate_client_configs(command=command, args=args)
    filtered = {name: configs[name] for name in selected}

    applied: list[dict[str, str]] = []
    if apply:
        needs_project_dir = any(c in {"claude", "cursor"} for c in selected) or (
            "antigravity" in selected and plugin_dir is None
        )
        root: Path | None = None
        if project_dir is not None:
            root = Path(project_dir).expanduser().resolve()
            if not root.is_dir():
                raise NotADirectoryError(f"project_dir is not a directory: {root}")
        elif needs_project_dir:
            raise ValueError(
                "--apply requires --project-dir (or --plugin-dir when client is antigravity) "
                "so global user files are never silently altered"
            )

        entry = {"command": command, "args": list(args) if args is not None else ["mcp"]}
        if "claude" in selected and root is not None:
            applied.append(_merge_json_mcp(root / ".mcp.json", "bug-vet", entry))
        if "cursor" in selected and root is not None:
            applied.append(_merge_json_mcp(root / ".cursor" / "mcp.json", "bug-vet", entry))
        if "antigravity" in selected:
            target_plugin = (
                Path(plugin_dir).expanduser().resolve()
                if plugin_dir is not None
                else (root / ".agents" / "plugins" / "bug-vet")  # type: ignore[operator]
            )
            applied.append(
                install_antigravity_plugin(
                    target_plugin,
                    command=command,
                    args=args,
                    python_bin=python_bin,
                )
            )

    return {
        "client": client,
        "apply": apply,
        "configs": filtered,
        "applied": applied,
    }
