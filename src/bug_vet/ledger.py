"""Evidence ledger: records obligations, evidence with authority levels, and fail-closed verdicts.

Principles:
* Every claim or structural candidate becomes an *obligation* that must be closed.
* Every check leaves an *evidence* record stamped with authority, verdict, and digests.
* Evidence below an obligation's ``required_observation`` cannot close it.
* A probe without ``self_check()`` fails closed as ``unknown`` (``missing-self-check``).
* Disproven candidates are explicitly recorded via ``falsified`` so dismissed paths stay auditable.
* ``unknown`` never aggregates to ``ready``.

Pure Python stdlib only. Never writes to stdout.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

SCHEMA = "bug-vet-ledger"
SCHEMA_VERSION = 1

AUTHORITIES = ("doc", "probe", "test", "published", "runtime")
DISPOSITIONS = ("active", "probed-pass", "probed-fail", "falsified", "unknown", "deferred")
DEFINITE_VERDICTS = {"PASS", "FAIL"}
NON_DEFINITE_VERDICTS = {"INCONCLUSIVE", "ERROR", "CHECKER_BUG", "CHECKER BUG"}


def is_non_definite_verdict(verdict: str | None) -> bool:
    """Timed-out or crashed probes report as ``ERROR(<code>)`` and must stay non-definite."""
    if not verdict:
        return True
    return verdict in NON_DEFINITE_VERDICTS or verdict.startswith("ERROR")


FAIL_CLOSED = [
    "inconclusive",            # a probe without a definite verdict is never a pass
    "missing-self-check",      # a probe without self_check() cannot certify anything
    "insufficient-authority",  # evidence below required_observation cannot close obligation
    "environment-changed",     # evidence from another interpreter/cwd is not comparable
    "unbound-evidence",        # a failing probe naming no obligation blocks readiness
    "contradiction",           # the same obligation has conflicting admissible verdicts
    "weak-contract",           # prose-only contract: usable as a lead, marked explicitly
]


def rank(authority: str) -> int:
    try:
        return AUTHORITIES.index(authority)
    except ValueError:
        return -1


def comparable(available: str, required: str) -> bool:
    """True when an observation at ``available`` satisfies ``required``."""
    return rank(available) >= rank(required) >= 0


def digest(*parts: object) -> str:
    blob = "\x00".join(str(part) for part in parts).encode("utf-8", "replace")
    return hashlib.sha256(blob).hexdigest()[:32]


def env_digest(python: str, cwd: str | Path) -> str:
    """Identity of the execution environment."""
    return digest("env", python, str(Path(cwd).resolve()))


def code_digest(root: Path | None) -> str:
    """Identity of the audited checkout: git HEAD when available, else ``'nogit'``."""
    if root is None:
        return "nogit"
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "nogit"


# --------------------------------------------------------------------------- ledger IO


def empty_ledger(target: dict | None = None) -> dict:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "target": target or {},
        "obligations": [],
        "evidence": [],
    }


def load(run_path: Path) -> dict:
    path = run_path / "ledger.json"
    if not path.exists():
        return empty_ledger()
    try:
        ledger = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return empty_ledger()
    ledger.setdefault("schema", SCHEMA)
    ledger.setdefault("schema_version", SCHEMA_VERSION)
    ledger.setdefault("target", {})
    ledger.setdefault("obligations", [])
    ledger.setdefault("evidence", [])
    return ledger


def save(run_path: Path, ledger: dict) -> Path:
    run_path.mkdir(parents=True, exist_ok=True)
    path = run_path / "ledger.json"
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- obligations


def contract_authority(claim: dict) -> str:
    """Authority of where the promise is stated: test > published > doc."""
    explicit = claim.get("authority")
    if explicit in AUTHORITIES:
        return str(explicit)
    visibility = claim.get("visibility", "public")
    if visibility == "published":
        return "published"
    source = str(claim.get("source", ""))
    src_path = Path(source)
    if (
        visibility == "test"
        or "tests" in src_path.parts
        or "test" in src_path.parts
        or src_path.name.startswith("test_")
        or src_path.stem.endswith("_test")
    ):
        return "test"
    return "doc"


def obligation_id(claim_id: str) -> str:
    return claim_id if claim_id.startswith("ob-") else f"ob-{claim_id}"


def obligation_from_claim(claim: dict, required: str = "probe") -> dict:
    raw_id = claim.get("id")
    if not raw_id:
        raw_id = "auto-" + digest(claim.get("source", ""), claim.get("symbol", ""), claim.get("text", ""))[:8]
    ob_id = obligation_id(str(raw_id))
    req = claim.get("required_observation") or required
    return {
        "id": ob_id,
        "claim_id": str(raw_id),
        "origin": claim.get("origin", "claims"),
        "text": claim.get("text", ""),
        "source": claim.get("source", ""),
        "line": claim.get("line"),
        "symbol": claim.get("symbol"),
        "family": claim.get("kind", "contract"),
        "contract_authority": contract_authority(claim),
        "required_observation": req if req in AUTHORITIES else "probe",
        "required_case_kinds": list(claim.get("required_case_kinds", ["positive", "malformed"])),
        "dependencies": list(claim.get("dependencies", [])),
        "residual_unknowns": list(claim.get("residual_unknowns", [])),
        "disposition": "active",
        "evidence": [],
        "falsification": None,
    }


def _preserve_existing(fresh: dict, old: dict | None) -> dict:
    if not old:
        return fresh
    fresh["disposition"] = old.get("disposition", "active")
    fresh["evidence"] = list(old.get("evidence", []))
    fresh["residual_unknowns"] = list(old.get("residual_unknowns", []))
    fresh["falsification"] = old.get("falsification")
    return fresh


def sync_obligations(run_path: Path, claims: list[dict], *, required: str = "probe") -> dict:
    """Rebuild claim-derived obligations while preserving evidence history and manual/oracle obligations."""
    ledger = load(run_path)
    previous = {ob["id"]: ob for ob in ledger["obligations"]}
    rebuilt: list[dict] = []
    seen_ids: set[str] = set()

    for claim in claims:
        fresh = obligation_from_claim({**claim, "origin": claim.get("origin", "claims")}, required=required)
        _preserve_existing(fresh, previous.get(fresh["id"]))
        rebuilt.append(fresh)
        seen_ids.add(fresh["id"])

    for old in ledger["obligations"]:
        if old["id"] not in seen_ids and old.get("origin", "claims") != "claims":
            rebuilt.append(old)

    ledger["obligations"] = rebuilt
    save(run_path, ledger)
    return ledger


def promote_candidates(
    run_path: Path,
    *,
    indices: list[int] | None = None,
    structural_only: bool = False,
    required: str = "probe",
) -> list[dict]:
    """Promote selected entries from ``candidates.json`` into ledger obligations."""
    cand_file = run_path / "candidates.json"
    if not cand_file.exists():
        raise FileNotFoundError(f"candidates.json not found under {run_path}")
    doc = json.loads(cand_file.read_text(encoding="utf-8", errors="replace"))
    cands: list[dict] = list(doc.get("candidates", []))
    target_root = doc.get("target")

    selected: list[tuple[int, dict]] = []
    if indices:
        seen_idx: set[int] = set()
        for idx in indices:
            if idx < 1 or idx > len(cands):
                raise ValueError(f"candidate index {idx} out of range 1..{len(cands)}")
            if idx not in seen_idx:
                seen_idx.add(idx)
                selected.append((idx, cands[idx - 1]))
    elif structural_only:
        for idx, cand in enumerate(cands, 1):
            if cand.get("oracle") != "claims":
                selected.append((idx, cand))
    else:
        raise ValueError("specify candidate indices or structural_only=True")

    promoted: list[dict] = []
    for idx, cand in selected:
        src_rel = str(cand.get("file") or "")
        src_path = str(Path(target_root) / src_rel) if target_root and src_rel else src_rel
        cid = f"cand-{idx}-{digest(src_rel, cand.get('line'), cand.get('symbol') or '', cand.get('family') or '')[:6]}"
        claim = {
            "id": cid,
            "text": str(cand.get("detail") or ""),
            "source": src_path,
            "line": cand.get("line"),
            "symbol": cand.get("symbol"),
            "kind": str(cand.get("family") or "contract"),
            "origin": f"oracle:{cand.get('oracle', 'scan')}",
            "visibility": "public",
        }
        promoted.append(add_obligation(run_path, claim, required=required))
    return promoted


def add_obligation(run_path: Path, claim: dict, *, required: str = "probe") -> dict:
    """Register or update a single obligation in ``ledger.json`` and return the obligation."""
    ledger = load(run_path)
    fresh = obligation_from_claim({**claim, "origin": claim.get("origin", "manual")}, required=required)
    existing_idx = next((i for i, ob in enumerate(ledger["obligations"]) if ob["id"] == fresh["id"]), None)
    if existing_idx is not None:
        _preserve_existing(fresh, ledger["obligations"][existing_idx])
        ledger["obligations"][existing_idx] = fresh
    else:
        ledger["obligations"].append(fresh)
    save(run_path, ledger)
    return fresh


# --------------------------------------------------------------------------- evidence & settling


def resolve_binding(raw_binding: str | None) -> tuple[str | None, str | None]:
    """Turn a probe's CLAIM or OBLIGATION identifier into ``(claim_id, obligation_id)``."""
    if not raw_binding:
        return None, None
    token = raw_binding.strip()
    if token.startswith("ob-"):
        return token[3:], token
    return token, obligation_id(token)


def make_evidence(
    probe: str,
    result: dict,
    python: str,
    cwd: str | Path,
    probe_path: Path | None = None,
) -> dict:
    verdict = result.get("verdict", "ERROR")
    raw_binding = result.get("obligation_id") or result.get("claim") or None
    claim_id, ob_id = resolve_binding(raw_binding)
    probe_digest = "missing"
    if probe_path is not None and probe_path.exists():
        try:
            probe_digest = digest(probe_path.read_text(encoding="utf-8", errors="replace"))[:16]
        except OSError:
            probe_digest = "unreadable"
    has_self_check = bool(result.get("has_self_check", True))
    return {
        "id": "ev-" + digest(probe, verdict, python, cwd, probe_digest, has_self_check)[:16],
        "kind": "probe-run",
        "probe": probe,
        "source_key": f"probe:{probe}",
        "probe_digest": probe_digest,
        "claim_id": claim_id,
        "obligation_id": ob_id,
        "verdict": verdict,
        "exit_code": result.get("exit_code"),
        "authority": "probe",
        "has_self_check": has_self_check,
        "definite": verdict in DEFINITE_VERDICTS and result.get("exit_code") in (0, 1),
        "env_digest": env_digest(python, cwd),
        "output_digest": digest(result.get("output", ""))[:16],
        "output": (result.get("output") or "")[-600:],
    }


def _set_target(ledger: dict, python: str, cwd: str | Path, root: Path | None) -> None:
    prev_code = ledger.get("target", {}).get("code_digest", "nogit")
    ledger["target"] = {
        "python": python,
        "cwd": str(Path(cwd).resolve()),
        "env_digest": env_digest(python, cwd),
        "code_digest": code_digest(root) if root is not None else prev_code,
    }


def record_probe_run(
    run_path: Path,
    results: list[dict],
    python: str,
    cwd: str | Path,
    root: Path | None = None,
) -> dict:
    """Append one evidence record per probe and settle obligations."""
    ledger = load(run_path)
    probe_dir = run_path / "probes"
    _set_target(ledger, python, cwd, root)
    by_obligation = {ob["id"]: ob for ob in ledger["obligations"]}
    for result in results:
        name = result.get("probe", "?")
        evidence = make_evidence(name, result, python, cwd, probe_dir / name)
        ledger["evidence"].append(evidence)
        obligation = by_obligation.get(evidence.get("obligation_id") or "")
        if obligation is None:
            continue
        obligation["evidence"].append(evidence["id"])
        _settle(obligation, ledger)
    save(run_path, ledger)
    return ledger


def record_test_evidence(
    run_path: Path,
    target_obligation_id: str,
    command: str,
    exit_code: int,
    output: str,
    python: str,
    cwd: str | Path,
    root: Path | None = None,
) -> tuple[dict, dict]:
    """Record ``test``-authority evidence from running the repository's own test command."""
    ledger = load(run_path)
    if not ledger.get("target"):
        _set_target(ledger, python, cwd, root)
    ob_id = obligation_id(target_obligation_id)
    verdict = "PASS" if exit_code == 0 else "FAIL"
    seq = len(ledger["evidence"])
    ev = {
        "id": "ev-" + digest("test-run", ob_id, command, exit_code, output, seq)[:16],
        "kind": "test-run",
        "probe": command,
        "source_key": f"test:{command}",
        "probe_digest": digest("test", command, seq)[:16],
        "claim_id": ob_id[3:] if ob_id.startswith("ob-") else ob_id,
        "obligation_id": ob_id,
        "verdict": verdict,
        "exit_code": exit_code,
        "authority": "test",
        "has_self_check": True,
        "definite": True,
        "env_digest": env_digest(python, cwd),
        "output_digest": digest(output)[:16],
        "output": output[-600:],
    }
    ledger["evidence"].append(ev)
    obligation = next((ob for ob in ledger["obligations"] if ob["id"] == ob_id), None)
    if obligation is not None:
        obligation["evidence"].append(ev["id"])
        _settle(obligation, ledger)
    save(run_path, ledger)
    return ledger, ev


def record_published_evidence(
    run_path: Path,
    target_obligation_id: str,
    package: str,
    version: str,
    differing_count: int,
    details: str,
    python: str,
    cwd: str | Path,
    root: Path | None = None,
) -> tuple[dict, dict]:
    """Record ``published``-authority evidence from differential comparison against a PyPI release."""
    ledger = load(run_path)
    if not ledger.get("target"):
        _set_target(ledger, python, cwd, root)
    ob_id = obligation_id(target_obligation_id)
    verdict = "FAIL" if differing_count > 0 else "PASS"
    seq = len(ledger["evidence"])
    label = f"{package}=={version}"
    ev = {
        "id": "ev-" + digest("published-diff", ob_id, label, differing_count, details, seq)[:16],
        "kind": "published-diff",
        "probe": label,
        "source_key": f"published:{label}",
        "probe_digest": digest("published", label, seq)[:16],
        "claim_id": ob_id[3:] if ob_id.startswith("ob-") else ob_id,
        "obligation_id": ob_id,
        "verdict": verdict,
        "exit_code": 1 if differing_count > 0 else 0,
        "authority": "published",
        "has_self_check": True,
        "definite": True,
        "env_digest": env_digest(python, cwd),
        "output_digest": digest(details)[:16],
        "output": details[-600:],
        "differing_count": differing_count,
    }
    ledger["evidence"].append(ev)
    obligation = next((ob for ob in ledger["obligations"] if ob["id"] == ob_id), None)
    if obligation is not None:
        obligation["evidence"].append(ev["id"])
        _settle(obligation, ledger)
    save(run_path, ledger)
    return ledger, ev


def record_falsification(
    run_path: Path,
    target_obligation_id: str,
    reason: str,
    category: str,
    source_location: str | None = None,
) -> tuple[dict, dict]:
    """Record an adversarial falsification and mark the obligation ``falsified``."""
    ledger = load(run_path)
    ob_id = obligation_id(target_obligation_id)
    obligation = next((ob for ob in ledger["obligations"] if ob["id"] == ob_id), None)
    if obligation is None:
        raise KeyError(f"obligation {ob_id!r} not found in ledger")
    seq = len(ledger["evidence"])
    ev = {
        "id": "ev-" + digest("falsification", ob_id, category, reason, source_location or "", seq)[:16],
        "kind": "falsification",
        "probe": f"falsify:{category}",
        "source_key": f"falsify:{ob_id}",
        "probe_digest": digest("falsify", ob_id, seq)[:16],
        "claim_id": obligation.get("claim_id"),
        "obligation_id": ob_id,
        "verdict": "FALSIFIED",
        "exit_code": 0,
        "authority": "runtime",
        "has_self_check": True,
        "definite": True,
        "env_digest": ledger.get("target", {}).get("env_digest", ""),
        "output_digest": digest(reason)[:16],
        "output": reason[-600:],
        "category": category,
        "source_location": source_location,
    }
    ledger["evidence"].append(ev)
    obligation["evidence"].append(ev["id"])
    obligation["falsification"] = {
        "category": category,
        "reason": reason,
        "source_location": source_location,
        "evidence_id": ev["id"],
    }
    _settle(obligation, ledger)
    save(run_path, ledger)
    return ledger, ev


def _is_env_comparable(evidence: dict, ledger: dict) -> bool:
    if evidence.get("authority") == "published" or evidence.get("kind") == "falsification":
        return True
    target_env = ledger.get("target", {}).get("env_digest")
    if not target_env:
        return True
    return evidence.get("env_digest") == target_env


def _admissible(evidence: dict, ledger: dict, required_authority: str = "probe") -> bool:
    if not evidence.get("definite", False):
        return False
    if evidence.get("kind") == "probe-run" and not evidence.get("has_self_check", True):
        return False
    if not _is_env_comparable(evidence, ledger):
        return False
    return comparable(evidence.get("authority", ""), required_authority)


def _latest_per_source(evidence: list[dict]) -> dict[str, list[dict]]:
    """Keep the newest revision per probe/command/source.

    For ``probe-run``, all records sharing the newest ``probe_digest`` are kept so
    non-deterministic PASS/FAIL flips on the same probe file are caught as contradictions.
    For ``test-run``, ``published-diff``, and ``falsification``, the latest run supersedes.
    """
    by_source: dict[str, list[dict]] = {}
    for record in evidence:
        key = record.get("source_key") or record.get("probe") or record.get("id") or "?"
        by_source.setdefault(key, []).append(record)
    out: dict[str, list[dict]] = {}
    for key, records in by_source.items():
        latest = records[-1]
        if latest.get("kind", "probe-run") == "probe-run":
            newest_digest = latest.get("probe_digest")
            out[key] = [r for r in records if r.get("probe_digest") == newest_digest]
        else:
            out[key] = [latest]
    return out


def _settle(obligation: dict, ledger: dict) -> None:
    """Set one obligation's disposition from its evidence. Fail-closed: unknown wins over pass."""
    by_id = {ev["id"]: ev for ev in ledger["evidence"]}
    cited = [by_id[eid] for eid in obligation.get("evidence", []) if eid in by_id]
    if not cited:
        obligation["disposition"] = "active" if not obligation.get("residual_unknowns") else "deferred"
        return
    if cited[-1].get("kind") == "falsification":
        obligation["disposition"] = "falsified"
        return

    required = obligation.get("required_observation", "probe")
    verdicts: set[str] = set()
    usable = 0
    for records in _latest_per_source(cited).values():
        for record in records:
            if record.get("kind") == "falsification":
                continue
            if not _admissible(record, ledger, required):
                continue
            usable += 1
            verdicts.add(record["verdict"])

    if usable == 0:
        obligation["disposition"] = "unknown"
        return
    if len(verdicts) > 1:
        obligation["disposition"] = "unknown"
    elif verdicts == {"FAIL"}:
        obligation["disposition"] = "probed-fail"
    else:
        obligation["disposition"] = "probed-pass"


# --------------------------------------------------------------------------- evaluation


def evaluate(ledger: dict, probe_dir: Path | None = None) -> dict:
    """Apply fail-closed rules and return a three-state readiness report."""
    obligations = ledger.get("obligations", [])
    evidence = ledger.get("evidence", [])
    by_id = {ev["id"]: ev for ev in evidence}
    latest = _latest_per_source(evidence)
    blockers: list[dict] = []
    counts: dict[str, int] = {name: 0 for name in DISPOSITIONS}

    for obligation in obligations:
        disp = obligation.get("disposition", "active")
        counts[disp] = counts.get(disp, 0) + 1
        if disp == "falsified":
            continue
        cited = [by_id[eid] for eid in obligation.get("evidence", []) if eid in by_id]
        current = _latest_per_source(cited)
        required = obligation.get("required_observation", "probe")
        has_sufficient_authority = False

        for records in current.values():
            for record in records:
                if record.get("kind") == "falsification":
                    continue
                if is_non_definite_verdict(record.get("verdict")):
                    blockers.append(
                        {"kind": "inconclusive", "obligation": obligation["id"], "probe": record["probe"]}
                    )
                if record.get("kind") == "probe-run" and not record.get("has_self_check", True):
                    blockers.append(
                        {"kind": "missing-self-check", "obligation": obligation["id"], "probe": record["probe"]}
                    )
                if not _is_env_comparable(record, ledger):
                    blockers.append(
                        {"kind": "environment-changed", "obligation": obligation["id"], "probe": record["probe"]}
                    )
                if comparable(record.get("authority", ""), required):
                    has_sufficient_authority = True

        if cited and not has_sufficient_authority:
            latest_rec = cited[-1]
            blockers.append(
                {
                    "kind": "insufficient-authority",
                    "obligation": obligation["id"],
                    "probe": latest_rec.get("probe"),
                    "available": latest_rec.get("authority"),
                    "required": required,
                }
            )

        verdicts = {
            record["verdict"]
            for records in current.values()
            for record in records
            if record.get("kind") != "falsification" and _admissible(record, ledger, required)
        }
        if "PASS" in verdicts and "FAIL" in verdicts:
            blockers.append({"kind": "contradiction", "obligation": obligation["id"]})
        if disp == "probed-fail" and obligation.get("contract_authority") == "doc":
            obligation["weak_contract"] = True

    for records in latest.values():
        record = records[-1]
        if record.get("kind") != "probe-run":
            continue
        if record.get("verdict") != "FAIL" or record.get("obligation_id"):
            continue
        probe_name = record.get("probe", "")
        if probe_dir is not None and not (Path(probe_dir) / probe_name).exists():
            continue
        blockers.append({"kind": "unbound-evidence", "probe": probe_name})

    findings = [ob for ob in obligations if ob.get("disposition") == "probed-fail"]
    falsified = [ob for ob in obligations if ob.get("disposition") == "falsified"]
    unknowns = [
        ob for ob in obligations if ob.get("disposition") in {"unknown", "deferred", "active"}
    ]
    blocking_kinds = {b["kind"] for b in blockers}
    hard_blockers = {"contradiction", "unbound-evidence"}
    soft_blockers = {
        "inconclusive",
        "environment-changed",
        "missing-self-check",
        "insufficient-authority",
    }

    if not obligations or (blocking_kinds & hard_blockers):
        state = "not-ready"
    elif findings:
        state = "partial" if (blocking_kinds & soft_blockers) else "ready"
    elif not unknowns and not (blocking_kinds & soft_blockers):
        state = "ready"
    else:
        state = "partial"

    return {
        "state": state,
        "counts": counts,
        "findings": [
            {
                "id": ob["id"],
                "claim": ob["text"],
                "source": ob["source"],
                "line": ob["line"],
                "symbol": ob.get("symbol"),
                "family": ob["family"],
                "contract_authority": ob["contract_authority"],
                "weak_contract": ob.get("weak_contract", False),
            }
            for ob in findings
        ],
        "falsified": [
            {
                "id": ob["id"],
                "claim": ob["text"],
                "source": ob["source"],
                "line": ob["line"],
                "falsification": ob.get("falsification"),
            }
            for ob in falsified
        ],
        "unknowns": [
            {"id": ob["id"], "disposition": ob.get("disposition"), "claim": ob["text"][:120]}
            for ob in unknowns
        ],
        "blockers": blockers,
        "coverage": {
            "obligations": len(obligations),
            "evidence": len(evidence),
            "dispositions": counts,
            "limits": (
                "public claims and structural scans only; doc-only contracts remain weak leads "
                "until corroborated by test/published evidence"
            ),
        },
    }


def summary(ledger: dict, probe_dir: Path | None = None) -> str:
    report = evaluate(ledger, probe_dir=probe_dir)
    counts = report["counts"]
    return (
        f"obligations {len(ledger.get('obligations', []))}: "
        f"fail {counts.get('probed-fail', 0)}, pass {counts.get('probed-pass', 0)}, "
        f"falsified {counts.get('falsified', 0)}, unknown {counts.get('unknown', 0)}, "
        f"active {counts.get('active', 0)} -> {report['state']}"
    )
