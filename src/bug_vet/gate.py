"""Fail-closed readiness gate across ledger, red/green verification, and collision checks."""

from __future__ import annotations

import json
from pathlib import Path

from bug_vet import ledger as ledger_mod
from bug_vet.paths import run_dir

STATE_EXIT_CODES = {"ready": 0, "partial": 2, "not-ready": 1}


def evaluate_gate(
    run_name: str,
    *,
    runs_dir: str | Path | None = None,
    require_verification: bool = False,
    require_collision: bool = False,
) -> dict:
    """Evaluate all artifacts for ``run_name``, persist ``gate.json``, and return readiness report."""
    path = run_dir(run_name, runs_dir=runs_dir)
    if not path.exists():
        raise FileNotFoundError(f"run directory does not exist: {path}")

    ledger = ledger_mod.load(path)
    report = ledger_mod.evaluate(ledger, probe_dir=path / "probes")

    ver_path = path / "verification.json"
    if ver_path.exists():
        verification = json.loads(ver_path.read_text(encoding="utf-8", errors="replace"))
        report["verification"] = {
            "fixed": verification.get("fixed", []),
            "still_failing": verification.get("still_failing", []),
            "regressed": verification.get("regressed", []),
        }
        if verification.get("regressed") or verification.get("still_failing"):
            report["blockers"].append(
                {
                    "kind": "verification-failed",
                    "still_failing": verification.get("still_failing", []),
                    "regressed": verification.get("regressed", []),
                }
            )
            report["state"] = "not-ready"
        elif require_verification and not verification.get("fixed"):
            report["blockers"].append({"kind": "verification-no-flip"})
            if report["state"] == "ready":
                report["state"] = "partial"
    elif require_verification:
        report["blockers"].append({"kind": "verification-missing"})
        if report["state"] == "ready":
            report["state"] = "partial"

    col_path = path / "collision-results.json"
    if col_path.exists():
        collision = json.loads(col_path.read_text(encoding="utf-8", errors="replace"))
        report["collision"] = {
            "verdict": collision.get("verdict"),
            "merged_candidates": collision.get("merged_candidates"),
            "related_open_prs": collision.get("related_open_prs"),
        }
        if collision.get("verdict") != "CLEAR":
            report["blockers"].append(
                {"kind": "collision-blocked", "verdict": collision.get("verdict")}
            )
            report["state"] = "not-ready"
    elif require_collision:
        report["blockers"].append({"kind": "collision-missing"})
        if report["state"] == "ready":
            report["state"] = "partial"

    gate_file = path / "gate.json"
    gate_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "run": run_name,
        "artifact": str(gate_file),
        "exit_code": STATE_EXIT_CODES.get(report["state"], 1),
        **report,
    }
