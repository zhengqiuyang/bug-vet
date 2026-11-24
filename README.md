# bug-vet

**Vet silent contract bugs with executable red/green proof.**

Deterministic contract probes, evidence-authority ledger, fail-closed gating, and a zero-dependency MCP + CLI dual surface for coding agents.

> Milestone 1 (v0.1.0): Core package, upgraded evidence ledger, and stdio MCP server.

## Quickstart

```bash
pip install -e ".[dev]"
pytest -v

bug-vet --help
bug-vet mcp
bug-vet setup --client all
```

## Run Storage

By default, artifacts are written under `.bug-vet/runs/<run>/` in the current working directory (configurable via `--runs-dir` or `BUG_VET_RUNS_DIR`).

## License

MIT — see [LICENSE](LICENSE).
