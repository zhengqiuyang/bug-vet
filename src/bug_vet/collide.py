"""GitHub unclaimed-issue scanner and timeline/PR collision gate (Phase 0)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.parse import quote

from bug_vet.paths import ensure_run


def _gh_json(*args: str) -> object:
    try:
        completed = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("gh CLI is not installed on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("gh CLI timed out") from exc
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {err}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from gh: {exc}") from exc


def check_collision(
    repo: str,
    *,
    issue: int | None = None,
    keywords: list[str] | None = None,
    files: list[str] | None = None,
    runs_dir: str | Path | None = None,
    name: str | None = None,
    require_clear: bool = False,
) -> dict:
    """Check issue timeline cross-references, open PRs, and merged PRs for collisions."""
    verdict = "CLEAR"
    related = False
    findings: list[dict] = []

    def add(kind: str, number: int, title: str, url: str) -> None:
        findings.append({"kind": kind, "number": number, "title": title, "url": url})

    if issue is not None:
        timeline = _gh_json(
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repo}/issues/{issue}/timeline?per_page=100",
        )
        if isinstance(timeline, list):
            for event in timeline:
                if not isinstance(event, dict) or event.get("event") != "cross-referenced":
                    continue
                item = event.get("source", {}).get("issue") or {}
                if item.get("state") == "open":
                    add(
                        "linked-open-pr",
                        item.get("number", 0),
                        item.get("title", ""),
                        item.get("html_url", ""),
                    )
        if any(f["kind"] == "linked-open-pr" for f in findings):
            verdict = "BLOCKED"

    queries: list[tuple[str, str]] = []
    for keyword in keywords or []:
        queries.append(("keyword", keyword))
    for filename in files or []:
        queries.append(("file", Path(filename).name))

    target_names = list(files or [])
    for _kind, value in queries:
        for state in ("is:open", "is:merged"):
            label = "open" if state == "is:open" else "merged"
            result = _gh_json(
                "api",
                "search/issues?q=" + quote(f"repo:{repo} is:pr {state} {value}") + "&per_page=20",
            )
            items = result.get("items", []) if isinstance(result, dict) else []
            for item in items:
                number = item["number"]
                touches = False
                if target_names and state == "is:open":
                    pr_files = _gh_json("api", f"repos/{repo}/pulls/{number}/files?per_page=100")
                    changed = [f.get("filename", "") for f in pr_files] if isinstance(pr_files, list) else []
                    touches = any(
                        any(
                            path.endswith(spec) if "/" in spec else Path(path).name == spec
                            for path in changed
                        )
                        for spec in target_names
                    )
                add(f"{label}-pr", number, item["title"], item.get("html_url", ""))
                if state == "is:open" and (touches or not target_names):
                    verdict = "BLOCKED"
                elif state == "is:open" and not touches:
                    related = True

    unique = {(f["kind"], f["number"]): f for f in findings}
    findings = sorted(unique.values(), key=lambda f: (f["kind"], f["number"]))
    has_merged = any(f["kind"] == "merged-pr" for f in findings)

    run_name = name or repo.replace("/", "__")
    out_dir = ensure_run(run_name, runs_dir=runs_dir)
    record = {
        "run": run_name,
        "repo": repo,
        "issue": issue,
        "keywords": list(keywords or []),
        "files": list(files or []),
        "verdict": verdict,
        "merged_candidates": has_merged,
        "related_open_prs": related,
        "findings": findings,
    }
    artifact = out_dir / "collision-results.json"
    artifact.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    record["artifact"] = str(artifact)
    record["blocked"] = bool(require_clear and (verdict != "CLEAR" or has_merged))
    return record


def scan_issues(
    repo: str,
    *,
    runs_dir: str | Path | None = None,
    name: str | None = None,
    label: str | None = None,
    limit: int = 50,
    show: int = 25,
) -> dict:
    """List open issues and mark unclaimed targets (no linked PR, assignee, or comments)."""
    issues: list[dict] = []
    page = 1
    while len(issues) < limit:
        batch = _gh_json("api", f"repos/{repo}/issues?state=open&per_page=100&page={page}")
        if not isinstance(batch, list) or not batch:
            break
        issues.extend(item for item in batch if "pull_request" not in item)
        if len(batch) < 100:
            break
        page += 1
    if label:
        wanted = label.lower()
        issues = [
            item
            for item in issues
            if any(str(entry.get("name", "")).lower() == wanted for entry in item.get("labels", []))
        ]
    issues = issues[:limit]

    rows = []
    for issue in issues:
        timeline = _gh_json(
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repo}/issues/{issue['number']}/timeline?per_page=100",
        )
        refs = []
        if isinstance(timeline, list):
            for event in timeline:
                if not isinstance(event, dict) or event.get("event") != "cross-referenced":
                    continue
                source = event.get("source", {}).get("issue", {})
                if source.get("number"):
                    refs.append(
                        {
                            "number": source["number"],
                            "state": source.get("state"),
                            "title": source.get("title", ""),
                        }
                    )
        open_refs = [ref for ref in refs if ref["state"] == "open"]
        unclaimed = not refs and not issue.get("assignees") and issue["comments"] == 0
        rows.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "created": (issue.get("created_at") or "")[:10],
                "comments": issue["comments"],
                "assignees": [person.get("login") for person in issue.get("assignees", [])],
                "linked_prs": refs,
                "open_linked_prs": open_refs,
                "unclaimed": unclaimed,
            }
        )
    rows.sort(key=lambda row: (not row["unclaimed"], row["created"]))

    run_name = name or repo.replace("/", "__")
    out_dir = ensure_run(run_name, runs_dir=runs_dir)
    artifact = out_dir / "issues-results.json"
    payload = {"repo": repo, "label": label, "rows": rows}
    artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    free = [row for row in rows if row["unclaimed"]]
    return {
        "run": run_name,
        "repo": repo,
        "artifact": str(artifact),
        "total": len(rows),
        "unclaimed": len(free),
        "rows": rows[:show],
    }
