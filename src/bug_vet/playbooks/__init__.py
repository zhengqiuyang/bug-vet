"""On-demand access to compiled bug-family playbooks."""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Any


def _read_resource(filename: str) -> str:
    try:
        res = importlib.resources.files("bug_vet").joinpath("playbooks", filename)
        if res.is_file():
            return res.read_text(encoding="utf-8")
    except Exception:
        pass
    local = Path(__file__).resolve().parent / filename
    if local.is_file():
        return local.read_text(encoding="utf-8")
    raise FileNotFoundError(f"playbook resource not found: {filename}")


def list_playbooks() -> list[dict[str, Any]]:
    """Return metadata entries for all compiled playbooks."""
    raw = _read_resource("index.json")
    entries = json.loads(raw)
    return list(entries)


def get_playbook(query_or_id: str) -> dict[str, Any]:
    """Return the best-matching playbook content and metadata by id, family, or keyword."""
    q = query_or_id.strip().lower()
    if not q:
        raise ValueError("playbook query cannot be empty")
    entries = list_playbooks()

    # Exact id or family match first
    for entry in entries:
        if entry["id"].lower() == q or entry["family"].lower() == q:
            return {**entry, "content": _read_resource(entry["file"])}

    # Substring / keyword scoring
    tokens = [tok for tok in q.replace("-", " ").replace("_", " ").split() if tok]
    best_score = 0
    best_entry: dict[str, Any] | None = None
    for entry in entries:
        haystack = " ".join(
            [
                entry["id"],
                entry["title"],
                entry["family"],
                entry["summary"],
                *entry.get("keywords", []),
            ]
        ).lower()
        score = sum(3 if kw.lower() in q else 0 for kw in entry.get("keywords", []))
        score += sum(2 for tok in tokens if tok in haystack)
        if q in haystack:
            score += 4
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry is None:
        available = [e["id"] for e in entries]
        raise LookupError(f"no playbook matching {query_or_id!r}; available: {available}")

    return {**best_entry, "content": _read_resource(best_entry["file"])}
