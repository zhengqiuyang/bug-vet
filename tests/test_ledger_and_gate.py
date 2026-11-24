from __future__ import annotations

from pathlib import Path

from bug_vet import gate, ledger as ledger_mod


def _claims() -> list[dict]:
    return [
        {
            "id": "c-0001",
            "text": "lossless roundtrip",
            "source": "m.py",
            "line": 1,
            "symbol": "f",
            "kind": "roundtrip",
            "visibility": "public",
        },
        {
            "id": "c-0002",
            "text": "raises ValueError on bad input",
            "source": "m.py",
            "line": 10,
            "symbol": "g",
            "kind": "error_contract",
            "visibility": "public",
        },
    ]


def test_authority_ladder_and_insufficient_authority_blocker(tmp_path: Path) -> None:
    assert ledger_mod.comparable("test", "doc") is True
    assert ledger_mod.comparable("published", "test") is True
    assert ledger_mod.comparable("probe", "published") is False
    assert ledger_mod.comparable("probe", "test") is False

    runs_dir = tmp_path / "runs"
    run = runs_dir / "auth-run"
    (run / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(
        run,
        [
            {
                "id": "c-0001",
                "text": "matches published behavior",
                "source": "m.py",
                "line": 1,
                "symbol": "f",
                "kind": "roundtrip",
                "visibility": "public",
                "required_observation": "published",
            }
        ],
    )
    # Probe-level evidence cannot close a published-level obligation
    ledger_mod.record_probe_run(
        run,
        [{"probe": "p1.py", "claim": "c-0001", "verdict": "FAIL", "exit_code": 1, "output": "diff"}],
        "python",
        run,
    )
    state = ledger_mod.load(run)
    assert state["obligations"][0]["disposition"] == "unknown"
    report = ledger_mod.evaluate(state)
    assert any(b["kind"] == "insufficient-authority" for b in report["blockers"])
    gate_res = gate.evaluate_gate("auth-run", runs_dir=runs_dir)
    assert gate_res["state"] == "partial"
    assert gate_res["exit_code"] == 2

    # Published-level evidence closes the obligation cleanly even from another env
    ledger_mod.record_published_evidence(
        run,
        "ob-c-0001",
        "demo-pkg",
        "1.0.0",
        differing_count=2,
        details="output mismatch on empty input",
        python="other-venv-python",
        cwd=tmp_path / "other_cwd",
    )
    state = ledger_mod.load(run)
    assert state["obligations"][0]["disposition"] == "probed-fail"
    gate_res2 = gate.evaluate_gate("auth-run", runs_dir=runs_dir)
    assert gate_res2["state"] == "ready"
    assert gate_res2["exit_code"] == 0


def test_missing_self_check_fails_closed(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run = runs_dir / "sc-run"
    (run / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(run, [_claims()[0]])
    ledger_mod.record_probe_run(
        run,
        [
            {
                "probe": "p_noself.py",
                "claim": "c-0001",
                "has_self_check": False,
                "verdict": "PASS",
                "exit_code": 0,
                "output": "ok",
            }
        ],
        "python",
        run,
    )
    state = ledger_mod.load(run)
    assert state["obligations"][0]["disposition"] == "unknown"
    report = ledger_mod.evaluate(state)
    assert any(b["kind"] == "missing-self-check" for b in report["blockers"])
    assert report["state"] == "partial"


def test_falsification_settles_obligation_and_stays_auditable(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run = runs_dir / "falsify-run"
    (run / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(run, _claims())
    ledger_mod.record_probe_run(
        run,
        [{"probe": "p2.py", "claim": "c-0002", "verdict": "PASS", "exit_code": 0, "output": "ok"}],
        "python",
        run,
    )
    ledger_mod.record_falsification(
        run,
        "ob-c-0001",
        reason="outer router already defaults COHERE_API_BASE before helper runs",
        category="compensated-upstream",
        source_location="router.py:42",
    )
    gate_res = gate.evaluate_gate("falsify-run", runs_dir=runs_dir)
    assert gate_res["state"] == "ready"
    assert gate_res["exit_code"] == 0
    assert len(gate_res["falsified"]) == 1
    assert gate_res["falsified"][0]["falsification"]["category"] == "compensated-upstream"


def test_contradiction_inconclusive_env_change_and_unbound_probe(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"

    # 1. Fail + pass normal ready state
    r1 = runs_dir / "r1"
    (r1 / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(r1, _claims())
    ledger_mod.record_probe_run(
        r1,
        [
            {"probe": "p1.py", "claim": "c-0001", "verdict": "FAIL", "exit_code": 1, "output": "boom"},
            {"probe": "p2.py", "claim": "c-0002", "verdict": "PASS", "exit_code": 0, "output": "ok"},
        ],
        "python",
        r1,
    )
    assert gate.evaluate_gate("r1", runs_dir=runs_dir)["exit_code"] == 0

    # Unbound failing probe blocks to not-ready (exit 1)
    p3 = r1 / "probes" / "p3.py"
    p3.write_text("raise SystemExit(1)\n", encoding="utf-8")
    ledger_mod.record_probe_run(
        r1,
        [{"probe": "p3.py", "claim": None, "verdict": "FAIL", "exit_code": 1, "output": "x"}],
        "python",
        r1,
    )
    g1 = gate.evaluate_gate("r1", runs_dir=runs_dir)
    assert g1["state"] == "not-ready"
    assert g1["exit_code"] == 1
    assert any(b["kind"] == "unbound-evidence" for b in g1["blockers"])

    # Deleting the unbound probe clears its historical blocker
    p3.unlink()
    assert gate.evaluate_gate("r1", runs_dir=runs_dir)["state"] == "ready"

    # 2. Contradiction on same probe digest
    r2 = runs_dir / "r2"
    (r2 / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(r2, _claims())
    ledger_mod.record_probe_run(
        r2, [{"probe": "p1.py", "claim": "c-0001", "verdict": "PASS", "exit_code": 0, "output": ""}], "python", r2
    )
    ledger_mod.record_probe_run(
        r2, [{"probe": "p1.py", "claim": "c-0001", "verdict": "FAIL", "exit_code": 1, "output": ""}], "python", r2
    )
    g2 = gate.evaluate_gate("r2", runs_dir=runs_dir)
    assert g2["state"] == "not-ready"
    assert any(b["kind"] == "contradiction" for b in g2["blockers"])

    # 3. INCONCLUSIVE never becomes pass
    r3 = runs_dir / "r3"
    (r3 / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(r3, _claims())
    ledger_mod.record_probe_run(
        r3,
        [{"probe": "p1.py", "claim": "c-0001", "verdict": "INCONCLUSIVE", "exit_code": 2, "output": ""}],
        "python",
        r3,
    )
    assert ledger_mod.load(r3)["obligations"][0]["disposition"] == "unknown"
    assert gate.evaluate_gate("r3", runs_dir=runs_dir)["state"] == "partial"

    # 4. Environment change invalidates earlier probe
    r4 = runs_dir / "r4"
    (r4 / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(r4, _claims())
    ledger_mod.record_probe_run(
        r4, [{"probe": "p1.py", "claim": "c-0001", "verdict": "FAIL", "exit_code": 1, "output": ""}], "python", r4
    )
    other = tmp_path / "other"
    other.mkdir()
    ledger_mod.record_probe_run(r4, [], "python", other)
    g4 = gate.evaluate_gate("r4", runs_dir=runs_dir)
    assert any(b["kind"] == "environment-changed" for b in g4["blockers"])
    assert g4["state"] == "partial"


def test_error_code_verdicts_are_non_definite_and_counted(tmp_path: Path) -> None:
    from bug_vet import runner

    assert runner.is_failing_verdict("ERROR(-1)") is True
    assert runner.is_failing_verdict("ERROR(7)") is True
    assert runner.is_failing_verdict("PASS") is False
    assert runner.is_failing_verdict("INCONCLUSIVE") is False
    assert ledger_mod.is_non_definite_verdict("ERROR(7)") is True
    assert ledger_mod.is_non_definite_verdict("PASS") is False

    runs_dir = tmp_path / "runs"
    run = runs_dir / "err-run"
    (run / "probes").mkdir(parents=True)
    ledger_mod.sync_obligations(run, [_claims()[0]])
    ledger_mod.record_probe_run(
        run,
        [{"probe": "p1.py", "claim": "c-0001", "verdict": "ERROR(7)", "exit_code": 7, "output": "crashed"}],
        "python",
        run,
    )
    state = ledger_mod.load(run)
    assert state["obligations"][0]["disposition"] == "unknown"
    report = ledger_mod.evaluate(state)
    assert any(b["kind"] == "inconclusive" for b in report["blockers"])
    assert report["state"] == "partial"
