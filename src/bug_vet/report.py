"""Reporting engine: findings.json, REPORT.md, SARIF 2.1.0, and optional PR kit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bug_vet import __version__, ledger as ledger_mod
from bug_vet.paths import run_dir

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"


def _load_optional_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def _rule_id(family: str) -> str:
    slug = (family or "contract").strip().lower().replace(" ", "-")
    return f"bug-vet/{slug}"


def _sarif_location(source: str, line: int | None) -> dict[str, Any]:
    uri = (source or "unknown").replace("\\", "/").lstrip("/")
    physical: dict[str, Any] = {"artifactLocation": {"uri": uri}}
    if isinstance(line, int) and line >= 1:
        physical["region"] = {"startLine": line}
    return {"physicalLocation": physical}


def build_sarif(
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
    *,
    include_candidates: bool = False,
) -> dict[str, Any]:
    """Build a SARIF 2.1.0 document from confirmed findings (and optional candidates)."""
    rules_by_id: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    def add_rule(rid: str, desc: str) -> None:
        if rid not in rules_by_id:
            rules_by_id[rid] = {
                "id": rid,
                "name": rid.split("/", 1)[-1],
                "shortDescription": {"text": desc[:200] or rid},
            }

    for item in findings:
        rid = _rule_id(item.get("family", "contract"))
        add_rule(rid, f"Confirmed contract violation ({item.get('family', 'contract')})")
        results.append(
            {
                "ruleId": rid,
                "level": "error",
                "message": {"text": item.get("claim", "contract obligation failed")},
                "locations": [_sarif_location(str(item.get("source", "")), item.get("line"))],
                "properties": {
                    "obligationId": item.get("id"),
                    "contractAuthority": item.get("contract_authority"),
                    "weakContract": bool(item.get("weak_contract", False)),
                },
            }
        )

    if include_candidates and candidates:
        for cand in candidates:
            if cand.get("oracle") == "claims":
                continue
            rid = _rule_id(cand.get("family", "structural"))
            add_rule(rid, f"Structural anomaly candidate ({cand.get('family', 'structural')})")
            results.append(
                {
                    "ruleId": rid,
                    "level": "warning",
                    "message": {"text": cand.get("detail", "structural candidate")},
                    "locations": [_sarif_location(str(cand.get("file", "")), cand.get("line"))],
                    "properties": {
                        "oracle": cand.get("oracle"),
                        "confidence": cand.get("confidence"),
                        "symbol": cand.get("symbol"),
                        "unverifiedCandidate": True,
                    },
                }
            )

    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "bug-vet",
                        "version": __version__,
                        "informationUri": "https://github.com/zhengqiuyang/bug-vet",
                        "rules": list(rules_by_id.values()),
                    }
                },
                "results": results,
            }
        ],
    }


def _render_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        f"# bug-vet Audit Report: `{bundle['run']}`",
        "",
        f"- **Gate state**: `{bundle['state'].upper()}`",
        f"- **Confirmed findings**: {len(bundle['findings'])}",
        f"- **Falsified hypotheses**: {len(bundle['falsified'])}",
        f"- **Unknown / active obligations**: {len(bundle['unknowns'])}",
        f"- **Blockers**: {len(bundle['blockers'])}",
        "",
        "## Confirmed Findings",
        "",
    ]
    if not bundle["findings"]:
        lines.append("_No confirmed failing obligations._")
    else:
        for f in bundle["findings"]:
            loc = f"`{f['source']}:{f['line']}`" if f.get("line") else f"`{f['source']}`"
            sym = f" (`{f['symbol']}`)" if f.get("symbol") else ""
            weak = " [weak doc-only contract]" if f.get("weak_contract") else ""
            lines.append(f"### `{f['id']}` [{f['family']}] — {loc}{sym}{weak}")
            lines.append("")
            lines.append(f"- **Claim**: {f['claim']}")
            lines.append(f"- **Authority**: `{f['contract_authority']}`")
            lines.append("")

    if bundle.get("verification"):
        ver = bundle["verification"]
        lines.extend(
            [
                "## Red-to-Green Verification",
                "",
                f"- **Fixed (red -> green)**: {ver.get('fixed', [])}",
                f"- **Still failing**: {ver.get('still_failing', [])}",
                f"- **Regressed**: {ver.get('regressed', [])}",
                "",
            ]
        )

    if bundle.get("shrink"):
        sh = bundle["shrink"]
        lines.extend(
            [
                "## Minimal Reproduction (Delta-Debug Shrink)",
                "",
                f"- **Reduced**: `{sh.get('original_bytes')}` -> `{sh.get('minimal_bytes')}` bytes "
                f"in {sh.get('candidates_tried')} candidate run(s)",
                f"- **Confirmed minimal input**: `{json.dumps(sh.get('minimal'), ensure_ascii=False)}`",
                "",
            ]
        )

    lines.extend(["## Falsified Hypotheses", ""])
    if not bundle["falsified"]:
        lines.append("_No falsified hypotheses recorded._")
    else:
        lines.extend(["| Obligation | Category | Location | Reason |", "|---|---|---|---|"])
        for item in bundle["falsified"]:
            fal = item.get("falsification") or {}
            loc = fal.get("source_location") or f"{item.get('source')}:{item.get('line')}"
            lines.append(
                f"| `{item['id']}` | `{fal.get('category', '-')}` | `{loc}` | {fal.get('reason', '-')} |"
            )

    if bundle["blockers"] or bundle["unknowns"]:
        lines.extend(["", "## Blockers & Residual Unknowns", ""])
        for b in bundle["blockers"]:
            lines.append(f"- **BLOCKER `{b['kind']}`**: {json.dumps(b, ensure_ascii=False)}")
        for u in bundle["unknowns"]:
            lines.append(f"- `{u['id']}` (`{u['disposition']}`): {u['claim']}")

    lines.extend(
        [
            "",
            "## Scope & Limits",
            "",
            f"> {bundle['coverage'].get('limits', 'Only probed public boundaries are covered.')}",
            "",
        ]
    )
    return "\n".join(lines)


def _render_pr_kit(bundle: dict[str, Any]) -> str:
    findings = bundle["findings"]
    ver = bundle.get("verification") or {}
    sh = bundle.get("shrink")
    lines = [
        f"## Summary ({bundle['run']})",
        "",
        f"Verified {len(findings)} contract violation(s) under `bug-vet` gate state `{bundle['state']}`.",
        "",
        "## Root Cause & Findings",
        "",
    ]
    for f in findings:
        lines.append(f"- `{f['source']}:{f.get('line') or '?'}` (`{f['family']}`): {f['claim']}")
    if not findings:
        lines.append("- No failing obligations confirmed.")

    lines.extend(["", "## Minimal Reproduction & Red/Green Evidence", ""])
    if sh:
        lines.append(
            f"- Delta-debugged input (`{sh.get('original_bytes')}` -> `{sh.get('minimal_bytes')}` bytes): "
            f"`{json.dumps(sh.get('minimal'), ensure_ascii=False)}`"
        )
    if ver:
        lines.append(f"- Fixed probes (red -> green): `{ver.get('fixed', [])}`")
        lines.append(f"- Regressions: `{ver.get('regressed', [])}`")

    if bundle["falsified"]:
        lines.extend(["", "## Alternatives Investigated & Falsified", ""])
        for item in bundle["falsified"]:
            fal = item.get("falsification") or {}
            lines.append(f"- `{item['id']}` ({fal.get('category', '-') }): {fal.get('reason', '-')}")
    lines.append("")
    return "\n".join(lines)


def generate_report(
    run_name: str,
    *,
    runs_dir: str | Path | None = None,
    formats: tuple[str, ...] | list[str] = ("markdown", "json", "sarif"),
    include_pr: bool = False,
    include_candidates: bool = False,
) -> dict[str, Any]:
    """Compile run artifacts into ``findings.json``, ``REPORT.md``, ``results.sarif``, and optional ``PR.md``."""
    path = run_dir(run_name, runs_dir=runs_dir)
    if not path.exists():
        raise FileNotFoundError(f"run directory does not exist: {path}")

    gate_data = _load_optional_json(path / "gate.json")
    if gate_data is None:
        ledger = ledger_mod.load(path)
        gate_data = ledger_mod.evaluate(ledger, probe_dir=path / "probes")

    verification = _load_optional_json(path / "verification.json")
    shrink = _load_optional_json(path / "shrink-results.json")
    candidates_doc = _load_optional_json(path / "candidates.json") or {}
    candidates = candidates_doc.get("candidates", [])

    bundle: dict[str, Any] = {
        "run": run_name,
        "state": gate_data.get("state", "not-ready"),
        "counts": gate_data.get("counts", {}),
        "findings": gate_data.get("findings", []),
        "falsified": gate_data.get("falsified", []),
        "unknowns": gate_data.get("unknowns", []),
        "blockers": gate_data.get("blockers", []),
        "coverage": gate_data.get("coverage", {}),
        "verification": verification,
        "shrink": shrink,
    }

    normalized_formats = {fmt.strip().lower() for fmt in formats}
    artifacts: dict[str, str] = {}

    if "json" in normalized_formats:
        f_path = path / "findings.json"
        f_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        artifacts["json"] = str(f_path)

    if "markdown" in normalized_formats or "md" in normalized_formats:
        md_path = path / "REPORT.md"
        md_path.write_text(_render_markdown(bundle), encoding="utf-8")
        artifacts["markdown"] = str(md_path)

    sarif_results = 0
    if "sarif" in normalized_formats:
        sarif_doc = build_sarif(
            bundle["findings"],
            candidates=candidates,
            include_candidates=include_candidates,
        )
        sarif_results = len(sarif_doc["runs"][0]["results"])
        s_path = path / "results.sarif"
        s_path.write_text(json.dumps(sarif_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        artifacts["sarif"] = str(s_path)

    if include_pr:
        pr_path = path / "PR.md"
        pr_path.write_text(_render_pr_kit(bundle), encoding="utf-8")
        artifacts["pr"] = str(pr_path)

    return {
        "run": run_name,
        "state": bundle["state"],
        "finding_count": len(bundle["findings"]),
        "falsified_count": len(bundle["falsified"]),
        "sarif_results": sarif_results,
        "artifacts": artifacts,
    }
