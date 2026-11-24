"""Contract claim mining, fenced example execution, and call-site analysis."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from bug_vet import ledger as ledger_mod
from bug_vet.paths import ensure_run, resolve_python

FAMILIES: dict[str, tuple[re.Pattern[str], str]] = {
    "roundtrip": (
        re.compile(
            r"\b(lossless(ly)?|reversible|round[- ]?trip(s|ped|ping)?|preserves? (exactly|the original))\b",
            re.I,
        ),
        "prop-roundtrip",
    ),
    "idempotence": (
        re.compile(r"\b(idempoten(t|ce)|safe to (re-?run|repeat)|no[- ]?op when)\b", re.I),
        "prop-idempotent",
    ),
    "determinism": (
        re.compile(
            r"\b(deterministic(ally)?|reproducible|stable (output|ordering|bytes)|same (input|result))\b",
            re.I,
        ),
        "prop-deterministic",
    ),
    "preservation": (
        re.compile(
            r"\b(preserves?|keeps?|kept|leaves?|left|returns?)\b[^.]{0,60}?"
            r"\b(exactly|unchanged|untouched|verbatim|intact|as[- ]is)\b",
            re.I,
        ),
        "prop-preservation",
    ),
    "exclusivity": (
        re.compile(
            r"\b(must never|never (returns?|raises?|mutates?|modifies?|loses?|drops?|writes?)|"
            r"exactly one|at most one|only one)\b",
            re.I,
        ),
        "prop-negative-case",
    ),
    "concurrency": (
        re.compile(r"\b(thread[- ]?safe|race[- ]?free|concurren(t|cy) safe|lock[- ]?free)\b", re.I),
        "prop-concurrency",
    ),
    "bounds": (
        re.compile(r"\b(at most|no (more|larger) than|bounded by|upper[- ]bound|within the limit)\b", re.I),
        "prop-bounds",
    ),
    "error_contract": (
        re.compile(
            r"\b(must (raise|reject|fail)|raises? (a |an )?(ValueError|TypeError|KeyError|RuntimeError|"
            r"NotImplementedError|error)|rejects? (invalid|unknown|unsupported|bad)|"
            r"fails? (loudly|with|when|rather than))\b",
            re.I,
        ),
        "prop-error-contract",
    ),
    "return_shape": (
        re.compile(
            r"\breturns? (the original|an empty|empty|None|the same|unchanged|the input)\b",
            re.I,
        ),
        "prop-return-shape",
    ),
    "ordering": (
        re.compile(
            r"\b(must be called (before|after)|order(ing)? (matters|is preserved|is stable)|"
            r"before .{0,20} is called|after .{0,20} returns)\b",
            re.I,
        ),
        "prop-ordering",
    ),
    "passthrough": (
        re.compile(r"\b(pass(ed)? through|forwarded as[- ]is|untouched|verbatim)\b", re.I),
        "prop-passthrough",
    ),
    "validation": (
        re.compile(r"\b(validat(e|es|ion)|rejects?|must not accept)\b", re.I),
        "prop-validation",
    ),
}

TIER: dict[str, str] = {
    "roundtrip": "A",
    "idempotence": "A",
    "determinism": "A",
    "concurrency": "A",
    "bounds": "A",
    "error_contract": "A",
    "return_shape": "A",
    "preservation": "B",
    "exclusivity": "B",
    "ordering": "B",
    "passthrough": "B",
    "validation": "B",
}

NEGATION = re.compile(r"\b(not|never|no|non|isn't|aren't|cannot|can't|without)\b[\w\s-]{0,24}$", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
WHITESPACE = re.compile(r"[ \t]+")
DOCTEST = re.compile(r"^[ \t]*(>>>|\.\.\.).*$", re.M)
FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")

VISIBILITY_RANK = {"public": 0, "module": 1, "docs": 2}

EXAMPLE_LANGS = {"python", "python3", "py"}
EXAMPLE_SKIP_MARKERS = (
    ">>>",
    "...",
    "<your",
    "your_api_key",
    "YOUR_API_KEY",
    "pip install",
    'os.environ["OPENAI_API_KEY"] = "sk-',
)
EXAMPLE_ENV_MARKERS = (
    "timeout after",
    "ConnectionError",
    "Connection refused",
    "SSLError",
    "ProxyError",
    "No module named",
    "ModuleNotFoundError",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "api_key",
    "API key",
)


@dataclass(frozen=True)
class Claim:
    id: str
    kind: str
    tier: str
    text: str
    source: str
    line: int
    symbol: str | None
    visibility: str
    reachable: bool
    importers: list[str]
    probe: str


def strip_code_fences(text: str) -> str:
    """Remove CommonMark-style backtick and tilde fenced blocks of any length >= 3."""
    kept: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        match = FENCE_OPEN.match(line)
        if fence_char is None:
            if match:
                fence = match.group(1)
                rest = match.group(2)
                if fence[0] == "`" and "`" in rest:
                    kept.append(line)
                    continue
                fence_char = fence[0]
                fence_len = len(fence)
                continue
            kept.append(line)
        else:
            close_candidate = stripped.strip()
            if (
                len(close_candidate) >= fence_len
                and set(close_candidate) == {fence_char}
            ):
                fence_char = None
                fence_len = 0
    return "\n".join(kept)


def fenced_blocks(text: str, language: str = "python") -> list[tuple[int, str]]:
    """Return ``(start_line, code)`` for every fenced code block matching ``language``."""
    blocks: list[tuple[int, str]] = []
    fence_char: str | None = None
    fence_len = 0
    start: int | None = None
    tag: str = ""
    body: list[str] = []
    wanted = EXAMPLE_LANGS if language in EXAMPLE_LANGS else {language.lower()}

    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE_OPEN.match(line)
        if fence_char is None:
            if match:
                fence = match.group(1)
                rest = match.group(2).strip()
                if fence[0] == "`" and "`" in rest:
                    continue
                fence_char = fence[0]
                fence_len = len(fence)
                start = number
                tag = rest.split()[0].lower() if rest else ""
                body = []
            continue

        close_candidate = line.strip()
        if len(close_candidate) >= fence_len and set(close_candidate) == {fence_char}:
            if start is not None and (tag in wanted or (not tag and language in EXAMPLE_LANGS)):
                blocks.append((start, "\n".join(body)))
            fence_char = None
            fence_len = 0
            start = None
            tag = ""
            body = []
        else:
            body.append(line)
    return blocks


def _sentences(text: str) -> list[str]:
    normalized = WHITESPACE.sub(" ", text)
    return [s.strip() for s in SENTENCE_SPLIT.split(normalized) if s and s.strip()]


def _negated(sentence: str, match_start: int) -> bool:
    prefix = sentence[:match_start]
    return bool(NEGATION.search(prefix[-40:]))


def claims_in(text: str) -> list[tuple[str, str, int]]:
    """Return ``(kind, sentence, offset)`` for every non-negated contract claim in ``text``."""
    cleaned = DOCTEST.sub(" ", strip_code_fences(text))
    found: list[tuple[str, str, int]] = []
    for sentence in _sentences(cleaned):
        offset = cleaned.find(sentence)
        for kind, (pattern, _probe) in FAMILIES.items():
            match = pattern.search(sentence)
            if not match or _negated(sentence, match.start()):
                continue
            if len(sentence) > 400:
                continue
            found.append((kind, sentence, offset))
            break
    return found


def _docstring_nodes(tree: ast.AST) -> list[tuple[str | None, str, int, str]]:
    out: list[tuple[str | None, str, int, str]] = []
    module_doc = ast.get_docstring(tree, clean=False)
    if module_doc:
        out.append((None, module_doc, 1, "module"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        private = node.name.startswith("_") and not node.name.startswith("__")
        out.append((node.name, doc, node.lineno, "docs" if private else "public"))
    return out


def _make(
    path: Path,
    line: int,
    symbol: str | None,
    visibility: str,
    kind: str,
    sentence: str,
    importers: list[str],
) -> Claim:
    return Claim(
        id="",
        kind=kind,
        tier=TIER[kind],
        text=sentence,
        source=str(path),
        line=line,
        symbol=symbol,
        visibility=visibility,
        reachable=bool(importers),
        importers=importers,
        probe=FAMILIES[kind][1],
    )


def scan_python(path: Path, importers: list[str]) -> list[Claim]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []
    claims: list[Claim] = []
    for symbol, doc, line, visibility in _docstring_nodes(tree):
        for kind, sentence, _offset in claims_in(doc):
            claims.append(_make(path, line, symbol, visibility, kind, sentence, importers))
    return claims


def scan_markdown(path: Path, importers: list[str]) -> list[Claim]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    claims: list[Claim] = []
    fence_char: str | None = None
    fence_len = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        match = FENCE_OPEN.match(raw)
        if fence_char is None:
            if match:
                fence = match.group(1)
                rest = match.group(2)
                if not (fence[0] == "`" and "`" in rest):
                    fence_char = fence[0]
                    fence_len = len(fence)
                    continue
        else:
            close_candidate = raw.strip()
            if len(close_candidate) >= fence_len and set(close_candidate) == {fence_char}:
                fence_char = None
                fence_len = 0
            continue
        for kind, sentence, _offset in claims_in(raw):
            claims.append(_make(path, number, None, "docs", kind, sentence, importers))
    return claims


def module_stem(path: Path) -> str:
    return path.parent.name if path.name == "__init__.py" else path.stem


def _import_stems(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    stems: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                stems.add(node.module.rsplit(".", 1)[-1])
            for alias in node.names:
                stems.add(alias.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                stems.add(alias.name.rsplit(".", 1)[-1])
    return stems


def _importer_map(paths: list[Path]) -> dict[str, list[str]]:
    importers: dict[str, list[str]] = {}
    for path in paths:
        for stem in _import_stems(path):
            importers.setdefault(stem, []).append(path.name)
    return {stem: sorted(set(names)) for stem, names in importers.items()}


def scan_claims(
    root: Path,
    *,
    include_tests: bool = False,
    tier: str = "A",
    max_per_family: int = 40,
    reachable_only: bool = True,
) -> list[Claim]:
    """Scan ``root`` for stated contracts and return numbered ``Claim`` records."""
    patterns = ("*.py", "*.md", "*.rst")
    skipped = {".git", ".venv", "node_modules", ".bug-vet"}
    py_paths = [
        path
        for path in sorted(root.rglob("*.py"))
        if not (skipped & set(path.parts))
        and (include_tests or not ("test" in path.name or "tests" in set(path.parts)))
    ]
    importers_by_stem = _importer_map(py_paths)

    claims: list[Claim] = []
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            parts = set(path.parts)
            if skipped & parts:
                continue
            if not include_tests and ("test" in path.name or "tests" in parts):
                continue
            importers = importers_by_stem.get(module_stem(path), []) if path.suffix == ".py" else []
            claims.extend(
                scan_python(path, importers) if path.suffix == ".py" else scan_markdown(path, importers)
            )

    seen: set[tuple[str, int, str, str]] = set()
    unique: list[Claim] = []
    for claim in claims:
        key = (claim.source, claim.line, claim.kind, claim.text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(claim)

    if reachable_only:
        unique = [c for c in unique if c.reachable]
    if tier != "all":
        unique = [c for c in unique if c.tier == tier]
    unique.sort(key=lambda c: (VISIBILITY_RANK.get(c.visibility, 9), c.source, c.line))

    counted: dict[str, int] = {}
    capped: list[Claim] = []
    for claim in unique:
        if max_per_family and counted.get(claim.kind, 0) >= max_per_family:
            continue
        counted[claim.kind] = counted.get(claim.kind, 0) + 1
        capped.append(claim)

    return [Claim(**{**asdict(claim), "id": f"c-{index:04d}"}) for index, claim in enumerate(capped, start=1)]


def run_claims_scan(
    run_name: str,
    target_path: str | Path,
    *,
    runs_dir: str | Path | None = None,
    include_tests: bool = False,
    tier: str = "A",
    max_per_family: int = 40,
    reachable_only: bool = True,
) -> dict:
    """Scan ``target_path``, write ``claims.json``, sync ``ledger.json``, and return summary."""
    root = Path(target_path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"target path does not exist: {root}")
    claims = scan_claims(
        root,
        include_tests=include_tests,
        tier=tier,
        max_per_family=max_per_family,
        reachable_only=reachable_only,
    )
    run_path = ensure_run(run_name, runs_dir=runs_dir)
    claim_dicts = [asdict(c) for c in claims]
    claims_file = run_path / "claims.json"
    claims_file.write_text(json.dumps(claim_dicts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ledger = ledger_mod.sync_obligations(run_path, claim_dicts)

    by_kind: dict[str, int] = {}
    for claim in claims:
        by_kind[claim.kind] = by_kind.get(claim.kind, 0) + 1
    return {
        "run": run_name,
        "claims_file": str(claims_file),
        "ledger_file": str(run_path / "ledger.json"),
        "count": len(claims),
        "by_kind": by_kind,
        "obligations": len(ledger["obligations"]),
        "claims": claim_dicts,
    }


def classify_example(exit_code: int, output: str) -> str:
    """Classify example output into PASS, FRAGMENT, NEEDS_ENV, or FAIL."""
    if exit_code == 0:
        return "PASS"
    if "NameError" in output and "is not defined" in output:
        return "FRAGMENT"
    if any(marker in output for marker in EXAMPLE_ENV_MARKERS):
        return "NEEDS_ENV"
    return "FAIL"


def run_examples(
    run_name: str,
    target_path: str | Path,
    *,
    runs_dir: str | Path | None = None,
    lang: str = "python",
    python: str | None = None,
    cwd: str | Path | None = None,
    timeout: int = 120,
) -> dict:
    """Extract fenced code examples from Markdown docs, execute them, and write ``examples-results.json``."""
    target = Path(target_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"example target does not exist: {target}")
    run_path = ensure_run(run_name, runs_dir=runs_dir)
    examples_dir = run_path / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    exec_cwd = Path(cwd).resolve() if cwd else (target.parent if target.is_file() else target)
    interpreter = resolve_python(python, exec_cwd)
    files = [target] if target.is_file() else sorted(target.rglob("*.md"))

    results: list[dict] = []
    index = 0
    for doc in files:
        text = doc.read_text(encoding="utf-8", errors="replace")
        for line_number, code in fenced_blocks(text, lang):
            index += 1
            name = f"example_{index:03d}.py"
            script = examples_dir / name
            script.write_text(code + "\n", encoding="utf-8")
            skip = any(marker in code for marker in EXAMPLE_SKIP_MARKERS) or (
                "import " not in code and "from " not in code
            )
            if skip:
                results.append({"example": name, "doc": str(doc), "line": line_number, "verdict": "SKIP"})
                continue
            try:
                completed = subprocess.run(
                    [interpreter, str(script)],
                    cwd=str(exec_cwd),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
                code_exit = completed.returncode
                output = ((completed.stdout or "") + (completed.stderr or "")).strip()[-2000:]
            except subprocess.TimeoutExpired:
                code_exit, output = -1, f"timeout after {timeout}s"
            results.append(
                {
                    "example": name,
                    "doc": str(doc),
                    "line": line_number,
                    "verdict": classify_example(code_exit, output),
                    "exit_code": code_exit,
                    "output": output,
                }
            )

    out_file = run_path / "examples-results.json"
    payload = {"path": str(target), "interpreter": interpreter, "results": results}
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return {
        "run": run_name,
        "artifact": str(out_file),
        "total": index,
        "counts": counts,
        "results": results,
    }


def _scan_ts_callsites(run_path: Path, root: Path, function_name: str, lang: str) -> dict:
    name = re.escape(function_name)
    definition = re.compile(
        rf"(?:export\s+)?(?:async\s+)?function\s+{name}\s*\(|"
        rf"(?:const|let|var)\s+{name}\s*=\s*(?:async\s*)?\("
    )
    call = re.compile(rf"(?<![\w.]){name}\s*\(")
    patterns = ("*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.cjs")
    files = sorted({path for pattern in patterns for path in root.rglob(pattern)})
    definitions, calls = [], []
    for file in files:
        if "node_modules" in file.parts:
            continue
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            if definition.search(line):
                definitions.append({"file": str(file.relative_to(root)), "line": number})
                continue
            if call.search(line):
                calls.append({"file": str(file.relative_to(root)), "line": number})
    payload = {
        "function": function_name,
        "language": lang,
        "definitions": definitions,
        "calls": calls,
    }
    out_file = run_path / "callsites-results.json"
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"run": run_name_from_path(run_path), "artifact": str(out_file), **payload}


def run_name_from_path(run_path: Path) -> str:
    return run_path.name


def scan_callsites(
    run_name: str,
    root_path: str | Path,
    function_name: str,
    *,
    runs_dir: str | Path | None = None,
    lang: str = "python",
) -> dict:
    """List call sites for ``function_name`` and (for Python) options never passed by callers."""
    root = Path(root_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")
    run_path = ensure_run(run_name, runs_dir=runs_dir)

    if lang.lower() in {"ts", "js", "tsx", "jsx"}:
        return _scan_ts_callsites(run_path, root, function_name, lang)

    files = [
        file
        for file in sorted(root.rglob("*.py"))
        if not ({".venv", "node_modules", ".git", ".bug-vet"} & set(file.parts))
    ]
    definitions: list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name:
                definitions.append((file, node))
    if not definitions:
        raise LookupError(f"function {function_name!r} not found under {root}")

    calls: list[dict] = []
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
            if name != function_name:
                continue
            keyword_names = {kw.arg for kw in node.keywords if kw.arg}
            calls.append(
                {
                    "file": str(file.relative_to(root)),
                    "line": node.lineno,
                    "positional": len(node.args),
                    "keywords": sorted(keyword_names),
                }
            )

    results = []
    for file, node in definitions:
        args_node = node.args
        params = [arg.arg for arg in [*args_node.posonlyargs, *args_node.args]]
        options = [arg.arg for arg in args_node.kwonlyargs]
        defaults = len(args_node.defaults)
        optional = params[len(params) - defaults :] if defaults else []
        passed_keywords = {name for call in calls for name in call["keywords"]}
        unwired = [name for name in [*optional, *options] if name not in passed_keywords]
        results.append(
            {
                "defined_in": str(file.relative_to(root)),
                "line": node.lineno,
                "params": params,
                "optional": optional,
                "keyword_only": options,
                "unwired": unwired,
            }
        )

    payload = {"function": function_name, "language": "python", "calls": calls, "definitions": results}
    out_file = run_path / "callsites-results.json"
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"run": run_name, "artifact": str(out_file), **payload}
