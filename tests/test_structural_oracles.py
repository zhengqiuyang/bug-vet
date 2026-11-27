from __future__ import annotations

from pathlib import Path

from bug_vet.oracles import contracts, parser_meta, run_all_scans, siblings


def test_siblings_abc_kwarg_asymmetry_pre_and_post_fix(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text(
        "class ItemInfo:\n"
        "    def __init__(self, path, plus=0):\n"
        "        self.path = path\n"
        "        self.plus = plus\n\n"
        "class Provider:\n"
        "    def get_items(self): raise NotImplementedError\n",
        encoding="utf-8",
    )
    (tmp_path / "p_one.py").write_text(
        "from base import Provider, ItemInfo\n"
        "class One(Provider):\n"
        "    def get_items(self):\n"
        "        return [ItemInfo('a.py', plus=3)]\n",
        encoding="utf-8",
    )
    (tmp_path / "p_two.py").write_text(
        "from base import Provider, ItemInfo\n"
        "class Two(Provider):\n"
        "    def get_items(self):\n"
        "        return [ItemInfo('b.py', plus=5)]\n",
        encoding="utf-8",
    )
    p3 = tmp_path / "p_three.py"
    p3.write_text(
        "from base import Provider, ItemInfo\n"
        "class Three(Provider):\n"
        "    def get_items(self):\n"
        "        return [ItemInfo('c.py')]\n",
        encoding="utf-8",
    )

    cands = siblings.scan_siblings(tmp_path, include_ts=False)
    assert len(cands) == 1
    assert cands[0].file == "p_three.py"
    assert cands[0].symbol == "get_items"
    assert "plus" in cands[0].evidence["missing_kwargs"]

    # Post-fix clears to 0
    p3.write_text(
        "from base import Provider, ItemInfo\n"
        "class Three(Provider):\n"
        "    def get_items(self):\n"
        "        return [ItemInfo('c.py', plus=1)]\n",
        encoding="utf-8",
    )
    assert siblings.scan_siblings(tmp_path, include_ts=False) == []


def test_siblings_provider_guard_and_env_and_ts_command_asymmetry(tmp_path: Path) -> None:
    (tmp_path / "enc_openai.py").write_text(
        "def image_to_openai(image):\n"
        "    if image.source.startswith(('http://', 'https://')):\n"
        "        return {'url': image.source}\n"
        "    if image.data:\n"
        "        return {'data': image.data}\n"
        "    raise ValueError('missing')\n",
        encoding="utf-8",
    )
    (tmp_path / "enc_anth.py").write_text(
        "def image_to_anthropic(image):\n"
        "    if isinstance(image.source, str) and image.source.startswith(('http://', 'https://')):\n"
        "        image.data = 'fetched'\n"
        "    return {'source': {'data': image.data}}\n",
        encoding="utf-8",
    )
    (tmp_path / "dispatch.py").write_text(
        "import os\n"
        "def run_rerank():\n"
        "    return os.getenv('COHERE_API_BASE')\n"
        "def call_api(provider, api_base=None):\n"
        "    if provider == 'vllm':\n"
        "        api_base = api_base or os.getenv('VLLM_API_BASE')\n"
        "        return send(api_base=api_base)\n"
        "    elif provider == 'or':\n"
        "        api_base = api_base or os.getenv('OR_API_BASE')\n"
        "        return send(api_base=api_base)\n"
        "    elif provider == 'cohere':\n"
        "        key = os.getenv('COHERE_API_KEY')\n"
        "        return send(api_base=api_base, api_key=key)\n",
        encoding="utf-8",
    )
    cmds = tmp_path / "commands"
    cmds.mkdir()
    (cmds / "test.ts").write_text(
        "import { assertJUnitReportOptions } from '../lib/junit-report.js';\n"
        "export function validate(opts: any) { assertJUnitReportOptions(opts); }\n",
        encoding="utf-8",
    )
    (cmds / "testlist.ts").write_text(
        "import { buildJUnitReport } from '../lib/junit-report.js';\n"
        "export function validateList(opts: any) {\n"
        "  if (opts.report === 'junit') { return true; }\n"
        "}\n",
        encoding="utf-8",
    )

    cands = siblings.scan_siblings(tmp_path, include_ts=True)
    rules = {c.evidence["rule"] for c in cands}
    assert {"sibling-guard-asymmetry", "env-asymmetry", "ts-validator-asymmetry"} <= rules


def test_parser_meta_rules_detect_and_clear_on_fix(tmp_path: Path) -> None:
    f_fence = tmp_path / "fences.py"
    f_fence.write_text(
        "import re\n"
        "_WRAP_RE = re.compile(r'\\A```[^\\n]*\\n(.*?)```\\Z', re.S)\n"
        "def clean(s: str) -> str:\n"
        "    return s.strip('`')\n",
        encoding="utf-8",
    )
    f_sse = tmp_path / "sse.py"
    f_sse.write_text(
        'TERMINATORS = (b"\\n\\n", b"\\r\\n\\r\\n")\n',
        encoding="utf-8",
    )
    f_doc = tmp_path / "doc.py"
    f_doc.write_text(
        "import re\n"
        "_B = re.compile(r'^- (?P<text>.*?)(?P<m>(?:\\[\\^\\d+\\])*)$')\n"
        "def parse_doc(s): return s\n"
        "def serialize_doc(d): return str(d)\n",
        encoding="utf-8",
    )
    f_deser = tmp_path / "pipe.py"
    f_deser.write_text(
        "def _from_dict_impl(data):\n"
        "    return [(k, v) for k, v in data.get('components', {}).items()]\n",
        encoding="utf-8",
    )

    cands = parser_meta.scan_parser_meta(tmp_path)
    rules = {c.evidence["rule"] for c in cands}
    assert rules == {
        "naive-fence-strip",
        "hardcoded-triple-fence-regex",
        "incomplete-event-terminators",
        "unescaped-marker-regex",
        "unsafe-dict-get-items",
    }

    # Fix all 4 files and verify 0 findings remain
    f_fence.write_text(
        "import re\n"
        "_WRAP_RE = re.compile(r'\\A(?P<f>`{3,}|~{3,})[^\\n]*\\n(.*?)(?P=f)\\Z', re.S)\n"
        "def clean(s: str) -> str:\n"
        "    return s\n",
        encoding="utf-8",
    )
    f_sse.write_text(
        'TERMINATORS = (b"\\r\\n\\r\\n", b"\\r\\n\\n", b"\\n\\r\\n", b"\\n\\n", b"\\r\\r")\n',
        encoding="utf-8",
    )
    f_doc.write_text(
        "import re\n"
        "_B = re.compile(r'^- (?P<text>.*?)(?P<m>(?:(?<!\\\\)\\[\\^\\d+\\])*)$')\n"
        "def parse_doc(s): return s\n"
        "def serialize_doc(d): return str(d)\n",
        encoding="utf-8",
    )
    f_deser.write_text(
        "def _from_dict_impl(data):\n"
        "    comps = data.get('components', {})\n"
        "    if not isinstance(comps, dict):\n"
        "        raise ValueError('bad')\n"
        "    return list(comps.items())\n",
        encoding="utf-8",
    )
    assert parser_meta.scan_parser_meta(tmp_path) == []


def test_contracts_oracle_detects_type_narrowing_reregister_and_subscript(tmp_path: Path) -> None:
    f_seq = tmp_path / "seq.py"
    f_seq.write_text(
        "from typing import Sequence\n"
        "def rollout(data: Sequence[int]):\n"
        "    if not isinstance(data, list):\n"
        "        data = [data]\n"
        "    return data\n",
        encoding="utf-8",
    )
    f_reg = tmp_path / "reg.py"
    f_reg.write_text(
        "class Registry:\n"
        "    def __init__(self): self._s = {}\n"
        "    def register(self, handle):\n"
        "        existing = self._s.get(handle.id)\n"
        "        if existing is not None:\n"
        "            existing.tasks.clear()\n"
        "        self._s[handle.id] = handle\n",
        encoding="utf-8",
    )
    f_ha = tmp_path / "sensor.py"
    f_ha.write_text(
        "class Sensor:\n"
        "    def native_value(self):\n"
        "        return self.coordinator.data[self.mac]\n",
        encoding="utf-8",
    )

    cands = contracts.scan_contracts(tmp_path)
    rules = {c.evidence["rule"] for c in cands}
    assert rules == {"sequence-isinstance-list", "reregister-self-eviction", "bare-coordinator-subscript"}

    # Fix all 3
    f_seq.write_text(
        "from typing import Sequence\n"
        "def rollout(data: Sequence[int]):\n"
        "    if isinstance(data, int):\n"
        "        data = [data]\n"
        "    else:\n"
        "        data = list(data)\n"
        "    return data\n",
        encoding="utf-8",
    )
    f_reg.write_text(
        "class Registry:\n"
        "    def __init__(self): self._s = {}\n"
        "    def register(self, handle):\n"
        "        existing = self._s.get(handle.id)\n"
        "        if existing is handle:\n"
        "            return\n"
        "        if existing is not None:\n"
        "            existing.tasks.clear()\n"
        "        self._s[handle.id] = handle\n",
        encoding="utf-8",
    )
    f_ha.write_text(
        "class Sensor:\n"
        "    def _handle_update(self):\n"
        "        val = self.coordinator.data.get(self.mac)\n"
        "        return val\n",
        encoding="utf-8",
    )
    assert contracts.scan_contracts(tmp_path) == []


def test_run_all_scans_ranks_structural_above_claims(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from pkg.mod import parse_it\n", encoding="utf-8")
    (pkg / "mod.py").write_text(
        "from typing import Sequence\n"
        "def parse_it(items: Sequence[str]):\n"
        "    '''Lossless parser.'''\n"
        "    if not isinstance(items, list):\n"
        "        items = [items]\n"
        "    return items\n",
        encoding="utf-8",
    )
    (tmp_path / "user.py").write_text("from pkg import parse_it\n", encoding="utf-8")
    res = run_all_scans("all-run", tmp_path, runs_dir=tmp_path / "runs")
    assert res["count"] >= 2
    assert res["candidates"][0]["oracle"] == "contracts"
    assert (tmp_path / "runs" / "all-run" / "candidates.json").exists()


def test_v031_ignores_exception_kwargs_and_inplace_dict_redaction(tmp_path: Path) -> None:
    (tmp_path / "base_err.py").write_text(
        "class BaseSandbox:\n"
        "    def read(self): raise NotImplementedError\n\n"
        "class SandboxReadError(Exception):\n"
        "    def __init__(self, *, path, cause=None, retryable=None):\n"
        "        self.path, self.cause, self.retryable = path, cause, retryable\n\n"
        "class DockerSandbox(BaseSandbox):\n"
        "    def read(self): raise SandboxReadError(path='x', cause=OSError(), retryable=False)\n\n"
        "class CloudSandbox(BaseSandbox):\n"
        "    def read(self): raise SandboxReadError(path='y', cause=OSError(), retryable=False)\n\n"
        "class LocalSandbox(BaseSandbox):\n"
        "    def read(self): raise SandboxReadError(path='z')\n",
        encoding="utf-8",
    )
    (tmp_path / "client.py").write_text(
        "from collections.abc import Mapping\n\n"
        "def deserialize_state(payload: Mapping[str, object]) -> dict:\n"
        "    sanitized = dict(payload)\n"
        "    persisted = sanitized\n"
        "    if isinstance(payload, dict):\n"
        "        payload.clear()\n"
        "        payload.update(sanitized)\n"
        "        persisted = payload\n"
        "    return persisted\n\n"
        "def bad_narrowing(cfg: Mapping[str, object]) -> dict:\n"
        "    if isinstance(cfg, dict):\n"
        "        return dict(cfg)\n"
        "    raise TypeError('must be dict')\n",
        encoding="utf-8",
    )

    assert siblings.scan_siblings(tmp_path) == []
    contract_cands = contracts.scan_contracts(tmp_path)
    assert len(contract_cands) == 1
    assert contract_cands[0].symbol == "bad_narrowing"
    assert contract_cands[0].evidence["rule"] == "mapping-isinstance-dict"


def test_parser_meta_flags_literal_fence_checks_and_skips_filename_stripping(tmp_path: Path) -> None:
    f = tmp_path / "yaml_extract.py"
    f.write_text(
        "def _extract_labelled_yaml_block(text):\n"
        "    for line in text.splitlines():\n"
        "        stripped = line.strip()\n"
        "        if not (stripped.startswith('```yaml') or stripped.startswith('```yml')):\n"
        "            continue\n"
        "        if stripped == '```':\n"
        "            return 'ok'\n"
        "    return None\n"
        "def _resolve_diff_file_path(relevant_file):\n"
        "    return relevant_file.strip().strip('`')\n",
        encoding="utf-8",
    )
    cands = parser_meta.scan_parser_meta(tmp_path)
    fence_checks = [c for c in cands if c.evidence["rule"] == "literal-fence-check"]
    assert len(fence_checks) >= 3
    assert {c.evidence["fence"] for c in fence_checks} == {"backtick"}
    assert not any(c.evidence["rule"] == "naive-fence-strip" for c in cands)

    f.write_text(
        "def _extract_labelled_yaml_block(text):\n"
        "    for line in text.splitlines():\n"
        "        stripped = line.strip()\n"
        "        if not (stripped.startswith('```yaml') or stripped.startswith('~~~yaml')):\n"
        "            continue\n"
        "        if stripped == '```' or stripped == '~~~':\n"
        "            return 'ok'\n"
        "    return None\n",
        encoding="utf-8",
    )
    assert not [
        c for c in parser_meta.scan_parser_meta(tmp_path) if c.evidence["rule"] == "literal-fence-check"
    ]
