from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _send(proc: subprocess.Popen[bytes], payload: dict) -> None:
    line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    assert proc.stdin is not None
    proc.stdin.write(line)
    proc.stdin.flush()


def _recv(proc: subprocess.Popen[bytes]) -> dict:
    assert proc.stdout is not None
    raw = proc.stdout.readline()
    assert raw, "MCP server closed stdout unexpectedly"
    return json.loads(raw.decode("utf-8"))


def test_mcp_stdio_end_to_end_workflow(tmp_path: Path) -> None:
    repo = tmp_path / "sample_repo"
    pkg = repo / "samplepkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from samplepkg.codec import encode_item, decode_item\n", encoding="utf-8")
    codec = pkg / "codec.py"
    codec.write_text(
        "def encode_item(text: str) -> str:\n"
        "    '''Lossless round-trip encoder for 中文和 ASCII 文本.'''\n"
        "    return 'ENC:' + text\n\n"
        "def decode_item(raw: str) -> str:\n"
        "    # intentional bug: truncates output\n"
        "    body = raw[4:] if raw.startswith('ENC:') else raw\n"
        "    return body[:4] if len(body) > 15 else body\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("from samplepkg import encode_item\n", encoding="utf-8")

    runs_dir = tmp_path / "mcp_runs"

    proc = subprocess.Popen(
        [sys.executable, "-m", "bug_vet", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmp_path),
    )
    try:
        # 1. Initialize handshake
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "pytest"}},
            },
        )
        init_resp = _recv(proc)
        assert init_resp["id"] == 1
        assert init_resp["result"]["serverInfo"]["name"] == "bug-vet"
        assert "tools" in init_resp["result"]["capabilities"]

        # 2. Notification produces no response; ping proves ordering
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
        ping_resp = _recv(proc)
        assert ping_resp["id"] == 2
        assert ping_resp["result"] == {}

        # 3. tools/list
        _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        list_resp = _recv(proc)
        names = {tool["name"] for tool in list_resp["result"]["tools"]}
        assert names == {
            "bv_scan",
            "bv_probe",
            "bv_shrink",
            "bv_verify",
            "bv_ledger",
            "bv_gate",
            "bv_playbook",
            "bv_falsify",
            "bv_report",
        }

        # 4. bv_scan (roundtrip)
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "bv_scan",
                    "arguments": {
                        "mode": "roundtrip",
                        "run": "demo",
                        "path": str(pkg),
                        "import_root": str(repo),
                        "runs_dir": str(runs_dir),
                    },
                },
            },
        )
        scan_resp = _recv(proc)
        assert scan_resp["result"]["isError"] is False
        scan_data = json.loads(scan_resp["result"]["content"][0]["text"])
        assert scan_data["count"] == 1

        # 5. bv_probe (run) catches bug
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "bv_probe",
                    "arguments": {
                        "action": "run",
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "python": sys.executable,
                        "cwd": str(repo),
                        "env": [f"PYTHONPATH={repo}"],
                    },
                },
            },
        )
        probe_resp = _recv(proc)
        assert probe_resp["result"]["isError"] is False
        probe_data = json.loads(probe_resp["result"]["content"][0]["text"])
        assert probe_data["failures"] == 1

        # 6. bv_verify baseline -> patch -> verify
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "bv_verify",
                    "arguments": {
                        "mode": "baseline",
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "python": sys.executable,
                        "cwd": str(repo),
                        "env": [f"PYTHONPATH={repo}"],
                    },
                },
            },
        )
        base_resp = _recv(proc)
        assert base_resp["result"]["isError"] is False

        codec.write_text(
            "def encode_item(text: str) -> str:\n"
            "    return 'ENC:' + text\n\n"
            "def decode_item(raw: str) -> str:\n"
            "    return raw[4:] if raw.startswith('ENC:') else raw\n",
            encoding="utf-8",
        )

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "bv_verify",
                    "arguments": {
                        "mode": "verify",
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "python": sys.executable,
                        "cwd": str(repo),
                        "env": [f"PYTHONPATH={repo}"],
                    },
                },
            },
        )
        ver_resp = _recv(proc)
        assert ver_resp["result"]["isError"] is False
        ver_data = json.loads(ver_resp["result"]["content"][0]["text"])
        assert ver_data["ok"] is True

        # 7. bv_ledger add_obligation + falsify + show
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "bv_ledger",
                    "arguments": {
                        "action": "add_obligation",
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "claim": {"id": "c-hyp", "text": "suspected env bug", "source": "codec.py", "line": 1},
                    },
                },
            },
        )
        _recv(proc)

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "bv_ledger",
                    "arguments": {
                        "action": "falsify",
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "obligation_id": "ob-c-hyp",
                        "reason": "compensated by caller",
                        "category": "compensated-upstream",
                    },
                },
            },
        )
        fal_resp = _recv(proc)
        assert fal_resp["result"]["isError"] is False

        # 8. bv_gate reports ready with exit_code == 0
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "bv_gate",
                    "arguments": {
                        "action": "gate",
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "require_verification": True,
                    },
                },
            },
        )
        gate_resp = _recv(proc)
        gate_data = json.loads(gate_resp["result"]["content"][0]["text"])
        assert gate_data["state"] == "ready"
        assert gate_data["exit_code"] == 0
        assert len(gate_data["falsified"]) == 1

        # 8b. bv_playbook + bv_falsify(vacuity) + bv_scan(all) + bv_report
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "tools/call",
                "params": {"name": "bv_playbook", "arguments": {"action": "get", "query": "parser"}},
            },
        )
        pb_resp = _recv(proc)
        assert pb_resp["result"]["isError"] is False
        assert json.loads(pb_resp["result"]["content"][0]["text"])["id"] == "parser-metadata"

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 102,
                "method": "tools/call",
                "params": {
                    "name": "bv_falsify",
                    "arguments": {
                        "mode": "vacuity",
                        "test_file": str(next((runs_dir / "demo" / "probes").glob("*.py"))),
                    },
                },
            },
        )
        vac_resp = _recv(proc)
        assert vac_resp["result"]["isError"] is False
        assert json.loads(vac_resp["result"]["content"][0]["text"])["vacuous"] is False

        (pkg / "narrow.py").write_text(
            "from collections.abc import Sequence\n"
            "def take(items: Sequence[str]):\n"
            "    if isinstance(items, list):\n"
            "        return items\n"
            "    return list(items)\n",
            encoding="utf-8",
        )
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 103,
                "method": "tools/call",
                "params": {
                    "name": "bv_scan",
                    "arguments": {
                        "mode": "all",
                        "run": "demo",
                        "path": str(repo),
                        "runs_dir": str(runs_dir),
                    },
                },
            },
        )
        all_resp = _recv(proc)
        assert all_resp["result"]["isError"] is False
        all_data = json.loads(all_resp["result"]["content"][0]["text"])
        assert all_data["count"] >= 1

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1035,
                "method": "tools/call",
                "params": {
                    "name": "bv_ledger",
                    "arguments": {
                        "action": "promote_candidate",
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "indices": [1],
                    },
                },
            },
        )
        prom_resp = _recv(proc)
        assert prom_resp["result"]["isError"] is False
        prom_data = json.loads(prom_resp["result"]["content"][0]["text"])
        assert prom_data["count"] == 1

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 104,
                "method": "tools/call",
                "params": {
                    "name": "bv_report",
                    "arguments": {
                        "run": "demo",
                        "runs_dir": str(runs_dir),
                        "formats": ["markdown", "json", "sarif"],
                        "include_pr": True,
                    },
                },
            },
        )
        rep_resp = _recv(proc)
        assert rep_resp["result"]["isError"] is False
        rep_data = json.loads(rep_resp["result"]["content"][0]["text"])
        assert rep_data["sarif_results"] >= 1
        assert Path(rep_data["artifacts"]["sarif"]).exists()

        # 9. Malformed JSON line returns -32700 and server stays alive
        assert proc.stdin is not None
        proc.stdin.write(b"{bad json\n")
        proc.stdin.flush()
        err_resp = _recv(proc)
        assert err_resp["error"]["code"] == -32700

        _send(proc, {"jsonrpc": "2.0", "id": 11, "method": "ping"})
        assert _recv(proc)["id"] == 11

        proc.stdin.close()
        rc = proc.wait(timeout=10)
        assert rc == 0
        stderr_out = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        assert stderr_out == ""
    finally:
        if proc.poll() is None:
            proc.kill()
