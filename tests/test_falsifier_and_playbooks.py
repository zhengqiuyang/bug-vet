from __future__ import annotations

from pathlib import Path

from bug_vet import falsifier, playbooks
from bug_vet.oracles import siblings


def test_upstream_compensation_distinguishes_compensated_vs_uncompensated(tmp_path: Path) -> None:
    (tmp_path / "router.py").write_text(
        "import os\n"
        "from leaf import call_xai, call_cohere\n\n"
        "def dispatch_xai(base=None):\n"
        "    base = base or os.getenv('XAI_API_BASE')\n"
        "    return call_xai(api_base=base)\n\n"
        "def dispatch_cohere(base=None):\n"
        "    return call_cohere(api_base=base)\n",
        encoding="utf-8",
    )
    (tmp_path / "leaf.py").write_text(
        "import os\n"
        "def call_xai(api_base=None):\n"
        "    return os.getenv('XAI_API_KEY'), api_base\n\n"
        "def call_cohere(api_base=None):\n"
        "    return os.getenv('COHERE_API_KEY'), api_base\n",
        encoding="utf-8",
    )

    comp_xai = falsifier.check_upstream_compensation(tmp_path, "call_xai", env_var="XAI_API_BASE")
    assert comp_xai["compensated"] is True
    assert comp_xai["verdict"] == "falsified"

    comp_cohere = falsifier.check_upstream_compensation(tmp_path, "call_cohere", env_var="COHERE_API_BASE")
    assert comp_cohere["compensated"] is False
    assert comp_cohere["verdict"] == "uncompensated"


def test_filter_env_candidates_drops_compensated_branch(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "import os\n"
        "def rerank_other():\n"
        "    return os.getenv('XAI_API_BASE'), os.getenv('COHERE_API_BASE')\n\n"
        "def entry_wrapper(provider, api_base=None):\n"
        "    if provider == 'xai':\n"
        "        api_base = api_base or os.getenv('XAI_API_BASE')\n"
        "        return dispatch(provider, api_base=api_base)\n"
        "    return dispatch(provider, api_base=api_base)\n\n"
        "def dispatch(provider, api_base=None):\n"
        "    if provider == 'a':\n"
        "        api_base = api_base or os.getenv('A_API_BASE')\n"
        "        return send(api_base=api_base)\n"
        "    elif provider == 'b':\n"
        "        api_base = api_base or os.getenv('B_API_BASE')\n"
        "        return send(api_base=api_base)\n"
        "    elif provider == 'xai':\n"
        "        k = os.getenv('XAI_API_KEY')\n"
        "        return send(api_base=api_base, key=k)\n"
        "    elif provider == 'cohere':\n"
        "        k = os.getenv('COHERE_API_KEY')\n"
        "        return send(api_base=api_base, key=k)\n",
        encoding="utf-8",
    )
    raw = siblings.scan_siblings(tmp_path, include_ts=False)
    env_cands = [c for c in raw if c.evidence.get("rule") == "env-asymmetry"]
    assert {c.evidence["env_var"] for c in env_cands} == {"XAI_API_BASE", "COHERE_API_BASE"}

    # When filtered on the entry wrapper calling dispatch with XAI_API_BASE resolved in the xai arm:
    # check_upstream_compensation detects XAI_API_BASE upstream in entry_wrapper, leaving COHERE_API_BASE!
    surviving, falsified = falsifier.filter_env_candidates(tmp_path, env_cands)
    assert len(falsified) == 1
    assert falsified[0]["env_var"] == "XAI_API_BASE"
    assert len(surviving) == 1
    assert surviving[0].evidence["env_var"] == "COHERE_API_BASE"


def test_audit_test_vacuity_catches_all_six_antipatterns(tmp_path: Path) -> None:
    bad = tmp_path / "test_bad.py"
    bad.write_text(
        "from unittest.mock import patch\n\n"
        "def self_check():\n"
        "    return []\n\n"
        "def test_no_assert():\n"
        "    x = 1 + 1\n\n"
        "def test_tautology():\n"
        "    x = 42\n"
        "    assert x == x\n\n"
        "def test_swallowed():\n"
        "    try:\n"
        "        assert 1 == 2\n"
        "    except Exception:\n"
        "        pass\n\n"
        "def test_caplog_text(caplog):\n"
        "    assert 'KeyError' in caplog.text\n\n"
        "def test_mocked():\n"
        "    with patch('mod.real_fn') as m:\n"
        "        m.assert_called_once()\n",
        encoding="utf-8",
    )
    res = falsifier.audit_test_vacuity(bad, target_symbol="real_fn")
    assert res["vacuous"] is True
    kinds = {f["kind"] for f in res["findings"]}
    assert kinds == {
        "empty-self-check",
        "no-assertions",
        "tautology-assert",
        "swallowed-assertion",
        "loose-caplog-text",
        "target-mocked",
    }

    good = tmp_path / "test_good.py"
    good.write_text(
        "def real_fn(x):\n"
        "    return x + 1\n\n"
        "def self_check():\n"
        "    if real_fn(1) != 2:\n"
        "        return ['broken']\n"
        "    return []\n\n"
        "def test_real(caplog):\n"
        "    assert real_fn(2) == 3\n"
        "    assert not any(r.levelno >= 40 for r in caplog.records)\n",
        encoding="utf-8",
    )
    good_res = falsifier.audit_test_vacuity(good, target_symbol="real_fn")
    assert good_res["vacuous"] is False
    assert good_res["findings"] == []


def test_playbooks_list_and_query() -> None:
    entries = playbooks.list_playbooks()
    assert len(entries) == 5
    ids = {e["id"] for e in entries}
    assert ids == {
        "sibling-asymmetry",
        "parser-metadata",
        "split-brain-dual-path",
        "lifecycle-missing-key",
        "fake-green-tests",
    }
    pb = playbooks.get_playbook("COHERE_API_BASE env asymmetry")
    assert pb["id"] == "sibling-asymmetry"
    assert "Mandatory Falsification" in pb["content"]

    pb2 = playbooks.get_playbook("caplog")
    assert pb2["id"] == "fake-green-tests"
