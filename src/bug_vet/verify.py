"""Red-to-green verification, test-authority runner, and published-release differential runner."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from bug_vet import ledger as ledger_mod
from bug_vet.paths import build_env, ensure_run, resolve_python, run_dir
from bug_vet.runner import FAILING_VERDICTS, execute_probes


def compare_verdicts(baseline: dict, results: list[dict]) -> dict:
    """Pure before/after probe comparison."""
    before = {row["probe"]: row for row in baseline.get("results", [])}
    after = {row["probe"]: row for row in results}
    fixed, still_failing, regressed, new = [], [], [], []
    for name, row in after.items():
        old = before.get(name)
        if old is None:
            new.append(name)
            continue
        was_bad = old["verdict"] in FAILING_VERDICTS
        is_good = row["verdict"] == "PASS"
        if was_bad and is_good:
            fixed.append(name)
        elif was_bad and not is_good:
            still_failing.append(name)
        elif not was_bad and not is_good:
            regressed.append(name)
    return {
        "fixed": fixed,
        "still_failing": still_failing,
        "regressed": regressed,
        "new_probes": new,
        "before": {name: row["verdict"] for name, row in before.items()},
        "after": {name: row["verdict"] for name, row in after.items()},
    }


def snapshot_baseline(
    run_name: str,
    *,
    runs_dir: str | Path | None = None,
    python: str | None = None,
    cwd: str | Path | None = None,
    env_pairs: list[str] | dict[str, str] | None = None,
    timeout: int = 300,
) -> dict:
    """Run probes on the pre-fix tree and store ``probe-baseline.json``."""
    path = run_dir(run_name, runs_dir=runs_dir)
    if not (path / "probes").exists():
        raise FileNotFoundError(f"no probes/ directory under {path}")
    results, interpreter, exec_cwd = execute_probes(
        path, python=python, cwd=cwd, env_pairs=env_pairs, timeout=timeout
    )
    if not results:
        raise FileNotFoundError(f"no probes under {path / 'probes'} to baseline")
    artifact = path / "probe-baseline.json"
    artifact.write_text(
        json.dumps({"python": interpreter, "cwd": str(exec_cwd), "results": results}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    failing = [row["probe"] for row in results if row["verdict"] in FAILING_VERDICTS]
    return {
        "run": run_name,
        "python": interpreter,
        "cwd": str(exec_cwd),
        "artifact": str(artifact),
        "count": len(results),
        "failing": failing,
        "results": results,
    }


def verify_fix(
    run_name: str,
    *,
    runs_dir: str | Path | None = None,
    python: str | None = None,
    cwd: str | Path | None = None,
    env_pairs: list[str] | dict[str, str] | None = None,
    timeout: int = 300,
) -> dict:
    """Re-run probes after a patch and compare against ``probe-baseline.json``."""
    path = run_dir(run_name, runs_dir=runs_dir)
    baseline_path = path / "probe-baseline.json"
    if not baseline_path.exists():
        raise FileNotFoundError(f"no baseline at {baseline_path}; run baseline before verifying")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8", errors="replace"))
    results, interpreter, exec_cwd = execute_probes(
        path, python=python, cwd=cwd, env_pairs=env_pairs, timeout=timeout
    )
    verification = compare_verdicts(baseline, results)
    ok = bool(verification["fixed"]) and not verification["still_failing"] and not verification["regressed"]
    verification["ok"] = ok
    verification["python"] = interpreter
    verification["cwd"] = str(exec_cwd)

    artifact = path / "verification.json"
    artifact.write_text(json.dumps(verification, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    before_rows = {row["probe"]: row for row in baseline.get("results", [])}
    after_rows = {row["probe"]: row for row in results}
    return {
        "run": run_name,
        "artifact": str(artifact),
        **verification,
        "before_outputs": {
            name: (before_rows[name].get("output") or "").strip()[-240:] for name in verification["fixed"]
        },
        "after_outputs": {
            name: (after_rows[name].get("output") or "").strip()[-240:] for name in verification["fixed"]
        },
    }


def _split_command(test_cmd: str | list[str], interpreter: str) -> list[str]:
    if isinstance(test_cmd, list):
        tokens = [str(part) for part in test_cmd]
    else:
        raw_tokens = shlex.split(test_cmd, posix=(os.name != "nt"))
        tokens = [
            tok[1:-1] if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in {'"', "'"} else tok
            for tok in raw_tokens
        ]
    if not tokens:
        raise ValueError("test command cannot be empty")
    head = tokens[0].lower()
    if head in {"python", "python3"}:
        return [interpreter, *tokens[1:]]
    if head == "pytest":
        return [interpreter, "-m", "pytest", *tokens[1:]]
    return tokens


def run_test_evidence(
    run_name: str,
    obligation_id: str,
    test_cmd: str | list[str],
    *,
    runs_dir: str | Path | None = None,
    python: str | None = None,
    cwd: str | Path | None = None,
    env_pairs: list[str] | dict[str, str] | None = None,
    timeout: int = 300,
) -> dict:
    """Execute a repository test command and record ``test``-authority evidence in ``ledger.json``."""
    path = ensure_run(run_name, runs_dir=runs_dir)
    exec_cwd = Path(cwd).expanduser().resolve() if cwd else path.resolve()
    interpreter = resolve_python(python, exec_cwd)
    argv = _split_command(test_cmd, interpreter)
    env = build_env(env_pairs)
    cmd_label = test_cmd if isinstance(test_cmd, str) else " ".join(test_cmd)

    try:
        completed = subprocess.run(
            argv,
            cwd=str(exec_cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        exit_code = completed.returncode
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        exit_code = -1
        output = f"timeout after {timeout}s"

    ledger, ev = ledger_mod.record_test_evidence(
        path,
        obligation_id,
        cmd_label,
        exit_code,
        output,
        interpreter,
        exec_cwd,
        root=exec_cwd,
    )
    return {
        "run": run_name,
        "obligation_id": ev["obligation_id"],
        "command": cmd_label,
        "argv": argv,
        "exit_code": exit_code,
        "verdict": ev["verdict"],
        "authority": ev["authority"],
        "evidence_id": ev["id"],
        "output": output[-2000:],
        "ledger_summary": ledger_mod.summary(ledger, probe_dir=path / "probes"),
    }


def _pip_venv(run_path: Path, package: str, version: str, python_version: str) -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH; install uv or pass old_python explicitly")
    safe = f"{package.replace('/', '_')}-{version}".replace(":", "_")
    base = run_path / f".diff-venv-{safe}"
    python = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        subprocess.run([uv, "venv", str(base), "--python", python_version], check=True)
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), f"{package}=={version}"],
            check=True,
        )
    return str(python)


def run_diff(
    run_name: str,
    scenario: str | Path,
    inputs: str | Path,
    *,
    runs_dir: str | Path | None = None,
    old_python: str | None = None,
    new_python: str | None = None,
    pip_version: str | None = None,
    pip_package: str | None = None,
    pip_python: str = "3.12",
    old_env: list[str] | dict[str, str] | None = None,
    new_env: list[str] | dict[str, str] | None = None,
    cwd: str | Path | None = None,
    timeout: int = 600,
    max_diffs: int = 50,
    obligation_id: str | None = None,
) -> dict:
    """Compare a scenario script on two environments and optionally log ``published`` evidence."""
    path = ensure_run(run_name, runs_dir=runs_dir)
    scenario_path = Path(scenario).expanduser().resolve()
    inputs_path = Path(inputs).expanduser().resolve()
    if not scenario_path.exists() or not inputs_path.exists():
        raise FileNotFoundError("scenario and inputs must both exist")

    exec_cwd = Path(cwd).expanduser().resolve() if cwd else scenario_path.parent
    if pip_version:
        if not pip_package:
            raise ValueError("--pip-version requires --pip-package")
        resolved_old_python = _pip_venv(path, pip_package, pip_version, pip_python)
    else:
        resolved_old_python = resolve_python(old_python, exec_cwd)
    resolved_new_python = resolve_python(new_python, exec_cwd)

    def run_env(interpreter: str, extra_env: list[str] | dict[str, str] | None) -> list[str] | str:
        env = build_env(extra_env)
        try:
            with inputs_path.open("r", encoding="utf-8", errors="replace") as handle:
                completed = subprocess.run(
                    [interpreter, str(scenario_path)],
                    stdin=handle,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    cwd=str(exec_cwd),
                    env=env,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"{type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            return f"exit {completed.returncode}: {(completed.stderr or '').strip()[-800:]}"
        return [line for line in (completed.stdout or "").splitlines() if line.strip()]

    old_lines = run_env(resolved_old_python, old_env)
    new_lines = run_env(resolved_new_python, new_env)
    if isinstance(old_lines, str) or isinstance(new_lines, str):
        raise RuntimeError(f"scenario failed: old={old_lines!r}, new={new_lines!r}")

    differences = []
    for index, (before, after) in enumerate(zip(old_lines, new_lines, strict=False)):
        if before != after:
            differences.append({"index": index, "old": before[:800], "new": after[:800]})
    if len(old_lines) != len(new_lines):
        differences.append(
            {
                "index": -1,
                "old": f"{len(old_lines)} result lines",
                "new": f"{len(new_lines)} result lines",
            }
        )

    results = {
        "run": run_name,
        "scenario": str(scenario_path),
        "inputs": str(inputs_path),
        "old_python": resolved_old_python,
        "new_python": resolved_new_python,
        "pip_package": pip_package,
        "pip_version": pip_version,
        "lines": {"old": len(old_lines), "new": len(new_lines)},
        "different": len(differences),
        "differences": differences[:max_diffs],
    }
    artifact = path / "diff-results.json"
    artifact.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    results["artifact"] = str(artifact)

    if pip_version and pip_package and obligation_id:
        summary_text = json.dumps(differences[:3], ensure_ascii=False) if differences else "no output differences"
        ledger, ev = ledger_mod.record_published_evidence(
            path,
            obligation_id,
            pip_package,
            pip_version,
            len(differences),
            summary_text,
            resolved_new_python,
            exec_cwd,
            root=exec_cwd,
        )
        results["published_evidence_id"] = ev["id"]
        results["ledger_summary"] = ledger_mod.summary(ledger, probe_dir=path / "probes")

    return results
