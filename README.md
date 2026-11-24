# bug-vet

**Vet silent contract bugs with executable red/green proof.**

Zero-dependency structural discovery oracles, adversarial falsification, fail-closed evidence ledger, SARIF 2.1.0 reporting, and a pure-stdlib MCP + CLI dual surface (`v0.3.0`).

## Features

- **Structural Discovery Oracles**:
  - `siblings`: ABC subclass kwarg omission, `<entity>_to_<provider>` missing-guard asymmetry, `*_API_BASE` vs `*_API_KEY` dispatch asymmetry, and TS/JS sibling command validator omissions.
  - `parser_meta`: naive code-fence stripping / hardcoded triple-backtick regexes, incomplete SSE/line-ending terminator sets, unescaped Markdown markers in parse+serialize modules, and unsafe `.get(k, {}).items()` deserializers.
  - `contracts`: `Sequence`/`Mapping` narrowed to `list`/`dict`, idempotent `register*` self-eviction, and unprotected `coordinator.data[key]` subscripts.
  - `roundtrip` & `claims`: self-checked inverse-pair round-trip probes, 12-family contract mining, fenced doc example runner, and Python/TS callsites.
- **Adversarial Falsifier**:
  - Upstream call-graph compensation checker (`bug-vet falsify --check-compensation`).
  - Python AST vacuous-test/probe auditor catching `no-assertions`, `tautology-assert`, `swallowed-assertion`, `loose-caplog-text`, `target-mocked`, and `empty-self-check`.
- **Compiled Playbooks**: 5 structured bug-family playbooks (`bug-vet playbook --list` / `bv_playbook`).
- **Evidence Ledger & Gate**: `doc < probe < test < published < runtime` authority ladder, mandatory `self_check()`, explicit `falsified` records, and fail-closed `ready / partial / not-ready` gating.
- **Reporting & ContractBench**: `REPORT.md`, `findings.json`, SARIF 2.1.0 (`results.sarif`), `PR.md`, and the 12-case `ContractBench` suite (`bug-vet bench`).

## ContractBench Snapshot (12 Real Merged OSS Bugs)

- **Pre-fix Recall@5**: **10 / 12 (83.3%)** across `instructor`, `litellm`, `pr-agent`, `headroom`, `DeepTutor`, `haystack`, `mujoco`, `home-assistant/core`, and `testsprite-cli` (**7 / 12 at Rank #1**, all 12 within Top-8).
- **Post-fix negative control**: **11 / 11** structural detections clear on the fix commit. See [BENCHMARK.md](bench/reports/BENCHMARK.md).

## Quickstart

```bash
pip install -e ".[dev]"
pytest -v

bug-vet scan --mode all path/to/repo --name myrun
bug-vet playbook --list
bug-vet mcp
bug-vet setup --client all
```

## Run Storage

Artifacts are stored under `.bug-vet/runs/<run>/` in the current working directory (override with `--runs-dir` or `BUG_VET_RUNS_DIR`).

## License

MIT — see [LICENSE](LICENSE).
