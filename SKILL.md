---
name: bug-vet
description: Vet silent correctness and contract bugs — code violating a stated or structural promise — and prove each with an executable repro, adversarial falsification, and red/green verification. Zero-dependency CLI and MCP tool surface.
---

# bug-vet

Find and vet bugs where code **breaks its own contract**, and prove it with executable red/green evidence.

**Zero unverified findings. Zero duplicate PRs. Zero maintainer slop.**

## Core Discipline: Hunter != Verifier

1. **No finding without a failing repro.** "Looks wrong" is a hypothesis, never a finding.
2. **Every probe must carry `self_check()`.** A probe without a `self_check` function cannot close an obligation (`missing-self-check` -> `unknown`).
3. **Authority ladder is enforced.** `doc < probe < test < published < runtime`. Evidence below an obligation's `required_observation` cannot settle it.
4. **Fail-closed gating.** `INCONCLUSIVE`, `CHECKER_BUG`, `ERROR`, environment drift, contradictions, and unbound failing probes never aggregate to `ready`.
5. **Anti-Slop for open-source contributions.** Always run `bv_gate(action="collide")` (or `bug-vet collide`) via issue timeline + keyword/file search before writing a fix, and check `CONTRIBUTING.md`.

## MCP Tools (Dual Surface)

Use `bug-vet mcp` (`uvx bug-vet mcp` or `.venv/Scripts/python.exe -m bug_vet mcp`):

| Tool | Purpose |
|---|---|
| `bv_scan` | Run `claims`, `roundtrip`, `examples`, or `callsites` scans |
| `bv_probe` | `scaffold` a new probe or `run` existing probes into `ledger.json` |
| `bv_shrink` | Delta-debug a failing JSON input (`BUG_VET_INPUT`) to its minimal counterexample |
| `bv_verify` | Take `baseline`, `verify` red-to-green delta, run repo `test_evidence` (`test` authority), or `diff` against `--pip-version` (`published` authority) |
| `bv_ledger` | `show` readiness report, `add_obligation`, or record `falsify` for disproven hypotheses |
| `bv_gate` | Run fail-closed `gate` (`ready` / `partial` / `not-ready`), `collide`, or `issues` |

## Probe Protocol

Standalone Python scripts under `<runs_dir>/<run>/probes/`:

- Declare `CLAIM = "c-0001"` (or `OBLIGATION = "ob-..."`) at module level.
- Define `def self_check() -> list[str]:` validating the checker on a known-good and known-bad input first; exit `3` (`CHECKER_BUG`) if non-empty.
- Exit codes: `0` PASS, `1` FAIL, `2` INCONCLUSIVE, `3` CHECKER_BUG.
- For shrinking, read JSON payload path from `BUG_VET_INPUT` (or `BUGCHECKUP_INPUT`), exit `1` when the failure still reproduces.

## CLI Workflow

```bash
bug-vet scan --mode claims path/to/pkg --name myrun
bug-vet scan --mode roundtrip path/to/pkg --name myrun
bug-vet probe myrun --cwd path/to/repo
bug-vet baseline myrun --cwd path/to/repo
# apply fix
bug-vet verify myrun --cwd path/to/repo
bug-vet test-evidence myrun --obligation ob-c-0001 --cmd "pytest -q" --cwd path/to/repo
bug-vet gate myrun --require-verification
```
