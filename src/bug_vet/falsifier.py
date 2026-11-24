"""Adversarial falsification: upstream compensation trace and vacuous test/probe auditor."""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path
from typing import Any

from bug_vet.oracles.base import Candidate, iter_python_files, rel_posix


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _string_literals(node: ast.AST) -> set[str]:
    return {
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    }


def _env_var_in_node(node: ast.AST, env_var: str) -> bool:
    if env_var in _string_literals(node):
        return True
    return False


def check_upstream_compensation(
    root: str | Path,
    symbol: str,
    *,
    env_var: str | None = None,
    file_hint: str | None = None,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Walk callers (and non-branching setup helpers) up to ``max_depth`` hops to see if ``env_var`` is handled."""
    target = Path(root).resolve()
    if not target.exists():
        raise FileNotFoundError(f"target path does not exist: {target}")

    # Collect function definitions and call graph
    defs: dict[str, list[tuple[str, int, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
    callers_of: dict[str, list[tuple[str, int, str, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
    callees_of: dict[tuple[str, str], list[str]] = {}

    for file in iter_python_files(target):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        rel = rel_posix(file, target)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            defs.setdefault(node.name, []).append((rel, node.lineno, node))
            seen_callees: list[str] = []
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                cname = _call_name(sub)
                if not cname or cname == node.name:
                    continue
                callers_of.setdefault(cname, []).append((rel, sub.lineno, node.name, node))
                if cname not in seen_callees:
                    seen_callees.append(cname)
            callees_of[(rel, node.name)] = seen_callees

    start_defs = defs.get(symbol, [])
    if file_hint:
        hint_norm = file_hint.replace("\\", "/")
        narrowed = [d for d in start_defs if d[0].endswith(hint_norm) or hint_norm in d[0]]
        if narrowed:
            start_defs = narrowed

    compensation_sites: list[dict[str, Any]] = []
    visited_funcs: set[str] = {symbol}
    queue: deque[tuple[str, int]] = deque([(symbol, 0)])
    hops_checked = 0

    # Check direct non-branching setup callees of start_defs (e.g. get_llm_provider called before branches)
    if env_var:
        for rel, _lineno, fn in start_defs:
            for stmt in fn.body:
                # Only inspect top-level statements before branching or direct calls, not sibling elif branches
                if isinstance(stmt, ast.If):
                    continue
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Call):
                        cname = _call_name(sub)
                        if cname and cname in defs:
                            for c_rel, c_line, c_fn in defs[cname]:
                                if _env_var_in_node(c_fn, env_var):
                                    compensation_sites.append(
                                        {
                                            "direction": "setup-callee",
                                            "file": c_rel,
                                            "line": c_line,
                                            "symbol": cname,
                                            "env_var": env_var,
                                        }
                                    )

    while queue:
        cur_sym, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for c_rel, c_line, caller_sym, caller_fn in callers_of.get(cur_sym, []):
            hops_checked = max(hops_checked, depth + 1)
            if env_var and _env_var_in_node(caller_fn, env_var):
                # Avoid false match when the caller itself is just a huge multi-provider router
                # mentioning env_var in a *different* elif branch unless env_var appears at setup level
                # or inside the branch that calls cur_sym.
                if _caller_compensates_call(caller_fn, cur_sym, env_var):
                    compensation_sites.append(
                        {
                            "direction": "upstream-caller",
                            "hop": depth + 1,
                            "file": c_rel,
                            "line": c_line,
                            "symbol": caller_sym,
                            "env_var": env_var,
                        }
                    )
            if caller_sym not in visited_funcs:
                visited_funcs.add(caller_sym)
                queue.append((caller_sym, depth + 1))

    compensated = bool(compensation_sites)
    return {
        "symbol": symbol,
        "env_var": env_var,
        "compensated": compensated,
        "verdict": "falsified" if compensated else "uncompensated",
        "hops_checked": hops_checked,
        "compensation_sites": compensation_sites,
    }


def _caller_compensates_call(
    caller_fn: ast.FunctionDef | ast.AsyncFunctionDef,
    callee_name: str,
    env_var: str,
) -> bool:
    """True if ``env_var`` is read in ``caller_fn`` either outside If branches or in the same branch calling ``callee_name``."""
    # Check top-level non-If statements
    for stmt in caller_fn.body:
        if not isinstance(stmt, ast.If) and _env_var_in_node(stmt, env_var):
            return True
    # Check any block containing both a call to callee_name and env_var
    for node in ast.walk(caller_fn):
        if isinstance(node, ast.If):
            body_mod = ast.Module(body=node.body, type_ignores=[])
            calls_target = any(
                isinstance(sub, ast.Call) and _call_name(sub) == callee_name for sub in ast.walk(body_mod)
            )
            if calls_target and _env_var_in_node(body_mod, env_var):
                return True
    return False


def filter_env_candidates(
    root: str | Path,
    candidates: list[Candidate],
    *,
    max_depth: int = 3,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Filter out env-asymmetry candidates already compensated upstream."""
    surviving: list[Candidate] = []
    falsified: list[dict[str, Any]] = []
    for cand in candidates:
        if cand.evidence.get("rule") == "env-asymmetry" and cand.symbol:
            env_var = cand.evidence.get("env_var")
            res = check_upstream_compensation(
                root,
                cand.symbol,
                env_var=env_var,
                file_hint=cand.file,
                max_depth=max_depth,
            )
            if res["compensated"]:
                falsified.append(
                    {
                        "file": cand.file,
                        "line": cand.line,
                        "symbol": cand.symbol,
                        "env_var": env_var,
                        "category": "compensated-upstream",
                        "reason": f"{env_var} is already handled upstream/setup",
                        "compensation_sites": res["compensation_sites"],
                    }
                )
                continue
        surviving.append(cand)
    return surviving, falsified


def _is_tautological_assert(stmt: ast.Assert) -> str | None:
    test = stmt.test
    if isinstance(test, ast.Constant) and bool(test.value):
        return f"assert {test.value!r} always succeeds"
    if (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Constant)
        and not test.operand.value
    ):
        return "assert not False always succeeds"
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        op = test.ops[0]
        if isinstance(op, (ast.Eq, ast.Is)) and ast.dump(test.left) == ast.dump(test.comparators[0]):
            return f"tautological comparison: {ast.unparse(test)}"
    return None


def _references_caplog_text(stmt: ast.Assert) -> bool:
    for node in ast.walk(stmt):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "text"
            and isinstance(node.value, ast.Name)
            and node.value.id == "caplog"
        ):
            return True
    return False


def _is_assertion_call(call: ast.Call) -> bool:
    name = _call_name(call)
    if not name:
        return False
    if name.startswith("assert") or name in {"raises", "fail", "xfail"}:
        return True
    return False


def _catches_swallowed(handler: ast.ExceptHandler) -> bool:
    if handler.type is not None:
        t_text = ast.unparse(handler.type)
        if not any(tok in t_text for tok in ("Exception", "BaseException", "AssertionError")):
            return False
    has_raise_or_fail = any(
        isinstance(sub, ast.Raise)
        or (isinstance(sub, ast.Call) and _call_name(sub) in {"fail", "append"})
        or (isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant) and sub.value.value == 1)
        for sub in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
    )
    return not has_raise_or_fail


def _extract_patched_targets(node: ast.AST) -> set[str]:
    targets: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        cname = _call_name(sub)
        if cname == "patch" and sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
            targets.add(sub.args[0].value.rsplit(".", 1)[-1])
        elif cname in {"setattr", "object"} and len(sub.args) >= 2:
            if isinstance(sub.args[1], ast.Constant) and isinstance(sub.args[1].value, str):
                targets.add(sub.args[1].value)
    return targets


def _records_into_var(body_node: ast.AST, var_names: set[str]) -> bool:
    """Return True when AST records a failure into ``var_names`` or explicitly fails/exits 1."""
    if not var_names:
        return False
    for sub in ast.walk(body_node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant) and sub.value.value == 1:
            return True
        if (
            isinstance(sub, ast.Call)
            and _call_name(sub) == "exit"
            and sub.args
            and isinstance(sub.args[0], ast.Constant)
            and sub.args[0].value == 1
        ):
            return True
        if (
            isinstance(sub, ast.Raise)
            and isinstance(sub.exc, ast.Call)
            and _call_name(sub.exc) == "SystemExit"
            and sub.exc.args
            and isinstance(sub.exc.args[0], ast.Constant)
            and sub.exc.args[0].value == 1
        ):
            return True
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in {"append", "extend"}
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id in var_names
        ):
            return True
        if (
            isinstance(sub, ast.AugAssign)
            and isinstance(sub.op, ast.Add)
            and isinstance(sub.target, ast.Name)
            and sub.target.id in var_names
        ):
            return True
    return False


def _helper_call_records_failures(
    fn_node: ast.AST,
    module_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> bool:
    """Detect 1-hop helper or ``asyncio.run(helper(failures))`` calls that record failures."""
    candidate_calls: list[ast.Call] = []
    for sub in ast.walk(fn_node):
        if not isinstance(sub, ast.Call):
            continue
        candidate_calls.append(sub)
        if _call_name(sub) == "run" and sub.args and isinstance(sub.args[0], ast.Call):
            candidate_calls.append(sub.args[0])

    for call in candidate_calls:
        cname = _call_name(call)
        if not cname or cname not in module_funcs:
            continue
        helper = module_funcs[cname]
        pos_params = [a.arg for a in [*helper.args.posonlyargs, *helper.args.args]]
        forwarded_params: set[str] = set()
        for idx, arg in enumerate(call.args):
            if isinstance(arg, ast.Name) and arg.id == "failures" and idx < len(pos_params):
                forwarded_params.add(pos_params[idx])
        for kw in call.keywords:
            if kw.arg and isinstance(kw.value, ast.Name) and kw.value.id == "failures":
                forwarded_params.add(kw.arg)
        if forwarded_params and _records_into_var(helper, forwarded_params):
            return True
    return False


def audit_test_vacuity(
    test_file: str | Path,
    *,
    target_symbol: str | None = None,
) -> dict[str, Any]:
    """Audit a Python test or probe script for vacuous / fake-green patterns."""
    path = Path(test_file).resolve()
    if not path.exists():
        raise FileNotFoundError(f"test/probe file does not exist: {path}")
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    module_funcs = {
        stmt.name: stmt
        for stmt in tree.body
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    findings: list[dict[str, Any]] = []
    functions_checked = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        is_test_fn = node.name.startswith("test_")
        is_probe_check = node.name in {"check", "main", "self_check"}
        if not (is_test_fn or is_probe_check):
            continue
        functions_checked += 1

        if node.name == "self_check":
            has_branch_or_assert = any(isinstance(sub, (ast.If, ast.Assert, ast.Raise)) for sub in ast.walk(node))
            if not has_branch_or_assert:
                findings.append(
                    {
                        "kind": "empty-self-check",
                        "function": node.name,
                        "line": node.lineno,
                        "detail": "self_check() has no conditional branch or assertion and always returns pass",
                    }
                )
            continue

        asserts = [sub for sub in ast.walk(node) if isinstance(sub, ast.Assert)]
        assert_calls = [sub for sub in ast.walk(node) if isinstance(sub, ast.Call) and _is_assertion_call(sub)]
        records_failure = _records_into_var(node, {"failures"}) or _helper_call_records_failures(
            node, module_funcs
        )

        if is_test_fn and not asserts and not assert_calls:
            findings.append(
                {
                    "kind": "no-assertions",
                    "function": node.name,
                    "line": node.lineno,
                    "detail": f"{node.name} executes without any assert or pytest.raises check",
                }
            )
        elif node.name in {"check", "main"} and not asserts and not assert_calls and not records_failure:
            findings.append(
                {
                    "kind": "no-assertions",
                    "function": node.name,
                    "line": node.lineno,
                    "detail": f"probe {node.name}() never records failure or exits 1",
                }
            )

        for stmt in asserts:
            taut = _is_tautological_assert(stmt)
            if taut:
                findings.append(
                    {"kind": "tautology-assert", "function": node.name, "line": stmt.lineno, "detail": taut}
                )
            if _references_caplog_text(stmt):
                findings.append(
                    {
                        "kind": "loose-caplog-text",
                        "function": node.name,
                        "line": stmt.lineno,
                        "detail": "asserts substring on caplog.text instead of structured caplog.records",
                    }
                )

        for sub in ast.walk(node):
            if isinstance(sub, ast.Try):
                try_body = ast.Module(body=sub.body, type_ignores=[])
                has_check_in_try = any(isinstance(x, ast.Assert) for x in ast.walk(try_body)) or any(
                    isinstance(x, ast.Call) and _is_assertion_call(x) for x in ast.walk(try_body)
                )
                if has_check_in_try and any(_catches_swallowed(h) for h in sub.handlers):
                    findings.append(
                        {
                            "kind": "swallowed-assertion",
                            "function": node.name,
                            "line": sub.lineno,
                            "detail": "assertion inside try block is swallowed by broad except handler",
                        }
                    )

        if target_symbol and is_test_fn:
            patched = _extract_patched_targets(node)
            if target_symbol in patched:
                findings.append(
                    {
                        "kind": "target-mocked",
                        "function": node.name,
                        "line": node.lineno,
                        "detail": f"test patches the target symbol '{target_symbol}' itself",
                    }
                )
            else:
                called_names = {_call_name(c) for c in ast.walk(node) if isinstance(c, ast.Call)}
                mock_assertions = {n for n in called_names if n and n.startswith("assert_called")}
                if mock_assertions and target_symbol not in called_names:
                    findings.append(
                        {
                            "kind": "target-mocked",
                            "function": node.name,
                            "line": node.lineno,
                            "detail": (
                                f"test asserts mock calls ({', '.join(sorted(mock_assertions))}) "
                                f"without invoking '{target_symbol}'"
                            ),
                        }
                    )

    return {
        "file": str(path),
        "target_symbol": target_symbol,
        "functions_checked": functions_checked,
        "vacuous": bool(findings),
        "findings": findings,
    }
