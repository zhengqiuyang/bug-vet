from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bug_vet import bench, gate, ledger as ledger_mod, report, runner


def test_report_generates_markdown_json_sarif_and_pr(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run = runs_dir / "rep-run"
    (run / "probes").mkdir(parents=True)

    ledger_mod.sync_obligations(
        run,
        [
            {
                "id": "c-0001",
                "text": "encode/decode preserves string type",
                "source": "src/codec.py",
                "line": 12,
                "symbol": "decode",
                "kind": "roundtrip",
                "visibility": "public",
            },
            {
                "id": "c-0002",
                "text": "env fallback missing",
                "source": "src/leaf.py",
                "line": 44,
                "symbol": "call",
                "kind": "sibling-asymmetry",
                "visibility": "public",
            },
        ],
    )
    ledger_mod.record_probe_run(
        run,
        [{"probe": "p1.py", "claim": "c-0001", "verdict": "FAIL", "exit_code": 1, "output": "type changed"}],
        "python",
        tmp_path,
    )
    ledger_mod.record_falsification(
        run,
        "ob-c-0002",
        reason="caller resolves env",
        category="compensated-upstream",
        source_location="router.py:9",
    )
    gate.evaluate_gate("rep-run", runs_dir=runs_dir)

    res = report.generate_report(
        "rep-run",
        runs_dir=runs_dir,
        formats=("markdown", "json", "sarif"),
        include_pr=True,
    )
    assert res["state"] == "ready"
    assert res["finding_count"] == 1
    assert res["falsified_count"] == 1
    assert res["sarif_results"] == 1

    sarif = json.loads(Path(res["artifacts"]["sarif"]).read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "bug-vet"
    loc = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/codec.py"
    assert loc["region"]["startLine"] == 12

    md = Path(res["artifacts"]["markdown"]).read_text(encoding="utf-8")
    assert "Confirmed Findings" in md
    assert "Falsified Hypotheses" in md
    assert "compensated-upstream" in md
    assert Path(res["artifacts"]["pr"]).exists()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


def test_contractbench_materialize_and_eval_on_synthetic_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "mini_repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")

    pkg = repo / "pkg"
    pkg.mkdir()
    target_file = pkg / "deser.py"
    target_file.write_text(
        "def load_config(data):\n"
        "    return list(data.get('items', {}).items())\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "buggy commit")
    parent_sha = _git(repo, "rev-parse", "HEAD")

    target_file.write_text(
        "def load_config(data):\n"
        "    items = data.get('items', {})\n"
        "    if not isinstance(items, dict):\n"
        "        raise ValueError('items must be a mapping')\n"
        "    return list(items.items())\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix commit")
    fix_sha = _git(repo, "rev-parse", "HEAD")

    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "synthetic-deser",
                    "repo": str(repo),
                    "fix_ref": fix_sha,
                    "parent_ref": parent_sha,
                    "paths": ["pkg"],
                    "target": {"files": ["pkg/deser.py"], "symbol": "load_config"},
                    "why": "unsafe .get('items', {}).items()",
                }
            ]
        ),
        encoding="utf-8",
    )

    out_json = tmp_path / "out.json"
    out_md = tmp_path / "BENCH.md"
    res = bench.run_benchmark(
        cases_path,
        cache_dir=tmp_path / "cache",
        json_out=out_json,
        markdown_out=out_md,
    )
    assert res["summary"]["recall_at_5_count"] == 1
    assert res["summary"]["post_fix_cleared_count"] == 1
    assert out_json.exists()
    assert "HIT" in out_md.read_text(encoding="utf-8")


def test_report_sarif_suppresses_settled_candidates(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    run = runs_dir / "sarif-sup"
    (run / "probes").mkdir(parents=True)

    (run / "candidates.json").write_text(
        json.dumps(
            {
                "run": "sarif-sup",
                "target": str(tmp_path / "repo"),
                "candidates": [
                    {
                        "oracle": "siblings",
                        "family": "sibling-asymmetry",
                        "file": "pkg/a.py",
                        "line": 10,
                        "symbol": "foo",
                        "detail": "settled as falsified",
                        "confidence": "high",
                    },
                    {
                        "oracle": "contracts",
                        "family": "type-contract",
                        "file": "pkg/b.py",
                        "line": 20,
                        "symbol": "bar",
                        "detail": "unsettled candidate",
                        "confidence": "high",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    ledger_mod.sync_obligations(
        run,
        [
            {
                "id": "c-settled",
                "text": "settled as falsified",
                "source": str(tmp_path / "repo" / "pkg" / "a.py"),
                "line": 10,
                "symbol": "foo",
                "kind": "sibling-asymmetry",
                "visibility": "public",
            }
        ],
    )
    ledger_mod.record_falsification(
        run,
        "ob-c-settled",
        reason="false positive",
        category="false-positive",
        source_location="pkg/a.py:10",
    )

    rep = report.generate_report("sarif-sup", runs_dir=runs_dir, include_candidates=True)
    assert rep["sarif_results"] == 1
    sarif = json.loads(Path(rep["artifacts"]["sarif"]).read_text(encoding="utf-8"))
    uris = [r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in sarif["runs"][0]["results"]]
    assert uris == ["pkg/b.py"]
