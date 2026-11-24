"""Delta-debugging shrinker for JSON failing inputs."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

from bug_vet.paths import build_env, ensure_run, resolve_python

Candidate = tuple[str, object]


def candidates(value: object) -> Iterator[Candidate]:
    """Yield ``(label, smaller_value)`` in deterministic simplest-first order."""
    if isinstance(value, dict):
        keys = list(value)
        if len(keys) > 1:
            half = keys[: len(keys) // 2]
            yield "drop half the keys", {k: v for k, v in value.items() if k not in half}
        for key in keys:
            yield f"drop key {key!r}", {k: v for k, v in value.items() if k != key}
        for key, item in value.items():
            for label, smaller in candidates(item):
                yield f"{key}.{label}", {**value, key: smaller}
        if len(value) == 1:
            yield "unwrap single-key object", next(iter(value.values()))
        if value:
            yield "empty object", {}
    elif isinstance(value, list):
        if len(value) > 1:
            yield "keep first half", value[: len(value) // 2]
            yield "keep second half", value[len(value) // 2 :]
        for index in range(len(value)):
            yield f"drop item {index}", value[:index] + value[index + 1 :]
        for index, item in enumerate(value):
            for label, smaller in candidates(item):
                yield f"[{index}].{label}", value[:index] + [smaller] + value[index + 1 :]
        if len(value) == 1:
            yield "unwrap single-item list", value[0]
        if value:
            yield "empty list", []
    elif isinstance(value, str):
        if value:
            yield "empty string", ""
            if len(value) > 1:
                yield "first half", value[: len(value) // 2]
            if "\n" in value:
                yield "first line", value.split("\n", 1)[0]
    elif isinstance(value, bool):
        if value:
            yield "false", False
    elif value is None:
        return
    elif isinstance(value, (int, float)):
        if value != 0:
            yield "zero", 0


def _fingerprint(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def shrink(
    predicate: Callable[[object], bool],
    value: object,
    *,
    max_runs: int = 300,
) -> tuple[object, int, list[str]]:
    """Return ``(smallest_failing_value, runs_used, accepted_step_labels)``."""
    accepted: list[str] = []
    runs = 0
    seen = {_fingerprint(value)}
    while runs < max_runs:
        progressed = False
        for label, candidate in candidates(value):
            if runs >= max_runs:
                break
            fingerprint = _fingerprint(candidate)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            runs += 1
            if predicate(candidate):
                value = candidate
                accepted.append(label)
                progressed = True
                break
        if not progressed:
            break
    return value, runs, accepted


def run_shrink(
    run_name: str,
    script: str | Path,
    input_path: str | Path,
    *,
    runs_dir: str | Path | None = None,
    python: str | None = None,
    cwd: str | Path | None = None,
    env_pairs: list[str] | dict[str, str] | None = None,
    timeout: int = 120,
    max_runs: int = 300,
) -> dict:
    """Delta-debug a failing JSON input against a probe reading ``BUG_VET_INPUT`` / ``BUGCHECKUP_INPUT``."""
    script_path = Path(script).expanduser().resolve()
    in_path = Path(input_path).expanduser().resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"script not found: {script_path}")
    if not in_path.exists():
        raise FileNotFoundError(f"input not found: {in_path}")

    original = json.loads(in_path.read_text(encoding="utf-8", errors="replace"))
    path = ensure_run(run_name, runs_dir=runs_dir)
    out_dir = path / "shrunk"
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_file = out_dir / "candidate.json"
    exec_cwd = Path(cwd).expanduser().resolve() if cwd else path.resolve()
    interpreter = resolve_python(python, exec_cwd)
    base_env = build_env(env_pairs)

    def predicate(value: object) -> bool:
        candidate_file.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_env = {
            **base_env,
            "BUG_VET_INPUT": str(candidate_file),
            "BUGCHECKUP_INPUT": str(candidate_file),
        }
        try:
            completed = subprocess.run(
                [interpreter, str(script_path)],
                cwd=str(exec_cwd),
                env=run_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False
        return completed.returncode == 1

    minimal, tried, accepted = shrink(predicate, original, max_runs=max_runs)
    confirmed = predicate(minimal)
    minimal_file = out_dir / f"{script_path.stem}.min.json"
    minimal_file.write_text(
        json.dumps(minimal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    original_bytes = len(json.dumps(original, ensure_ascii=False))
    minimal_bytes = len(json.dumps(minimal, ensure_ascii=False))
    record = {
        "run": run_name,
        "script": str(script_path),
        "input": str(in_path),
        "minimal_file": str(minimal_file),
        "minimal": minimal,
        "original_bytes": original_bytes,
        "minimal_bytes": minimal_bytes,
        "candidates_tried": tried,
        "accepted_steps": accepted,
        "confirmed": confirmed,
    }
    artifact = path / "shrink-results.json"
    artifact.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    record["artifact"] = str(artifact)
    return record
