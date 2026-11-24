---
name: bug-vet-hunt
description: Use this skill when scanning a Python or TypeScript repository read-only with bug-vet to discover silent contract bugs, promote structural candidates, and write executable self_check() probes.
---

# bug-vet Contract Bug Hunting Workflow

Run discovery read-only and isolate all run artifacts outside the scanned repo (for example `--runs-dir D:\projects\_bench\runs`).

## Steps
1. **Scan all structural + claim oracles**:
   - MCP: `bv_scan(mode="all", run="<run>", path="<repo>", runs_dir="<runs_dir>")`
   - CLI: `bug-vet scan --mode all <repo> --name <run> --runs-dir <runs_dir>`
   Review ranked `candidates.json` (`siblings`, `parser_meta`, `contracts` rank above `claims`).
2. **Consult playbooks**:
   - `bv_playbook(action="get", query="<family>")` or `bug-vet playbook <family>` (`sibling-asymmetry`, `parser-metadata`, `split-brain-dual-path`, `lifecycle-missing-key`, `fake-green-tests`).
3. **Promote selected leads into `ledger.json`**:
   - MCP: `bv_ledger(action="promote_candidate", run="<run>", indices=[1], runs_dir="<runs_dir>")`
   - CLI: `bug-vet ledger <run> --promote 1 --runs-dir <runs_dir>`
4. **Build and execute isolated probes**:
   - Scaffold with `bv_probe(action="scaffold")` / `bug-vet new-probe <run> --claim <cid>`.
   - Ensure `self_check()` tests both equal/passing and mismatched/failing pairs, and `check(failures)` calls the real implementation without mocking the target symbol.
   - Audit with `bv_falsify(mode="vacuity")`, then run `bv_probe(action="run", ...)` using the target repo's `.venv` Python interpreter and `--cwd <repo>`.

## Validation
Confirm `probe-results.json` shows definite `PASS` or `FAIL` verdicts (`ERROR` means an uncaught Python crash in the probe and blocks confirmation).
