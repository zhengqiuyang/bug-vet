# bug-vet Plugin for Antigravity

Native Antigravity bundle packaging the `bug-vet` contract-bug hunting harness:

- **MCP Server (`bug-vet-mcp`)**: Exposes all 9 stdio `bv_*` tools (`bv_scan`, `bv_playbook`, `bv_ledger`, `bv_probe`, `bv_falsify`, `bv_shrink`, `bv_verify`, `bv_gate`, `bv_report`).
- **Skills (`3`)**: `bug-vet-hunt`, `bug-vet-verify`, and `bug-vet-bench` loaded on demand.
- **Subagents (`2`)**: `bug-vet-hunter` (discovery + probe execution) and `bug-vet-falsifier` (read-only skeptic checking docs, tests, vacuity, and upstream compensation).
- **Rules**: Always-on `rules/AGENTS.md` enforcing Hunter != Verifier and local-only discipline.
- **Safety Hooks (`2`)**:
  - `PreToolUse` on `write_file` / `edit_file`: blocks vacuous or missing-`self_check()` probes written, edited, or appended under `probes/`.
  - `PreToolUse` on `run_command`: forces confirmation before remote `git push` (including `git -C ... push`), `gh pr|issue|release create`, and package/email publishes unless `BUG_VET_ALLOW_REMOTE=1`.

> Install this bundle with `bug-vet setup --client antigravity --apply`; copying the directory by hand leaves `hooks.json` pointing at a bare `python` that may not exist on `PATH`.
