from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bug_vet import collide


def _bin_path(name: str) -> str:
    scripts_dir = Path(sys.executable).parent
    exe = scripts_dir / f"{name}.exe"
    if exe.exists():
        return str(exe)
    plain = scripts_dir / name
    if plain.exists():
        return str(plain)
    raise FileNotFoundError(f"CLI binary {name!r} not found under {scripts_dir}")


def test_cli_help_and_alias() -> None:
    for cmd in ("bug-vet", "bugvet"):
        proc = subprocess.run(
            [_bin_path(cmd), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        assert proc.returncode == 0
        assert "setup" in proc.stdout
        assert "mcp" in proc.stdout
        assert "falsify" in proc.stdout


def test_setup_preview_and_safe_project_apply(tmp_path: Path) -> None:
    # Preview does not modify disk
    preview = subprocess.run(
        [_bin_path("bug-vet"), "setup", "--client", "all", "--json"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert preview.returncode == 0
    data = json.loads(preview.stdout)
    assert data["apply"] is False
    assert set(data["configs"]) == {"claude", "cursor", "codex", "dsh", "antigravity"}
    assert not (tmp_path / ".mcp.json").exists()

    # Apply merges safely and preserves existing mcpServers
    existing = {"mcpServers": {"other": {"command": "other-tool", "args": []}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(existing), encoding="utf-8")

    applied = subprocess.run(
        [_bin_path("bug-vet"), "setup", "--client", "all", "--apply", "--project-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert applied.returncode == 0
    assert (tmp_path / ".mcp.json.bak").exists()
    merged = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "other" in merged["mcpServers"]
    assert merged["mcpServers"]["bug-vet"]["args"] == ["mcp"]
    cursor_cfg = json.loads((tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert cursor_cfg["mcpServers"]["bug-vet"]["command"] == "bug-vet"
    assert (tmp_path / ".agents" / "plugins" / "bug-vet" / "plugin.json").is_file()


def _run_hook(hook_cmd: str, cwd: Path, payload: dict, *, extra_env: dict[str, str] | None = None) -> dict:
    shell_cmd = ["cmd", "/c", hook_cmd] if os.name == "nt" else ["sh", "-c", hook_cmd]
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        shell_cmd,
        cwd=str(cwd),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_setup_antigravity_plugin_bundle_and_hooks(tmp_path: Path) -> None:
    plugin_dest = tmp_path / "plugins" / "bug-vet"
    proc = subprocess.run(
        [
            _bin_path("bug-vet"),
            "setup",
            "--client",
            "antigravity",
            "--apply",
            "--plugin-dir",
            str(plugin_dest),
            "--command-bin",
            _bin_path("bug-vet"),
            "--python-bin",
            sys.executable,
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["apply"] is True
    assert Path(res["applied"][0]["file"]) == plugin_dest.resolve()

    manifest = json.loads((plugin_dest / "plugin.json").read_text(encoding="utf-8"))
    allowed_keys = {
        "name",
        "displayName",
        "version",
        "description",
        "logo",
        "author",
        "homepage",
        "repository",
        "suggestedPrompts",
        "disabled",
    }
    assert manifest["name"] == "bug-vet"
    assert set(manifest).issubset(allowed_keys)
    assert len(manifest["suggestedPrompts"]) == 3
    assert (plugin_dest / manifest["logo"]).is_file()

    mcp_cfg = json.loads((plugin_dest / "mcp_config.json").read_text(encoding="utf-8"))
    assert "bug-vet-mcp" in mcp_cfg["mcpServers"]
    assert mcp_cfg["mcpServers"]["bug-vet-mcp"]["args"] == ["mcp"]

    for skill in ("bug-vet-hunt", "bug-vet-verify", "bug-vet-bench"):
        md = (plugin_dest / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {skill}" in md

    for agent_name in ("bug-vet-hunter", "bug-vet-falsifier"):
        md = (plugin_dest / "agents" / f"{agent_name}.md").read_text(encoding="utf-8")
        assert "subagent: true" in md
        assert "mainAgent: false" in md
        assert "tools:" in md

    rules_md = (plugin_dest / "rules" / "AGENTS.md").read_text(encoding="utf-8")
    assert not rules_md.startswith("---")

    hooks_cfg = json.loads((plugin_dest / "hooks.json").read_text(encoding="utf-8"))
    local_cmd = hooks_cfg["bug-vet-local-guard"]["PreToolUse"][0]["hooks"][0]["command"]
    vac_cmd = hooks_cfg["bug-vet-probe-vacuity-guard"]["PreToolUse"][0]["hooks"][0]["command"]

    assert _run_hook(
        local_cmd, plugin_dest, {"toolCall": {"name": "run_command", "args": {"CommandLine": "git push origin main"}}}
    )["decision"] == "force_ask"
    assert _run_hook(
        local_cmd, plugin_dest, {"toolCall": {"name": "run_command", "args": {"CommandLine": "gh pr create --fill"}}}
    )["decision"] == "force_ask"
    assert _run_hook(
        local_cmd, plugin_dest, {"toolCall": {"name": "run_command", "args": {"CommandLine": "pytest -v"}}}
    )["decision"] == "allow"
    assert _run_hook(
        local_cmd,
        plugin_dest,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "git push origin main"}}},
        extra_env={"BUG_VET_ALLOW_REMOTE": "1"},
    )["decision"] == "allow"

    assert _run_hook(
        vac_cmd,
        plugin_dest,
        {"toolCall": {"name": "write_file", "args": {"Path": "/tmp/run/probes/p1.py", "Content": "def check(failures):\n    pass\n"}}},
    )["decision"] == "deny"
    assert _run_hook(
        vac_cmd,
        plugin_dest,
        {"toolCall": {"name": "write_file", "args": {"Path": "/tmp/run/probes/p2.py", "Content": "def self_check():\n    return []\n\ndef check(failures):\n    pass\n"}}},
    )["decision"] == "deny"
    assert _run_hook(
        vac_cmd,
        plugin_dest,
        {
            "toolCall": {
                "name": "write_file",
                "args": {
                    "Path": "/tmp/run/probes/p3.py",
                    "Content": "def self_check():\n    if 1 != 1:\n        return ['x']\n    return []\n\ndef check(failures):\n    failures.append('bad')\n",
                },
            }
        },
    )["decision"] == "allow"
    assert _run_hook(
        vac_cmd,
        plugin_dest,
        {"toolCall": {"name": "write_file", "args": {"Path": "/tmp/other/helper.py", "Content": "x = 1\n"}}},
    )["decision"] == "allow"


def test_cli_end_to_end_with_runs_dir_before_and_after_subcommand(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from pkg.core import serialize\n", encoding="utf-8")
    (pkg / "core.py").write_text(
        'def serialize(x):\n    """Lossless serialization."""\n    return x\n',
        encoding="utf-8",
    )

    cli = _bin_path("bug-vet")
    # --runs-dir after subcommand
    r1 = subprocess.run(
        [cli, "scan", str(pkg), "--name", "r1", "--mode", "claims", "--runs-dir", str(runs_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert r1.returncode == 0
    assert (runs_dir / "r1" / "claims.json").exists()

    # --runs-dir before subcommand
    r2 = subprocess.run(
        [cli, "--runs-dir", str(runs_dir), "status", "r1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert r2.returncode == 0
    assert "ledger.json" in r2.stdout

    # falsify + gate via CLI
    r3 = subprocess.run(
        [
            cli,
            "falsify",
            "r1",
            "--runs-dir",
            str(runs_dir),
            "--obligation",
            "ob-c-0001",
            "--reason",
            "trivial identity",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert r3.returncode == 0

    r4 = subprocess.run(
        [cli, "gate", "r1", "--runs-dir", str(runs_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert r4.returncode == 0
    assert "READY" in r4.stdout


def test_cli_ledger_promote_and_manual_add(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    cli = _bin_path("bug-vet")

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "seq.py").write_text(
        "from collections.abc import Sequence\n"
        "def take(items: Sequence[str]):\n"
        "    if isinstance(items, list):\n"
        "        return list(items)\n"
        "    raise TypeError('list only')\n",
        encoding="utf-8",
    )

    scan = subprocess.run(
        [cli, "scan", str(repo), "--name", "prom", "--mode", "all", "--runs-dir", str(runs_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert scan.returncode == 0

    prom = subprocess.run(
        [cli, "ledger", "prom", "--runs-dir", str(runs_dir), "--promote", "1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert prom.returncode == 0
    assert "promoted ob-cand-1-" in prom.stdout

    added = subprocess.run(
        [
            cli,
            "ledger",
            "prom",
            "--runs-dir",
            str(runs_dir),
            "--add",
            "manual boundary promise",
            "--id",
            "man-1",
            "--source",
            str(repo / "seq.py"),
            "--line",
            "2",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert added.returncode == 0
    assert "added ob-man-1" in added.stdout
    state = json.loads((runs_dir / "prom" / "ledger.json").read_text(encoding="utf-8"))
    assert len(state["obligations"]) == 2


def test_collide_handles_missing_gh_gracefully(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    try:
        collide.check_collision("owner/repo", issue=1, runs_dir=tmp_path)
    except RuntimeError as exc:
        assert "gh CLI" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when gh CLI is missing")
