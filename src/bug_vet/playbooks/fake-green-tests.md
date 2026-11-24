# Playbook: Fake-Green Tests, Vacuous Probes & Phase-0 Repo Admission

## Trigger Signals
- Tests asserting substrings against `caplog.text` (matching accidental DEBUG/INFO noise locally while failing on CI).
- Tests creating mocks whose return values bypass the real code path under test, or asserting only `mock.called` / `assert_called()` while patching out the target function itself.
- Standalone probes missing `self_check()`, empty `self_check()` functions, tautological `assert True` / `x == x` checks, or assertions swallowed inside broad `except Exception: pass`.
- Unvetted open-source PR targets with hidden collaborator-only restrictions, triage bots that auto-generate competing PRs within hours, or existing linked PRs hidden in the issue timeline.

## Probe & Audit Recipe
1. **Run `audit_test_vacuity`** (via `bv_falsify(mode="vacuity")` or `bug-vet falsify --check-vacuity`) on every new test and probe before accepting green status.
2. **Delete-the-fix sanity test**: temporarily revert the patch (or run against `baseline`) and verify the new test actually turns RED with the expected failure stack — never accept a test that stays green without the fix.
3. **Phase-0 Repo Admission** (contribution mode only):
   - Check `CONTRIBUTING.md` and recent merged PR authors (high star/throughput counts do not prove external PRs are accepted).
   - Run `bv_gate(action="collide")` over issue timeline cross-references plus open/merged PR keywords and target filenames.
   - Check for triage/auto-fix bots in issue comments — when official bots turn detailed issues into PRs automatically, file a clear diagnostic issue instead of competing with a duplicate PR.

## Mandatory Falsification & Pitfalls
1. **Trusting `caplog.text` substring matches.** Always assert on structured `caplog.records` filtered by `levelno >= ERROR` and `record.getMessage()`.
2. **Testing mock wiring instead of production paths.** Obtain mocks from top-level fixtures, assert call argument shapes, and confirm diff-level branch coverage hits the changed lines.
3. **Accepting `INCONCLUSIVE` or untested `self_check`.** Fail-closed ledger rules keep `unknown` as `unknown` — never aggregate missing checks to `ready`.

## Verification Checklist
- `audit_test_vacuity` reports `vacuous: false` with 0 findings.
- `baseline` (pre-fix) RED -> `verify` (post-fix) GREEN with zero regressions.
