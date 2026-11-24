"""Shared candidate dataclass and file-traversal helpers for all oracles."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SKIPPED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".bug-vet",
        "__pycache__",
        "site-packages",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)


@dataclass(frozen=True)
class Candidate:
    """One ranked structural or contract discovery candidate."""

    oracle: str
    family: str
    file: str
    line: int | None
    symbol: str | None
    detail: str
    confidence: str = "medium"
    evidence: dict[str, Any] = field(default_factory=dict)
    playbook_id: str | None = None

    def key(self) -> tuple[str, str, str | None, str]:
        return (self.oracle, self.file, self.symbol, self.family)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_test_path(path: Path) -> bool:
    """Return True if ``path`` appears to be a test or spec file."""
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts & {"test", "tests", "unittest", "unittests", "__tests__"}:
        return True
    name = path.name.lower()
    stem = path.stem.lower()
    return (
        name.startswith("test_")
        or stem.endswith("_test")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.js")
        or name.endswith(".test.ts")
        or name.endswith(".test.js")
    )


def iter_source_files(
    root: Path,
    *,
    suffixes: tuple[str, ...] = (".py",),
    include_tests: bool = False,
) -> list[Path]:
    """Yield source files under ``root`` while skipping vendor/venv/test directories."""
    if root.is_file():
        if root.suffix in suffixes and (include_tests or not is_test_path(root)):
            return [root]
        return []

    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        rel_parts = set(path.relative_to(root).parts)
        if SKIPPED_DIRS & rel_parts:
            continue
        if not include_tests and is_test_path(path.relative_to(root)):
            continue
        files.append(path)
    return files


def iter_python_files(root: Path, *, include_tests: bool = False) -> list[Path]:
    """Return sorted non-test Python files under ``root``."""
    return iter_source_files(root, suffixes=(".py",), include_tests=include_tests)


def rel_posix(path: Path, root: Path) -> str:
    """Return a repository-relative POSIX path string."""
    base = root.parent if root.is_file() else root
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()
