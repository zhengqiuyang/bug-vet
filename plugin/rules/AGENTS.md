# bug-vet Contract Audit Discipline

- **Hunter != Verifier**: Discovery candidates (`bv_scan` / `bug-vet scan`) and doc claims are leads only, never confirmed findings.
- **Executable Repro Required**: Never report a bug without a standalone probe defining top-level `def self_check()` that exits `1` cleanly (without an uncaught Python Traceback, which `runner` downgrades to `ERROR`).
- **Falsify Before Confirming**: Check upstream compensation (`bv_falsify`), surrounding tests, and documentation for intended/documented limitations before confirming any failure.
- **Fail-Closed Ledger & Gate**: Settle every promoted obligation (`probed-fail`, `probed-pass`, or `falsified`) and run `bv_gate` before generating `REPORT.md` or `results.sarif`.
- **Read-Only & Local-Only**: Keep scanned repositories clean by writing runs to `--runs-dir`, and never push branches, open PRs, or publish packages unless explicitly instructed.
