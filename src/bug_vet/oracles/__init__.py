"""Discovery and contract scanning oracles for bug-vet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bug_vet.oracles import claims, contracts, parser_meta, roundtrip, siblings
from bug_vet.oracles.base import Candidate, rel_posix
from bug_vet.paths import ensure_run

__all__ = [
    "Candidate",
    "claims",
    "contracts",
    "parser_meta",
    "roundtrip",
    "run_all_scans",
    "siblings",
]

ORACLE_PRIORITY = {
    "siblings": 0,
    "parser_meta": 0,
    "contracts": 0,
    "claims": 1,
}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def run_all_scans(
    run_name: str,
    target_path: str | Path,
    *,
    runs_dir: str | Path | None = None,
    include_ts: bool = True,
    apply_falsifier: bool = True,
    claim_tier: str = "A",
) -> dict[str, Any]:
    """Run all structural oracles plus claim scanning, filter false positives, and save ``candidates.json``."""
    from bug_vet import falsifier

    root = Path(target_path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"target path does not exist: {root}")
    run_path = ensure_run(run_name, runs_dir=runs_dir)

    raw_candidates: list[Candidate] = []
    raw_candidates.extend(siblings.scan_siblings(root, include_ts=include_ts))
    raw_candidates.extend(parser_meta.scan_parser_meta(root))
    raw_candidates.extend(contracts.scan_contracts(root))

    falsified_records: list[dict[str, Any]] = []
    if apply_falsifier:
        surviving, falsified_records = falsifier.filter_env_candidates(root, raw_candidates)
    else:
        surviving = raw_candidates

    claim_rows = claims.scan_claims(root, tier=claim_tier, max_per_family=20)
    for cl in claim_rows:
        surviving.append(
            Candidate(
                oracle="claims",
                family=cl.kind,
                file=rel_posix(Path(cl.source), root),
                line=cl.line,
                symbol=cl.symbol,
                detail=f"[{cl.kind}/{cl.tier}] {cl.text[:140]}",
                confidence="low" if cl.tier == "B" else "medium",
                evidence={"rule": "stated-claim", "claim_id": cl.id, "tier": cl.tier},
                playbook_id=None,
            )
        )

    deduped: list[Candidate] = []
    seen: set[tuple] = set()
    for cand in surviving:
        k = (cand.file, cand.line, cand.symbol, cand.family)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(cand)

    deduped.sort(
        key=lambda c: (
            ORACLE_PRIORITY.get(c.oracle, 9),
            CONFIDENCE_RANK.get(c.confidence, 9),
            c.file,
            c.line or 0,
        )
    )

    by_oracle: dict[str, int] = {}
    for cand in deduped:
        by_oracle[cand.oracle] = by_oracle.get(cand.oracle, 0) + 1

    payload = {
        "run": run_name,
        "target": str(root),
        "count": len(deduped),
        "by_oracle": by_oracle,
        "falsified_count": len(falsified_records),
        "falsified": falsified_records,
        "candidates": [c.to_dict() for c in deduped],
    }
    artifact = run_path / "candidates.json"
    artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload["artifact"] = str(artifact)
    return payload
