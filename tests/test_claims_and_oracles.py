from __future__ import annotations

import json
import sys
from pathlib import Path

from bug_vet import ledger as ledger_mod, runner
from bug_vet.oracles import claims as claims_oracle, roundtrip as roundtrip_oracle


def test_claim_families_and_negations() -> None:
    cases = {
        "Lossless round-trip encoding.": "roundtrip",
        "Must be thread-safe for concurrent readers.": "concurrency",
        "Raises ValueError for invalid keys.": "error_contract",
        "Returns None when the item is missing.": "return_shape",
        "Ordering is preserved across updates.": "ordering",
        "Headers are forwarded as-is to upstream.": "passthrough",
        "Validates the payload before saving.": "validation",
        "Size is bounded by max_items.": "bounds",
    }
    for text, expected in cases.items():
        found = [kind for kind, _sentence, _offset in claims_oracle.claims_in(text)]
        assert expected in found, f"expected {expected!r} in {found!r} for {text!r}"

    negated = [
        "This operation is not idempotent.",
        "Serialization is never lossless for custom objects.",
        "This cache is not thread-safe.",
    ]
    for text in negated:
        assert claims_oracle.claims_in(text) == []


def test_fence_stripping_handles_backticks_tildes_and_doctests() -> None:
    text = (
        "Raises ValueError on bad input.\n\n"
        "```python\n"
        "raises ValueError\n"
        "```\n\n"
        "~~~~markdown\n"
        "```python\n"
        "lossless round-trip inside nested tilde fence\n"
        "```\n"
        "~~~~\n\n"
        ">>> deterministic output here\n"
        "... reversible\n"
    )
    found = claims_oracle.claims_in(text)
    assert len(found) == 1
    assert found[0][0] == "error_contract"

    fenced_only = (
        "````markdown\n"
        "```python\n"
        "lossless round-trip\n"
        "```\n"
        "````\n"
    )
    assert claims_oracle.claims_in(fenced_only) == []


def test_package_init_reachability(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        '"""Package."""\n\n\ndef helper():\n    """Lossless round-trip."""\n',
        encoding="utf-8",
    )
    (pkg / "lazy.py").write_text('def f():\n    """Idempotent helper."""\n', encoding="utf-8")
    (tmp_path / "user.py").write_text(
        "from pkg import helper\nfrom pkg.lazy import (\n    f,\n)\n",
        encoding="utf-8",
    )
    claims = claims_oracle.scan_claims(tmp_path, tier="all")
    symbols = {c.symbol for c in claims}
    assert {"helper", "f"} <= symbols
    assert all(c.reachable for c in claims)


def test_examples_runner_handles_fences_and_classifies(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Demo\n\n"
        "```python\n"
        "import json\n"
        "assert json.loads('{\"a\": 1}')['a'] == 1\n"
        "```\n\n"
        "~~~python\n"
        "import sys\n"
        "raise RuntimeError('broken example')\n"
        "~~~\n\n"
        "```python\n"
        "import os\n"
        "print(undefined_var)\n"
        "```\n\n"
        "```python\n"
        "print('snippet without import')\n"
        "```\n",
        encoding="utf-8",
    )
    res = claims_oracle.run_examples(
        "ex-run",
        readme,
        runs_dir=tmp_path / "runs",
        python=sys.executable,
        cwd=tmp_path,
    )
    assert res["total"] == 4
    assert res["counts"] == {"PASS": 1, "FAIL": 1, "FRAGMENT": 1, "SKIP": 1}


def test_roundtrip_oracle_generates_self_checked_probe_and_catches_loss(tmp_path: Path) -> None:
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "codec.py").write_text(
        "import json\n\n"
        "def encode_payload(text: str) -> str:\n"
        "    return json.dumps({'v': text})\n\n"
        "def decode_payload(raw: str) -> str:\n"
        "    # intentional bug: truncates output on long strings\n"
        "    val = json.loads(raw)['v']\n"
        "    return val[:5] if len(val) > 20 else val\n",
        encoding="utf-8",
    )

    runs_dir = tmp_path / "runs"
    gen = roundtrip_oracle.generate_roundtrip_probes(
        "rt-run",
        pkg,
        import_root=tmp_path,
        runs_dir=runs_dir,
    )
    assert gen["count"] == 1
    probe_path = Path(gen["generated"][0]["probe_path"])
    assert runner.has_self_check(probe_path)

    run_res = runner.run_probes(
        "rt-run",
        runs_dir=runs_dir,
        python=sys.executable,
        cwd=tmp_path,
        env_pairs=[f"PYTHONPATH={tmp_path}"],
    )
    assert run_res["count"] == 1
    assert run_res["results"][0]["verdict"] == "FAIL"
    assert run_res["results"][0]["has_self_check"] is True

    state = ledger_mod.load(runs_dir / "rt-run")
    assert len(state["obligations"]) == 1
    assert state["obligations"][0]["disposition"] == "probed-fail"


def test_callsites_python_unwired_and_ts(tmp_path: Path) -> None:
    (tmp_path / "lib.py").write_text(
        "def connect(url, timeout=10, *, strict=False, retries=1):\n"
        "    return url, timeout, strict, retries\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        "from lib import connect\n"
        "connect('https://example.com', timeout=5)\n",
        encoding="utf-8",
    )
    (tmp_path / "web.ts").write_text(
        "export function renderCard(title: string) { return title; }\n"
        "const out = renderCard('hi');\n",
        encoding="utf-8",
    )

    runs_dir = tmp_path / "runs"
    py_res = claims_oracle.scan_callsites("cs-py", tmp_path, "connect", runs_dir=runs_dir)
    assert len(py_res["calls"]) == 1
    assert set(py_res["definitions"][0]["unwired"]) == {"strict", "retries"}

    ts_res = claims_oracle.scan_callsites("cs-ts", tmp_path, "renderCard", runs_dir=runs_dir, lang="ts")
    assert len(ts_res["definitions"]) == 1
    assert len(ts_res["calls"]) == 1
    assert (runs_dir / "cs-ts" / "callsites-results.json").exists()
    data = json.loads((runs_dir / "cs-ts" / "callsites-results.json").read_text(encoding="utf-8"))
    assert data["language"] == "ts"
