# Playbook: Sibling Implementation & Env-Var Asymmetry

## Trigger Signals
- A class hierarchy with 3+ sibling subclasses implements the same method and calls a shared constructor/DTO, where most siblings populate fields like line counts or metadata and a minority omit them.
- A family of `<entity>_to_<provider>` encoders handles missing data in most siblings (by raising an error or extracting inline payloads) while one sibling returns a dictionary containing `None`.
- A multi-provider dispatch chain (`if/elif provider == ...`) reads `X_API_BASE` in sibling arms, while one branch reads `P_API_KEY` and forwards `api_base` unchanged even though `P_API_BASE` exists elsewhere in the codebase.
- Sibling CLI command handlers check overlapping option rules manually instead of calling the shared validator imported by peer commands.
- Type annotations promise `Sequence` / `Mapping`, while runtime checks narrow to concrete `list` / `dict`.

## Probe Recipe
1. Feed identical boundary inputs across all sibling implementations: empty strings, inline `data:` URIs, mixed-case URL schemes (`HTTPS://`), tuple inputs where `Sequence` is advertised, and non-default env overrides.
2. Assert behavioral parity on error-vs-payload and populated metadata fields: record both the failing sibling and the passing sibling outputs.
3. Preserve default behavior byte-for-byte when the new fallback or env override is unset.

## Mandatory Falsification & Pitfalls
1. **Treating raw leaf scans as bug lists (95%+ false-positive trap).** Leaf modules often omit env/fallback reads because an upstream router, shared helper, or base class resolves them first. Always walk callers up 2-3 hops (`check_upstream_compensation`) before confirming an env asymmetry.
2. **Assuming asymmetry is intentional without checking callers.** Verify at least 3 layers: (a) dispatch callers, (b) shared helper modules, (c) sibling branches in the same API surface. Only when all layers leave the fallback missing is the asymmetry real.
3. **Fixing at the wrong layer.** Changing a shared low-level transformation when a specific dispatch branch omitted fallback alters unrelated callers and breaks default behavior. Patch at the asymmetric branch and prove the no-env/default path is unchanged.

## Verification Checklist
- Pre-fix probe FAILS on the minority sibling and PASSES on healthy siblings.
- Two-arm or three-arm regression test: override present (new route), override absent (default behavior unchanged), invalid override (fail fast).
- Known blind spots: dynamically constructed `getattr(os.environ, ...)` keys or reflection-based plugin loading.
