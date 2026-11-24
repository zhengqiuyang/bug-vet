"""Standalone probe scaffolding and isolated execution."""

from __future__ import annotations

import ast
import importlib.resources
import json
import re
import subprocess
from pathlib import Path

from bug_vet import ledger as ledger_mod
from bug_vet.paths import build_env, ensure_run, resolve_python, run_dir

VERDICTS = {0: "PASS", 1: "FAIL", 2: "INCONCLUSIVE", 3: "CHECKER_BUG"}
FAILING_VERDICTS = {"FAIL", "CHECKER_BUG", "ERROR"}

_OBLIGATION_RE = re.compile(r'^OBLIGATION\s*=\s*["\']([A-Za-z0-9_.:-]+)["\']', re.MULTILINE)
_CLAIM_RE = re.compile(r'^CLAIM\s*=\s*["\']([A-Za-z0-9_.:-]+)["\']', re.MULTILINE)


def probe_binding(probe: Path) -> str | None:
    """Read ``OBLIGATION = "..."`` or ``CLAIM = "..."`` from a probe without importing it."""
    try:
        text = probe.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for pattern in (_OBLIGATION_RE, _CLAIM_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def has_self_check(probe: Path) -> bool:
    """Return True if the probe defines a top-level ``self_check`` function."""
    try:
        source = probe.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "self_check"
        for node in tree.body
    )


def list_probes(run_path: Path) -> list[Path]:
    probes_dir = run_path / "probes"
    if not probes_dir.is_dir():
        return []
    return sorted(probes_dir.glob("*.py"))


def execute_probes(
    run_path: Path,
    *,
    python: str | None = None,
    cwd: str | Path | None = None,
    env_pairs: list[str] | dict[str, str] | None = None,
    timeout: int = 300,
) -> tuple[list[dict], str, Path]:
    """Execute every probe under ``run_path / 'probes'`` and return raw result rows."""
    exec_cwd = Path(cwd).expanduser().resolve() if cwd else run_path.resolve()
    interpreter = resolve_python(python, exec_cwd)
    env = build_env(env_pairs)
    results: list[dict] = []

    for probe in list_probes(run_path):
        binding = probe_binding(probe)
        self_checked = has_self_check(probe)
        try:
            completed = subprocess.run(
                [interpreter, str(probe)],
                cwd=str(exec_cwd),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            code = completed.returncode
            output = (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired:
            code = -1
            output = f"timeout after {timeout}s"

        verdict = VERDICTS.get(code, f"ERROR({code})")
        claim_id, ob_id = ledger_mod.resolve_binding(binding)
        results.append(
            {
                "probe": probe.name,
                "claim": claim_id,
                "obligation_id": ob_id,
                "has_self_check": self_checked,
                "exit_code": code,
                "verdict": verdict,
                "output": output.strip()[-4000:],
            }
        )
    return results, interpreter, exec_cwd


def run_probes(
    run_name: str,
    *,
    runs_dir: str | Path | None = None,
    python: str | None = None,
    cwd: str | Path | None = None,
    env_pairs: list[str] | dict[str, str] | None = None,
    timeout: int = 300,
) -> dict:
    """Execute probes for ``run_name``, persist ``probe-results.json``, and update ``ledger.json``."""
    path = run_dir(run_name, runs_dir=runs_dir)
    if not (path / "probes").exists():
        raise FileNotFoundError(f"no probes/ directory under {path}")
    results, interpreter, exec_cwd = execute_probes(
        path, python=python, cwd=cwd, env_pairs=env_pairs, timeout=timeout
    )
    artifact = path / "probe-results.json"
    artifact.write_text(
        json.dumps({"python": interpreter, "cwd": str(exec_cwd), "results": results}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    ledger = ledger_mod.record_probe_run(path, results, interpreter, exec_cwd, root=exec_cwd)
    failures = sum(1 for row in results if row["verdict"] in FAILING_VERDICTS)
    return {
        "run": run_name,
        "python": interpreter,
        "cwd": str(exec_cwd),
        "artifact": str(artifact),
        "count": len(results),
        "failures": failures,
        "results": results,
        "ledger_summary": ledger_mod.summary(ledger, probe_dir=path / "probes"),
    }


def _load_template(ecosystem: str) -> str:
    filename = f"{ecosystem}_probe.py.tmpl"
    try:
        resource = importlib.resources.files("bug_vet").joinpath("templates", filename)
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except Exception:
        pass
    local = Path(__file__).resolve().parent / "templates" / filename
    if local.is_file():
        return local.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no probe template for ecosystem {ecosystem!r}")


def scaffold_probe(
    run_name: str,
    claim: str,
    *,
    runs_dir: str | Path | None = None,
    family: str = "roundtrip",
    ecosystem: str = "python",
    description: str | None = None,
    out: str | None = None,
) -> Path:
    """Create a standalone probe file from the bundled template."""
    template = _load_template(ecosystem)
    path = ensure_run(run_name, runs_dir=runs_dir)
    filename = out or f"probe_{claim}_{family}.py"
    target = path / "probes" / filename
    body = (
        template.replace("{{claim}}", claim)
        .replace("{{family}}", family)
        .replace("{{filename}}", filename)
        .replace("{{description}}", description or f"{family} contract")
    )
    target.write_text(body, encoding="utf-8")
    return target
