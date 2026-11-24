"""ContractBench: non-invasive pre-fix recall & post-fix negative-control benchmark runner."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from bug_vet import falsifier
from bug_vet.oracles import claims, contracts, parser_meta, siblings
from bug_vet.oracles.base import Candidate, rel_posix

TOP_K = 5
ORACLE_ORDER = {"siblings": 0, "parser_meta": 0, "contracts": 0, "claims": 1}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def default_cache_dir() -> Path:
    env_val = os.environ.get("BUG_VET_BENCH_CACHE", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    if Path("D:/projects").exists():
        return Path("D:/projects/_bench/bug-vet-trees").resolve()
    return (Path.cwd() / ".bug-vet" / "bench-cache").resolve()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def materialize_ref(
    repo: str | Path,
    ref: str,
    paths: list[str] | None,
    cache_dir: Path,
    case_id: str,
) -> Path:
    """Extract ``paths`` at ``ref`` via read-only ``git archive`` without altering ``repo``."""
    repo_path = Path(repo).resolve()
    if not (repo_path / ".git").exists():
        raise FileNotFoundError(f"not a git repository: {repo_path}")

    resolved_sha = _git(repo_path, "rev-parse", ref)
    dest = cache_dir / case_id / resolved_sha[:12]
    marker = dest / ".extracted.ok"
    if marker.exists():
        return dest

    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "-C", str(repo_path), "archive", resolved_sha]
    if paths:
        cmd.append("--")
        cmd.extend(paths)
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git archive {resolved_sha} failed: {err}")

    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r|") as archive:
        archive.extractall(dest, filter="data")
    marker.write_text(resolved_sha + "\n", encoding="utf-8")
    return dest


def _collect_discovery_candidates(tree: Path, *, claim_tier: str = "A") -> tuple[list[Candidate], list[dict[str, Any]]]:
    raw: list[Candidate] = []
    raw.extend(siblings.scan_siblings(tree, include_ts=True))
    raw.extend(parser_meta.scan_parser_meta(tree))
    raw.extend(contracts.scan_contracts(tree))
    surviving, falsified = falsifier.filter_env_candidates(tree, raw)

    for cl in claims.scan_claims(tree, tier=claim_tier, max_per_family=25):
        surviving.append(
            Candidate(
                oracle="claims",
                family=cl.kind,
                file=rel_posix(Path(cl.source), tree),
                line=cl.line,
                symbol=cl.symbol,
                detail=f"[{cl.kind}/{cl.tier}] {cl.text[:140]}",
                confidence="low" if cl.tier == "B" else "medium",
                evidence={"rule": "stated-claim", "claim_id": cl.id, "tier": cl.tier},
            )
        )

    deduped: list[Candidate] = []
    seen: set[tuple] = set()
    for cand in surviving:
        k = (cand.oracle, cand.file, cand.line, cand.symbol, cand.family)
        if k not in seen:
            seen.add(k)
            deduped.append(cand)

    deduped.sort(
        key=lambda c: (
            ORACLE_ORDER.get(c.oracle, 9),
            CONFIDENCE_ORDER.get(c.confidence, 9),
            c.file,
            c.line or 0,
        )
    )
    return deduped, falsified


def _matches_target(cand: Candidate, target_files: set[str], target_symbol: str | None) -> bool:
    norm_file = cand.file.replace("\\", "/")
    return norm_file in target_files


def _hit_signature(cand: Candidate) -> tuple:
    return (
        cand.oracle,
        cand.file.replace("\\", "/"),
        cand.symbol,
        cand.evidence.get("rule"),
        cand.evidence.get("env_var"),
        cand.evidence.get("param"),
    )


def evaluate_case(case: dict[str, Any], cache_dir: Path | None = None) -> dict[str, Any]:
    """Run pre-fix discovery and post-fix negative control for one ContractBench case."""
    c_dir = cache_dir or default_cache_dir()
    repo = Path(case["repo"])
    fix_ref = case["fix_ref"]
    parent_ref = case.get("parent_ref") or f"{fix_ref}^"
    paths = case.get("paths")
    target_files = {f.replace("\\", "/") for f in case["target"]["files"]}
    target_symbol = case["target"].get("symbol")
    claim_tier = case.get("claim_tier", "A")

    pre_tree = materialize_ref(repo, parent_ref, paths, c_dir, case["id"])
    post_tree = materialize_ref(repo, fix_ref, paths, c_dir, case["id"])

    pre_cands, pre_falsified = _collect_discovery_candidates(pre_tree, claim_tier=claim_tier)
    post_cands, post_falsified = _collect_discovery_candidates(post_tree, claim_tier=claim_tier)

    # Deduplicated location keys for ranking
    ordered_keys: list[tuple] = []
    for c in pre_cands:
        k = c.key()
        if k not in ordered_keys:
            ordered_keys.append(k)

    pre_hits = [c for c in pre_cands if _matches_target(c, target_files, target_symbol)]
    first_rank: int | None = None
    if pre_hits:
        first_rank = ordered_keys.index(pre_hits[0].key()) + 1

    # Negative-control comparison: count how many pre-fix target signatures cleared on fix_ref
    pre_structural_hits = [c for c in pre_hits if c.oracle != "claims"]
    firing_oracles = sorted({c.oracle for c in pre_hits})
    pre_sigs = {_hit_signature(c) for c in (pre_structural_hits or pre_hits)}
    post_sigs = {_hit_signature(c) for c in post_cands}

    cleared_sigs = pre_sigs - post_sigs
    remaining_sigs = pre_sigs & post_sigs
    post_clean = bool(pre_structural_hits) and bool(cleared_sigs)
    post_full_clean = bool(pre_hits) and len(remaining_sigs) == 0

    return {
        "id": case["id"],
        "repo": Path(case["repo"]).name,
        "fix_ref": fix_ref,
        "parent_ref": parent_ref,
        "why": case.get("why", ""),
        "pre_fix": {
            "total_candidates": len(pre_cands),
            "falsified_filtered": len(pre_falsified),
            "target_hits": len(pre_hits),
            "has_structural_hit": bool(pre_structural_hits),
            "rank_of_first_hit": first_rank,
            "recall_at_5": bool(first_rank is not None and first_rank <= TOP_K),
            "firing_oracles": firing_oracles,
            "top_hit": pre_hits[0].to_dict() if pre_hits else None,
        },
        "post_fix": {
            "total_candidates": len(post_cands),
            "falsified_filtered": len(post_falsified),
            "cleared_signatures": len(cleared_sigs),
            "remaining_signatures": len(remaining_sigs),
            "same_rule_hits": len(remaining_sigs),
            "cleared": post_clean,
            "fully_cleared": post_full_clean,
        },
    }


def render_benchmark_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# ContractBench Results",
        "",
        f"- **Cases evaluated**: {s['total_cases']}",
        f"- **Pre-fix Recall@5**: {s['recall_at_5_count']}/{s['total_cases']} ({s['recall_at_5_pct']}%)",
        f"- **Pre-fix Top-1 hits**: {s['top_1_count']}/{s['total_cases']}",
        f"- **Post-fix cleared (structural hits)**: {s['post_fix_cleared_count']}/{s['structural_hit_count']}",
        f"- **Total env false-positives filtered by falsifier**: {s['total_falsified_filtered']}",
        "",
        "| # | Case | Repo | Pre-fix Hit | Rank | Firing Oracle(s) | Total Cands | Post-fix Cleared |",
        "| :-: | :--- | :--- | :---: | :---: | :--- | :---: | :---: |",
    ]
    for idx, row in enumerate(report["cases"], start=1):
        pre = row["pre_fix"]
        post = row["post_fix"]
        hit = "HIT" if pre["recall_at_5"] else ("MISS (>5)" if pre["target_hits"] else "MISS")
        rank = str(pre["rank_of_first_hit"]) if pre["rank_of_first_hit"] else "-"
        oracles = ", ".join(pre["firing_oracles"]) or "-"
        if post["cleared"]:
            cleared = f"YES (-{post['cleared_signatures']})"
        elif not pre.get("has_structural_hit"):
            cleared = "- (claims-only)"
        else:
            cleared = f"NO ({post['same_rule_hits']})"
        lines.append(
            f"| {idx} | `{row['id']}` | `{row['repo']}` | **{hit}** | {rank} | `{oracles}` | {pre['total_candidates']} | {cleared} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_benchmark(
    cases_path: str | Path,
    *,
    case_id: str | None = None,
    cache_dir: Path | None = None,
    json_out: str | Path | None = None,
    markdown_out: str | Path | None = None,
) -> dict[str, Any]:
    """Run all (or selected) ContractBench cases and optionally write JSON/Markdown reports."""
    c_path = Path(cases_path).resolve()
    cases = json.loads(c_path.read_text(encoding="utf-8", errors="replace"))
    selected = [c for c in cases if not case_id or c["id"] == case_id]
    if not selected:
        raise LookupError(f"no benchmark case matching {case_id!r}")

    results = [evaluate_case(c, cache_dir=cache_dir) for c in selected]
    recall_hits = sum(1 for r in results if r["pre_fix"]["recall_at_5"])
    top1_hits = sum(1 for r in results if r["pre_fix"]["rank_of_first_hit"] == 1)
    structural_hits = sum(1 for r in results if r["pre_fix"].get("has_structural_hit"))
    cleared = sum(1 for r in results if r["post_fix"]["cleared"])
    total_falsified = sum(r["pre_fix"]["falsified_filtered"] for r in results)

    report = {
        "top_k": TOP_K,
        "summary": {
            "total_cases": len(results),
            "recall_at_5_count": recall_hits,
            "recall_at_5_pct": round(100.0 * recall_hits / len(results), 1) if results else 0.0,
            "top_1_count": top1_hits,
            "structural_hit_count": structural_hits,
            "post_fix_cleared_count": cleared,
            "total_falsified_filtered": total_falsified,
        },
        "cases": results,
    }

    if json_out:
        jpath = Path(json_out).resolve()
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report["json_out"] = str(jpath)

    if markdown_out:
        mpath = Path(markdown_out).resolve()
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(render_benchmark_markdown(report), encoding="utf-8")
        report["markdown_out"] = str(mpath)

    return report
