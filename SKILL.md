---
name: bug-vet
description: Vet silent correctness and contract bugs — code violating a stated or structural promise — and prove each with an executable repro, adversarial falsification, and red/green verification. Zero-dependency CLI and MCP tool surface.
---

# bug-vet

Find and vet bugs where code **breaks its own contract**, and prove it with executable red/green evidence.

**Zero unverified findings. Zero duplicate PRs. Zero maintainer slop.**

## Core Discipline: Hunter != Verifier

1. **No finding without a failing repro.** Structural candidates and doc claims are leads, never findings, until a self-checked probe or test proves them red.
2. **Every probe must carry `self_check()`.** Missing `self_check` fails closed to `unknown` (`missing-self-check`).
3. **Adversarial falsification before confirmation.** Always check upstream compensation (`bv_falsify(mode="compensation")`) and test vacuity (`bv_falsify(mode="vacuity")`), and record disproven hypotheses (`mode="record"`).
4. **Authority ladder is enforced.** `doc < probe < test < published < runtime`.
5. **Anti-Slop for open-source contributions.** Always check `CONTRIBUTING.md` and run `bv_gate(action="collide")` via issue timeline + keyword/file search before writing a fix.

## MCP Tools (9 Tools)

| Tool | Purpose |
|---|---|
| `bv_scan` | Run `claims`, `roundtrip`, `examples`, `callsites`, `siblings`, `parser_meta`, `contracts`, or `all` |
| `bv_playbook` | `list` or `get` compiled bug-family playbooks and falsification pitfalls |
| `bv_probe` | `scaffold` a new probe or `run` probes into `ledger.json` |
| `bv_falsify` | Run `compensation` trace, `vacuity` test/probe audit, or `record` falsification in ledger |
| `bv_shrink` | Delta-debug a failing JSON input (`BUG_VET_INPUT`) to its minimal counterexample |
| `bv_verify` | Take `baseline`, `verify` red-to-green delta, run repo `test_evidence`, or `diff` against `--pip-version` |
| `bv_ledger` | `show` readiness report, `add_obligation`, `promote_candidate`, or `falsify` |
| `bv_gate` | Run fail-closed `gate` (`ready` / `partial` / `not-ready`), `collide`, or `issues` |
| `bv_report` | Generate `REPORT.md`, `findings.json`, SARIF 2.1.0 `results.sarif`, and optional `PR.md` |

## Antigravity Plugin Bundle (`plugin/`)

`bug-vet setup --client antigravity --apply` installs the full plugin bundle (`3` skills: `bug-vet-hunt`, `bug-vet-verify`, `bug-vet-bench`; `2` subagents: `bug-vet-hunter`, read-only `bug-vet-falsifier`; `rules/AGENTS.md`; `bug-vet-mcp`; and `PreToolUse` vacuity/local-only guard hooks).

## CLI Workflow

```bash
bug-vet scan --mode all path/to/repo --name myrun
bug-vet playbook sibling-asymmetry
bug-vet ledger myrun --promote 1
bug-vet falsify --check-vacuity .bug-vet/runs/myrun/probes/my_probe.py
bug-vet probe myrun --cwd path/to/repo
bug-vet baseline myrun --cwd path/to/repo
# apply fix
bug-vet verify myrun --cwd path/to/repo
bug-vet gate myrun --require-verification
bug-vet report myrun --formats markdown,json,sarif
```
