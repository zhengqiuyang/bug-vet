# Playbook: Lifecycle Snapshot Shrinking & Comparator Blind Spots

## Trigger Signals
- Long-lived entities or subscribers created once at setup time, while their backing snapshot (`coordinator.data`, cache map, registry dict) refreshes periodically and may shrink when upstream items disappear.
- Unprotected bare subscripts like `self.coordinator.data[self.item_id]` inside properties or callbacks without `.get()`, membership checks, or `KeyError` guards.
- List/UI memoization comparators checking only `prev.length === next.length`, head element, and tail element — missing in-place mutations on middle elements.

## Probe Recipe
1. **Snapshot contraction probe**: create entity/consumer against a 2-key snapshot, then push a refreshed snapshot missing the entity's key. Assert the entity transitions cleanly to unavailable/last-known state instead of raising `KeyError` / `StopIteration`.
2. **Mid-list mutation probe**: feed two equal-length lists `[A, B_v1, C]` and `[A, B_v2, C]` whose first and last items are identical while the middle item changes; verify the comparator returns unequal (needs re-render/update).
3. **Sweep the whole module**: when one missing-key crash is found (e.g. `StopIteration` or `KeyError`), scan every sibling sensor/handler in the same file/integration for bare subscripts against the same snapshot source.

## Mandatory Falsification & Pitfalls
1. **Stopping after fixing one exception type.** `next(...)` without default (`StopIteration`) and `data[key]` (`KeyError`) are symptoms of the same lifecycle mismatch — search by mechanism, not exception name.
2. **Swallowing errors silently (`except KeyError: pass` returning stale data as valid).** Respect domain semantics (e.g. mark entity `available = False` or preserve explicit stale marker) rather than faking healthy state.
3. **Assuming setup cleanup removes stale entities synchronously.** Upstream payloads often drop rows between setup runs without recreating entities.

## Verification Checklist
- Pre-fix probe reproduces the crash or frozen middle item reliably.
- All sibling consumers of the same snapshot in the touched module are verified under the same contraction test.
