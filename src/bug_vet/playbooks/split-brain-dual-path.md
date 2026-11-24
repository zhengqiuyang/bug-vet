# Playbook: Split-Brain Readers, Dual-Path State Leaks & Re-Registration

## Trigger Signals
- One business fact (token usage, session state, active count, config option) computed differently by two modules because they read different fields (for example inner chunk metadata vs outer envelope).
- Round-scoped or session-scoped cumulative buffers reset on normal completion or manual cancel, while a second entry path (auto-continue, reconnect, retry) bypasses the reset boundary.
- Idempotent `register(handle)` / `attach()` methods looking up `existing = self._store.get(id)` and clearing `existing`'s live task/resource list whenever `existing is not None`, without first guarding `if existing is handle: return`.

## Probe Recipe
1. **Enumerate all read & write sites** for the disputed state field across the package before writing code.
2. **Three-arm matrix for split-brain readers**:
   - Only primary/inner field present -> returns inner value.
   - Only fallback/outer field present -> returns fallback value.
   - Both present with differing values -> deterministic precedence wins cleanly without error.
3. **Dual-path lifecycle test**: trigger the sequence across BOTH main path and secondary path (manual abort + auto-resume, or different handle + exact same handle re-registration) and assert no leaked state or dropped references.

## Mandatory Falsification & Pitfalls
1. **Blaming arithmetic before checking data sources.** When two components compute different numbers for the same concept, verify both read the exact same canonical helper first.
2. **Patching only the reported entry path.** For round/session leaks, there are almost always `>= 2` write/re-entry paths; fixing only one leaves the twin bug open.
3. **Patching at consumers instead of a shared accessor.** Extract a single canonical getter or unified boundary reset; do not sprinkle ad-hoc fallbacks at call sites.
4. **Hiding disproven hypotheses.** If a suspected split-brain or env hypothesis turns out to be compensated or intentional, record `bug-vet falsify` in the ledger — documented falsifications build maintainer trust.

## Verification Checklist
- Three-arm test or dual-path probe fails on unpatched tree and passes on all arms after fix.
- Identity re-registration leaves attached resource counts unchanged (`N -> N`), while distinct handle replacement still cleans up old tasks.
