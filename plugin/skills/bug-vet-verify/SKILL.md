---
name: bug-vet-verify
description: Use this skill when challenging suspected bugs adversarially, running red-to-green patch verification, checking fail-closed readiness gates, and generating REPORT.md or SARIF 2.1.0 outputs.
---

# bug-vet Falsification, Gate & Reporting Workflow

Verify every lead against documentation, upstream callers, and fail-closed ledger gates before emitting findings.

## Steps
1. **Adversarial challenge**:
   - Check upstream env compensation: `bv_falsify(mode="compensation", ...)` or `bug-vet falsify --check-compensation`.
   - Check probe/test vacuity: `bv_falsify(mode="vacuity", ...)` or `bug-vet falsify --check-vacuity`.
   - Inspect target docs and tests; record disproven leads via `bv_falsify(mode="record", ...)` (`false-positive`, `compensated-upstream`, `documented-limitation`, `intentional-design`).
2. **Minimize & verify fixes (when patching locally)**:
   - Delta-debug structured failing JSON inputs with `bv_shrink` (`BUG_VET_INPUT`).
   - Capture pre-fix baseline with `bv_verify(mode="baseline")` / `bug-vet baseline`, apply minimal patch, verify flip via `bv_verify(mode="verify")` / `bug-vet verify`, and record repo tests with `bv_verify(mode="test_evidence")`.
3. **Evaluate gate & export reports**:
   - Check `bv_gate(action="gate")` / `bug-vet gate <run>` until `state == "ready"` (`active=0`, `unknown=0`).
   - Generate outputs via `bv_report(run="<run>", formats=["markdown", "json", "sarif"], include_candidates=True)` / `bug-vet report <run> --formats markdown,json,sarif --include-candidates`.

## Validation
Verify `gate.json` reports `"state": "ready"` and `results.sarif` suppresses warnings for obligations already settled in `ledger.json`.
