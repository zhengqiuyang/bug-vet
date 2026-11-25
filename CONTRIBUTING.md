# Contributing to `bug-vet`

Thank you for helping make silent contract bugs easier to prove and harder to misreport!

## Development Setup

`bug-vet` has **zero runtime dependencies** (`dependencies = []` in `pyproject.toml`) and targets Python 3.10+.

```bash
git clone https://github.com/zhengqiuyang/bug-vet.git
cd bug-vet
python -m venv .venv
# Linux / macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q
```

## Core Architecture Rules

1. **Zero Runtime Dependencies**: Keep `src/bug_vet/` pure Python 3.10+ standard library only (`dev` extras are only for `pytest`).
2. **Hunter != Verifier**: Discovery oracles (`src/bug_vet/oracles/`) emit candidate leads only, never confirmed findings. Confirmation always goes through executable `self_check()` probes, falsification, and fail-closed `ledger` / `gate` checks.
3. **Cross-Platform I/O**: Use explicit `encoding="utf-8", errors="replace"` for file reads/writes and subprocess streams, forward-slash POSIX relative paths (`rel_posix`) inside artifacts/SARIF, and `sys.stdin.buffer` / `sys.stdout.buffer` in `mcp_server.py`.

## Adding a Structural Oracle Rule

1. Add the rule under `src/bug_vet/oracles/` (`siblings.py`, `parser_meta.py`, `contracts.py`, or a new module wired into `run_all_scans()` in `src/bug_vet/oracles/__init__.py`).
2. Every rule MUST include tests in `tests/test_structural_oracles.py` covering **both**:
   - **Pre-fix positive detection**: triggers on the broken contract pattern.
   - **Post-fix negative control**: clears cleanly (`[]`) once fixed, plus common intentional idioms that must not trigger false positives.
3. If the new rule family has known falsification traps, add or update the corresponding guide under `src/bug_vet/playbooks/`.

## Adding a `ContractBench` Case

Add historical merged fixes to `bench/cases.json` referencing public commit SHAs (`fix_ref` and `parent_ref`) and run:

```bash
bug-vet bench --cases bench/cases.json --case <your-case-id>
```

`bench` materializes source trees read-only via `git archive` into a separate cache directory so scanned repositories stay untouched.

## Pull Request Checklist

- [ ] `pytest -q` passes locally across all tests.
- [ ] `dependencies = []` in `pyproject.toml` remains untouched.
- [ ] Any new oracle rule includes both pre-fix detection and post-fix negative-control tests.
- [ ] Documentation (`README.md`, `CHANGELOG.md`, or `docs/USER_GUIDE.md`) is updated if CLI/MCP surfaces changed.
