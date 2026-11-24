"""Zero-dependency stdio MCP server exposing bug-vet tools over JSON-RPC 2.0."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from bug_vet import __version__, collide, falsifier, gate, ledger as ledger_mod, playbooks, report, runner, shrinker, verify
from bug_vet.oracles import (
    claims as claims_oracle,
    contracts as contracts_oracle,
    parser_meta as parser_meta_oracle,
    roundtrip as roundtrip_oracle,
    run_all_scans,
    siblings as siblings_oracle,
)
from bug_vet.paths import ensure_run, run_dir

DEFAULT_PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "bv_scan",
        "description": (
            "Run contract or structural discovery scans (claims, roundtrip, examples, "
            "callsites, siblings, parser_meta, contracts, or all) into a run directory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [
                        "claims",
                        "roundtrip",
                        "examples",
                        "callsites",
                        "siblings",
                        "parser_meta",
                        "contracts",
                        "all",
                    ],
                    "description": "Scan mode to run.",
                },
                "run": {"type": "string", "description": "Run name."},
                "path": {"type": "string", "description": "Target directory or Markdown file."},
                "runs_dir": {"type": "string", "description": "Optional base runs directory."},
                "tier": {"type": "string", "enum": ["A", "B", "all"]},
                "max_per_family": {"type": "integer"},
                "include_tests": {"type": "boolean"},
                "include_unreachable": {"type": "boolean"},
                "include_ts": {"type": "boolean"},
                "register_obligations": {"type": "boolean"},
                "apply_falsifier": {"type": "boolean"},
                "function": {"type": "string", "description": "Function name for callsites mode."},
                "lang": {"type": "string", "description": "Language for examples/callsites mode."},
                "python": {"type": "string", "description": "Interpreter path for examples mode."},
                "cwd": {"type": "string", "description": "Working directory for examples mode."},
                "import_root": {"type": "string", "description": "Import root for roundtrip mode."},
                "timeout": {"type": "integer"},
            },
            "required": ["mode", "run", "path"],
        },
    },
    {
        "name": "bv_probe",
        "description": "Scaffold a standalone probe template or execute existing probes and record ledger evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["run", "scaffold"]},
                "run": {"type": "string"},
                "runs_dir": {"type": "string"},
                "python": {"type": "string"},
                "cwd": {"type": "string"},
                "env": {"type": "array", "items": {"type": "string"}},
                "timeout": {"type": "integer"},
                "claim": {"type": "string", "description": "Claim or obligation id when scaffolding."},
                "family": {"type": "string"},
                "description": {"type": "string"},
                "out": {"type": "string"},
            },
            "required": ["action", "run"],
        },
    },
    {
        "name": "bv_shrink",
        "description": "Delta-debug a failing JSON input down to the minimal counterexample via BUG_VET_INPUT.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run": {"type": "string"},
                "script": {"type": "string"},
                "input": {"type": "string"},
                "runs_dir": {"type": "string"},
                "python": {"type": "string"},
                "cwd": {"type": "string"},
                "env": {"type": "array", "items": {"type": "string"}},
                "timeout": {"type": "integer"},
                "max_runs": {"type": "integer"},
            },
            "required": ["run", "script", "input"],
        },
    },
    {
        "name": "bv_verify",
        "description": "Run baseline snapshot, red-to-green fix verification, test-authority runner, or released-version diff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["baseline", "verify", "test_evidence", "diff"]},
                "run": {"type": "string"},
                "runs_dir": {"type": "string"},
                "python": {"type": "string"},
                "cwd": {"type": "string"},
                "env": {"type": "array", "items": {"type": "string"}},
                "timeout": {"type": "integer"},
                "obligation_id": {"type": "string"},
                "command": {"type": "string"},
                "scenario": {"type": "string"},
                "inputs": {"type": "string"},
                "old_python": {"type": "string"},
                "new_python": {"type": "string"},
                "pip_version": {"type": "string"},
                "pip_package": {"type": "string"},
                "pip_python": {"type": "string"},
                "old_env": {"type": "array", "items": {"type": "string"}},
                "new_env": {"type": "array", "items": {"type": "string"}},
                "max_diffs": {"type": "integer"},
            },
            "required": ["mode", "run"],
        },
    },
    {
        "name": "bv_ledger",
        "description": "Inspect obligation readiness, register an obligation, or record an adversarial falsification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["show", "add_obligation", "falsify", "promote_candidate"]},
                "run": {"type": "string"},
                "runs_dir": {"type": "string"},
                "all": {"type": "boolean"},
                "claim": {"type": "object"},
                "indices": {"type": "array", "items": {"type": "integer"}},
                "structural_only": {"type": "boolean"},
                "required": {
                    "type": "string",
                    "enum": ["doc", "probe", "test", "published", "runtime"],
                },
                "obligation_id": {"type": "string"},
                "reason": {"type": "string"},
                "category": {"type": "string"},
                "source_location": {"type": "string"},
            },
            "required": ["action", "run"],
        },
    },
    {
        "name": "bv_gate",
        "description": "Evaluate fail-closed readiness gate, check GitHub PR/timeline collisions, or list unclaimed issues.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["gate", "collide", "issues"]},
                "run": {"type": "string"},
                "runs_dir": {"type": "string"},
                "require_verification": {"type": "boolean"},
                "require_collision": {"type": "boolean"},
                "repo": {"type": "string"},
                "issue": {"type": "integer"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "files": {"type": "array", "items": {"type": "string"}},
                "require_clear": {"type": "boolean"},
                "label": {"type": "string"},
                "limit": {"type": "integer"},
                "show": {"type": "integer"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "bv_playbook",
        "description": "List or retrieve compiled bug-family playbooks including probe recipes and falsification pitfalls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get"]},
                "query": {"type": "string", "description": "Playbook id, bug family, or keyword."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "bv_falsify",
        "description": "Run adversarial falsification (upstream compensation check, vacuous test audit, or record falsification).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["compensation", "vacuity", "record"]},
                "path": {"type": "string", "description": "Repo root for compensation mode."},
                "symbol": {"type": "string", "description": "Target function symbol for compensation mode."},
                "env_var": {"type": "string", "description": "Environment variable name to trace upstream."},
                "file_hint": {"type": "string"},
                "max_depth": {"type": "integer"},
                "test_file": {"type": "string", "description": "Probe or test file path for vacuity audit."},
                "target_symbol": {"type": "string", "description": "Symbol under test for vacuity mock checks."},
                "run": {"type": "string", "description": "Run name for record mode."},
                "runs_dir": {"type": "string"},
                "obligation_id": {"type": "string"},
                "reason": {"type": "string"},
                "category": {"type": "string"},
                "source_location": {"type": "string"},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "bv_report",
        "description": "Generate findings.json, REPORT.md, SARIF 2.1.0 results.sarif, and optional PR.md from a run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run": {"type": "string"},
                "runs_dir": {"type": "string"},
                "formats": {"type": "array", "items": {"type": "string"}},
                "include_pr": {"type": "boolean"},
                "include_candidates": {"type": "boolean"},
            },
            "required": ["run"],
        },
    },
]


def _require_str(args: dict[str, Any], key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"missing or invalid string argument: {key!r}")
    return val


def _handle_scan(args: dict[str, Any]) -> dict[str, Any]:
    mode = _require_str(args, "mode")
    run = _require_str(args, "run")
    path = _require_str(args, "path")
    runs_dir = args.get("runs_dir")
    if mode == "claims":
        return claims_oracle.run_claims_scan(
            run,
            path,
            runs_dir=runs_dir,
            include_tests=bool(args.get("include_tests", False)),
            tier=str(args.get("tier", "A")),
            max_per_family=int(args.get("max_per_family", 40)),
            reachable_only=not bool(args.get("include_unreachable", False)),
        )
    if mode == "roundtrip":
        return roundtrip_oracle.generate_roundtrip_probes(
            run,
            path,
            import_root=args.get("import_root"),
            runs_dir=runs_dir,
        )
    if mode == "examples":
        return claims_oracle.run_examples(
            run,
            path,
            runs_dir=runs_dir,
            lang=str(args.get("lang", "python")),
            python=args.get("python"),
            cwd=args.get("cwd"),
            timeout=int(args.get("timeout", 120)),
        )
    if mode == "callsites":
        func = _require_str(args, "function")
        return claims_oracle.scan_callsites(
            run,
            path,
            func,
            runs_dir=runs_dir,
            lang=str(args.get("lang", "python")),
        )
    if mode == "siblings":
        return siblings_oracle.run_siblings_scan(
            run,
            path,
            runs_dir=runs_dir,
            include_ts=bool(args.get("include_ts", True)),
            register_obligations=bool(args.get("register_obligations", False)),
        )
    if mode == "parser_meta":
        return parser_meta_oracle.run_parser_meta_scan(
            run,
            path,
            runs_dir=runs_dir,
            register_obligations=bool(args.get("register_obligations", False)),
        )
    if mode == "contracts":
        return contracts_oracle.run_contracts_scan(
            run,
            path,
            runs_dir=runs_dir,
            register_obligations=bool(args.get("register_obligations", False)),
        )
    if mode == "all":
        return run_all_scans(
            run,
            path,
            runs_dir=runs_dir,
            include_ts=bool(args.get("include_ts", True)),
            apply_falsifier=bool(args.get("apply_falsifier", True)),
            claim_tier=str(args.get("tier", "A")),
        )
    raise ValueError(f"unknown scan mode: {mode!r}")


def _handle_probe(args: dict[str, Any]) -> dict[str, Any]:
    action = _require_str(args, "action")
    run = _require_str(args, "run")
    runs_dir = args.get("runs_dir")
    if action == "run":
        return runner.run_probes(
            run,
            runs_dir=runs_dir,
            python=args.get("python"),
            cwd=args.get("cwd"),
            env_pairs=args.get("env"),
            timeout=int(args.get("timeout", 300)),
        )
    if action == "scaffold":
        claim = _require_str(args, "claim")
        target = runner.scaffold_probe(
            run,
            claim,
            runs_dir=runs_dir,
            family=str(args.get("family", "roundtrip")),
            description=args.get("description"),
            out=args.get("out"),
        )
        return {"run": run, "probe_path": str(target), "claim": claim}
    raise ValueError(f"unknown probe action: {action!r}")


def _handle_shrink(args: dict[str, Any]) -> dict[str, Any]:
    return shrinker.run_shrink(
        _require_str(args, "run"),
        _require_str(args, "script"),
        _require_str(args, "input"),
        runs_dir=args.get("runs_dir"),
        python=args.get("python"),
        cwd=args.get("cwd"),
        env_pairs=args.get("env"),
        timeout=int(args.get("timeout", 120)),
        max_runs=int(args.get("max_runs", 300)),
    )


def _handle_verify(args: dict[str, Any]) -> dict[str, Any]:
    mode = _require_str(args, "mode")
    run = _require_str(args, "run")
    runs_dir = args.get("runs_dir")
    if mode == "baseline":
        return verify.snapshot_baseline(
            run,
            runs_dir=runs_dir,
            python=args.get("python"),
            cwd=args.get("cwd"),
            env_pairs=args.get("env"),
            timeout=int(args.get("timeout", 300)),
        )
    if mode == "verify":
        return verify.verify_fix(
            run,
            runs_dir=runs_dir,
            python=args.get("python"),
            cwd=args.get("cwd"),
            env_pairs=args.get("env"),
            timeout=int(args.get("timeout", 300)),
        )
    if mode == "test_evidence":
        return verify.run_test_evidence(
            run,
            _require_str(args, "obligation_id"),
            _require_str(args, "command"),
            runs_dir=runs_dir,
            python=args.get("python"),
            cwd=args.get("cwd"),
            env_pairs=args.get("env"),
            timeout=int(args.get("timeout", 300)),
        )
    if mode == "diff":
        return verify.run_diff(
            run,
            _require_str(args, "scenario"),
            _require_str(args, "inputs"),
            runs_dir=runs_dir,
            old_python=args.get("old_python"),
            new_python=args.get("new_python"),
            pip_version=args.get("pip_version"),
            pip_package=args.get("pip_package"),
            pip_python=str(args.get("pip_python", "3.12")),
            old_env=args.get("old_env"),
            new_env=args.get("new_env"),
            cwd=args.get("cwd"),
            timeout=int(args.get("timeout", 600)),
            max_diffs=int(args.get("max_diffs", 50)),
            obligation_id=args.get("obligation_id"),
        )
    raise ValueError(f"unknown verify mode: {mode!r}")


def _handle_ledger(args: dict[str, Any]) -> dict[str, Any]:
    action = _require_str(args, "action")
    run = _require_str(args, "run")
    runs_dir = args.get("runs_dir")
    path = run_dir(run, runs_dir=runs_dir)

    if action == "show":
        if not path.exists():
            raise FileNotFoundError(f"run directory does not exist: {path}")
        state = ledger_mod.load(path)
        report = ledger_mod.evaluate(state, probe_dir=path / "probes")
        payload: dict[str, Any] = {
            "run": run,
            "summary": ledger_mod.summary(state, probe_dir=path / "probes"),
            **report,
        }
        if args.get("all", False):
            payload["obligations"] = state["obligations"]
        return payload

    if action == "add_obligation":
        claim = args.get("claim")
        if not isinstance(claim, dict):
            raise ValueError("add_obligation requires a 'claim' object")
        path = ensure_run(run, runs_dir=runs_dir)
        required = str(args.get("required", "probe"))
        ob = ledger_mod.add_obligation(path, claim, required=required)
        return {"run": run, "obligation": ob}

    if action == "promote_candidate":
        indices = args.get("indices")
        structural_only = bool(args.get("structural_only", False))
        required = str(args.get("required", "probe"))
        promoted = ledger_mod.promote_candidates(
            path,
            indices=[int(i) for i in indices] if isinstance(indices, list) else None,
            structural_only=structural_only,
            required=required,
        )
        return {"run": run, "count": len(promoted), "promoted": promoted}

    if action == "falsify":
        ob_id = _require_str(args, "obligation_id")
        reason = _require_str(args, "reason")
        category = str(args.get("category", "false-positive"))
        state, ev = ledger_mod.record_falsification(
            path,
            ob_id,
            reason,
            category,
            source_location=args.get("source_location"),
        )
        return {
            "run": run,
            "obligation_id": ev["obligation_id"],
            "evidence_id": ev["id"],
            "summary": ledger_mod.summary(state, probe_dir=path / "probes"),
        }

    raise ValueError(f"unknown ledger action: {action!r}")


def _handle_gate(args: dict[str, Any]) -> dict[str, Any]:
    action = _require_str(args, "action")
    runs_dir = args.get("runs_dir")
    if action == "gate":
        run = _require_str(args, "run")
        return gate.evaluate_gate(
            run,
            runs_dir=runs_dir,
            require_verification=bool(args.get("require_verification", False)),
            require_collision=bool(args.get("require_collision", False)),
        )
    if action == "collide":
        repo = _require_str(args, "repo")
        return collide.check_collision(
            repo,
            issue=args.get("issue"),
            keywords=args.get("keywords"),
            files=args.get("files"),
            runs_dir=runs_dir,
            name=args.get("run"),
            require_clear=bool(args.get("require_clear", False)),
        )
    if action == "issues":
        repo = _require_str(args, "repo")
        return collide.scan_issues(
            repo,
            runs_dir=runs_dir,
            name=args.get("run"),
            label=args.get("label"),
            limit=int(args.get("limit", 50)),
            show=int(args.get("show", 25)),
        )
    raise ValueError(f"unknown gate action: {action!r}")


def _handle_playbook(args: dict[str, Any]) -> dict[str, Any]:
    action = _require_str(args, "action")
    if action == "list":
        entries = playbooks.list_playbooks()
        return {"count": len(entries), "playbooks": entries}
    if action == "get":
        return playbooks.get_playbook(_require_str(args, "query"))
    raise ValueError(f"unknown playbook action: {action!r}")


def _handle_falsify(args: dict[str, Any]) -> dict[str, Any]:
    mode = _require_str(args, "mode")
    if mode == "compensation":
        return falsifier.check_upstream_compensation(
            _require_str(args, "path"),
            _require_str(args, "symbol"),
            env_var=args.get("env_var"),
            file_hint=args.get("file_hint"),
            max_depth=int(args.get("max_depth", 3)),
        )
    if mode == "vacuity":
        return falsifier.audit_test_vacuity(
            _require_str(args, "test_file"),
            target_symbol=args.get("target_symbol"),
        )
    if mode == "record":
        run = _require_str(args, "run")
        ob_id = _require_str(args, "obligation_id")
        reason = _require_str(args, "reason")
        category = str(args.get("category", "false-positive"))
        path = run_dir(run, runs_dir=args.get("runs_dir"))
        state, ev = ledger_mod.record_falsification(
            path,
            ob_id,
            reason,
            category,
            source_location=args.get("source_location"),
        )
        return {
            "run": run,
            "obligation_id": ev["obligation_id"],
            "evidence_id": ev["id"],
            "summary": ledger_mod.summary(state, probe_dir=path / "probes"),
        }
    raise ValueError(f"unknown falsify mode: {mode!r}")


def _handle_report(args: dict[str, Any]) -> dict[str, Any]:
    formats = args.get("formats") or ["markdown", "json", "sarif"]
    return report.generate_report(
        _require_str(args, "run"),
        runs_dir=args.get("runs_dir"),
        formats=formats,
        include_pr=bool(args.get("include_pr", False)),
        include_candidates=bool(args.get("include_candidates", False)),
    )


DISPATCH: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "bv_scan": _handle_scan,
    "bv_probe": _handle_probe,
    "bv_shrink": _handle_shrink,
    "bv_verify": _handle_verify,
    "bv_ledger": _handle_ledger,
    "bv_gate": _handle_gate,
    "bv_playbook": _handle_playbook,
    "bv_falsify": _handle_falsify,
    "bv_report": _handle_report,
}


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Process one parsed JSON-RPC 2.0 message and return a response dict or None."""
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if not isinstance(method, str):
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32600, "message": "Invalid Request: missing method"},
            }
        return None

    # Notifications have no id and expect no reply.
    if msg_id is None:
        return None

    if method == "initialize":
        proto = params.get("protocolVersion")
        if not isinstance(proto, str) or not proto:
            proto = DEFAULT_PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": proto,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bug-vet", "version": __version__},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name") if isinstance(params, dict) else None
        tool_args = params.get("arguments") if isinstance(params, dict) else None
        if not isinstance(tool_name, str) or tool_name not in DISPATCH:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name!r}"},
            }
        if tool_args is None:
            tool_args = {}
        if not isinstance(tool_args, dict):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "Tool arguments must be an object"},
            }
        try:
            payload = DISPATCH[tool_name](tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}
                    ],
                    "isError": False,
                },
            }
        except Exception as exc:  # noqa: BLE001 - tool errors stay inside JSON-RPC result
            err_payload = {"error": str(exc), "type": type(exc).__name__}
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(err_payload, ensure_ascii=False)}
                    ],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_stdio(
    in_stream: BinaryIO | None = None,
    out_stream: BinaryIO | None = None,
) -> int:
    """Serve newline-delimited JSON-RPC 2.0 MCP messages over UTF-8 binary stdio streams."""
    reader = in_stream or sys.stdin.buffer
    writer = out_stream or sys.stdout.buffer

    while True:
        raw_line = reader.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            resp: dict[str, Any] | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        else:
            if not isinstance(message, dict):
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
                }
            else:
                resp = handle_message(message)

        if resp is not None:
            encoded = (json.dumps(resp, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            writer.write(encoded)
            writer.flush()
    return 0
