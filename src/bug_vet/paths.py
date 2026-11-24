"""Path resolution and environment helpers for bug-vet.

Run artifacts are decoupled from the installed package directory: by default they live
under ``<cwd>/.bug-vet/runs/<run>`` and can be overridden via ``--runs-dir`` or
``BUG_VET_RUNS_DIR``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


def default_runs_dir(runs_dir: str | Path | None = None) -> Path:
    """Resolve the base directory where runs are stored."""
    if runs_dir is not None and str(runs_dir).strip():
        return Path(runs_dir).expanduser().resolve()
    env_dir = os.environ.get("BUG_VET_RUNS_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return (Path.cwd() / ".bug-vet" / "runs").resolve()


def validate_run_name(name: str) -> str:
    """Ensure a run name is a single safe directory component."""
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"invalid run name {name!r}: must be a single path segment")
    return cleaned


def run_dir(name: str, runs_dir: str | Path | None = None) -> Path:
    """Return the path for a named run without creating it."""
    return default_runs_dir(runs_dir) / validate_run_name(name)


def ensure_run(name: str, runs_dir: str | Path | None = None) -> Path:
    """Ensure ``<runs_dir>/<name>/`` and its ``probes/`` subdirectory exist."""
    path = run_dir(name, runs_dir=runs_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "probes").mkdir(exist_ok=True)
    return path


def resolve_python(explicit: str | Path | None = None, cwd: str | Path | None = None) -> str:
    """Pick the Python interpreter for target execution.

    Priority: explicit argument -> target ``.venv`` inside ``cwd`` -> current Python.
    """
    if explicit is not None and str(explicit).strip():
        return str(explicit)
    if cwd is not None:
        base = Path(cwd).expanduser().resolve()
        for rel in ("Scripts/python.exe", "bin/python"):
            candidate = base / ".venv" / rel
            if candidate.exists():
                return str(candidate)
    return sys.executable


def build_env(env_pairs: Sequence[str] | Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a child-process environment dict from ``KEY=VALUE`` pairs or a mapping."""
    env = dict(os.environ)
    if not env_pairs:
        return env
    if isinstance(env_pairs, Mapping):
        for key, value in env_pairs.items():
            env[str(key)] = str(value)
        return env
    for pair in env_pairs:
        key, sep, value = str(pair).partition("=")
        if not sep:
            raise ValueError(f"invalid environment entry {pair!r}: expected KEY=VALUE")
        env[key] = value
    return env
