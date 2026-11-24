# Changelog

## v0.4.1 — 2026-11-24

Packaging and safety-hook hardening for the Antigravity plugin bundle.

### Fixed

- **Plugin bundle now ships with the package.** Moved `plugin/` to
  `src/bug_vet/plugin/` and added it to `package-data`, so `pip install bug-vet`
  (wheel or sdist) can run `bug-vet setup --client antigravity --apply`.
  `_default_plugin_source()` resolves the packaged copy first and still falls
  back to a repository-root `plugin/` for old checkouts.
- **Probe vacuity guard no longer misses edits.** The `PreToolUse` matcher now
  covers `write_file|edit_file|multi_edit_file|replace_file_content`; append
  writes and patch fragments are reconstructed against the on-disk probe before
  auditing. Path matching accepts any `probes/` directory segment.
- **Fewer false positives in `self_check()` auditing.** Ternary returns,
  helper-delegated checkers, comprehensions, and unittest-style assertions are
  no longer reported as `empty-self-check`.
- **Local-only guard covers more remote writes:** `git -C <path> push`,
  `git -c k=v push`, `git send-email`, `gh release create`, `hub push`,
  `npm/pnpm/yarn/bun publish`, `cargo/poetry/uv publish`, `dotnet nuget push`,
  and `nuget push`.
- **Hooks fail closed.** A guard that cannot parse or audit a probe/command now
  returns `force_ask`/`ask` with a reason instead of silently allowing it.
- **Timed-out or crashed probes are no longer dropped from counts.**
  `ERROR(<code>)` verdicts count as failures (`runner.is_failing_verdict`),
  are reported as `inconclusive` blockers, and never settle as a pass in
  red/green verification.

### Added

- GitHub Actions CI (pytest on Linux/Windows, Python 3.10/3.12, plus a wheel
  content check for the plugin bundle).
- Tests for edit/append hook coverage, extended remote-write patterns,
  legitimate `self_check()` styles, and `ERROR(<code>)` verdict handling.

## v0.4.0 — 2026-11-24

- Packaged `bug-vet` as a native Antigravity plugin: `mcp_config.json`,
  3 on-demand skills, `bug-vet-hunter` / read-only `bug-vet-falsifier`
  subagents, `rules/AGENTS.md`, and 2 `PreToolUse` hooks.

## v0.3.1

- Hardened runner/oracles/falsifier, added CLI `ledger --promote`, and wrote the
  Chinese user guide.

## v0.3.0

- Structural oracles, falsifier, playbooks, SARIF reporter, and the 12-case
  ContractBench.

## v0.1.0

- Initial bug-vet core package, evidence ledger, and stdio MCP server.
