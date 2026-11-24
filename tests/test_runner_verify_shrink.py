from __future__ import annotations

import json
import sys
from pathlib import Path

from bug_vet import gate, ledger as ledger_mod, runner, shrinker, verify


def test_unclassified_exit_code_counts_as_failure(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    probes = runs_dir / "exit7" / "probes"
    probes.mkdir(parents=True)
    (probes / "p_exit7.py").write_text(
        'OBLIGATION = "ob-manual-1"\nimport sys\nsys.exit(7)\n',
        encoding="utf-8",
    )
    res = runner.run_probes("exit7", runs_dir=runs_dir, python=sys.executable)
    row = res["results"][0]
    assert row["verdict"] == "ERROR(7)"
    assert res["failures"] == 1


def test_baseline_verify_red_to_green_and_test_evidence(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    mod = target / "calc.py"
    mod.write_text(
        "def normalize(text: str) -> str:\n"
        "    return text.strip()\n",
        encoding="utf-8",
    )
    test_file = target / "test_calc.py"
    test_file.write_text(
        "from calc import normalize\n\n"
        "def test_normalize_idempotent():\n"
        "    assert normalize(normalize('  hi  ')) == normalize('  hi  ')\n",
        encoding="utf-8",
    )

    runs_dir = tmp_path / "runs"
    run_path = runs_dir / "calc-run"
    (run_path / "probes").mkdir(parents=True)

    ledger_mod.sync_obligations(
        run_path,
        [
            {
                "id": "c-0001",
                "text": "normalize collapses internal whitespace",
                "source": "calc.py",
                "line": 1,
                "symbol": "normalize",
                "kind": "idempotence",
                "visibility": "public",
                "required_observation": "test",
            }
        ],
    )

    probe_file = runner.scaffold_probe(
        "calc-run",
        "c-0001",
        runs_dir=runs_dir,
        family="idempotence",
        out="probe_norm.py",
    )
    probe_file.write_text(
        'CLAIM = "c-0001"\n'
        "from calc import normalize\n\n"
        "def self_check():\n"
        "    return [] if normalize('a') == 'a' else ['broken']\n\n"
        "def main():\n"
        "    if self_check():\n"
        "        return 3\n"
        "    # expects collapsed internal whitespace\n"
        "    if normalize('a   b') != 'a b':\n"
        "        print('FAIL: internal whitespace kept')\n"
        "        return 1\n"
        "    print('PASS')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    # Baseline on unpatched tree fails
    base = verify.snapshot_baseline(
        "calc-run",
        runs_dir=runs_dir,
        python=sys.executable,
        cwd=target,
        env_pairs=[f"PYTHONPATH={target}"],
    )
    assert base["failing"] == ["probe_norm.py"]

    # Record probe run BEFORE patch as well
    runner.run_probes(
        "calc-run",
        runs_dir=runs_dir,
        python=sys.executable,
        cwd=target,
        env_pairs=[f"PYTHONPATH={target}"],
    )

    # Patch the bug
    mod.write_text(
        "def normalize(text: str) -> str:\n"
        "    return ' '.join(text.split())\n",
        encoding="utf-8",
    )

    # Verify flips red -> green
    ver = verify.verify_fix(
        "calc-run",
        runs_dir=runs_dir,
        python=sys.executable,
        cwd=target,
        env_pairs=[f"PYTHONPATH={target}"],
    )
    assert ver["ok"] is True
    assert ver["fixed"] == ["probe_norm.py"]

    # Real test-authority evidence closes the obligation requiring 'test'
    te = verify.run_test_evidence(
        "calc-run",
        "ob-c-0001",
        "python -m pytest -q",
        runs_dir=runs_dir,
        python=sys.executable,
        cwd=target,
        env_pairs=[f"PYTHONPATH={target}"],
    )
    assert te["authority"] == "test"
    assert te["verdict"] == "PASS"

    g = gate.evaluate_gate("calc-run", runs_dir=runs_dir, require_verification=True)
    assert g["state"] == "ready"
    assert g["exit_code"] == 0


def test_verify_flags_regression() -> None:
    before = {
        "results": [
            {"probe": "a.py", "verdict": "FAIL"},
            {"probe": "b.py", "verdict": "PASS"},
        ]
    }
    after = [
        {"probe": "a.py", "verdict": "PASS"},
        {"probe": "b.py", "verdict": "FAIL"},
    ]
    delta = verify.compare_verdicts(before, after)
    assert delta["fixed"] == ["a.py"]
    assert delta["regressed"] == ["b.py"]


def test_shrink_reads_bug_vet_input_and_minimizes_json(tmp_path: Path) -> None:
    probe = tmp_path / "shrink_probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "p = os.environ.get('BUG_VET_INPUT') or os.environ.get('BUGCHECKUP_INPUT')\n"
        "data = json.loads(open(p, encoding='utf-8').read())\n"
        "def has_boom(node):\n"
        "    if isinstance(node, dict):\n"
        "        return node.get('boom') is True or any(has_boom(v) for v in node.values())\n"
        "    if isinstance(node, list):\n"
        "        return any(has_boom(v) for v in node)\n"
        "    return False\n"
        "sys.exit(1 if has_boom(data) else 0)\n",
        encoding="utf-8",
    )
    inp = tmp_path / "failing.json"
    inp.write_text(
        json.dumps(
            {
                "meta": {"tags": ["a", "b"], "note": "extra"},
                "rows": [{"x": 1}, {"y": {"boom": True, "noise": "中文注释"}}],
                "tail": "more",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runs_dir = tmp_path / "runs"
    res = shrinker.run_shrink(
        "shrink-run",
        probe,
        inp,
        runs_dir=runs_dir,
        python=sys.executable,
        cwd=tmp_path,
        max_runs=200,
    )
    assert res["confirmed"] is True
    assert res["minimal"] == {"boom": True}
    assert res["minimal_bytes"] < res["original_bytes"]
    assert Path(res["minimal_file"]).exists()


def test_diff_between_two_local_interpreters(tmp_path: Path) -> None:
    scenario = tmp_path / "scen.py"
    scenario.write_text(
        "import os, sys\n"
        "mode = os.environ.get('SCEN_MODE', 'old')\n"
        "for line in sys.stdin:\n"
        "    s = line.strip()\n"
        "    if s:\n"
        "        print(s.upper() if mode == 'new' else s)\n",
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs.jsonl"
    inputs.write_text("alpha\nbeta\n", encoding="utf-8")
    res = verify.run_diff(
        "diff-run",
        scenario,
        inputs,
        runs_dir=tmp_path / "runs",
        old_python=sys.executable,
        new_python=sys.executable,
        old_env=["SCEN_MODE=old"],
        new_env=["SCEN_MODE=new"],
        cwd=tmp_path,
    )
    assert res["different"] == 2
    assert Path(res["artifact"]).exists()


def test_uncaught_python_traceback_downgrades_to_error_not_fail(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run_path = runs_dir / "tb-run"
    (run_path / "probes").mkdir(parents=True)

    ledger_mod.sync_obligations(
        run_path,
        [
            {
                "id": "c-crash",
                "text": "contract check crashes before asserting",
                "source": "mod.py",
                "line": 1,
                "symbol": "fn",
                "kind": "contract",
                "visibility": "public",
            }
        ],
    )
    crash_probe = run_path / "probes" / "probe_crash.py"
    crash_probe.write_text(
        'CLAIM = "c-crash"\n'
        "def self_check():\n"
        "    raise TypeError('broken setup before check ran')\n"
        "if __name__ == '__main__':\n"
        "    self_check()\n",
        encoding="utf-8",
    )

    res = runner.run_probes("tb-run", runs_dir=runs_dir, python=sys.executable, cwd=tmp_path)
    assert res["results"][0]["exit_code"] == 1
    assert res["results"][0]["verdict"] == "ERROR"

    state = ledger_mod.load(run_path)
    assert state["obligations"][0]["disposition"] == "unknown"
    rep = ledger_mod.evaluate(state, probe_dir=run_path / "probes")
    assert rep["findings"] == []
    assert rep["state"] == "partial"
    assert any(b["kind"] == "inconclusive" for b in rep["blockers"])
