"""CLI entry point for bug-vet (and ``bugvet`` alias)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bug_vet import (
    __version__,
    bench,
    collide,
    falsifier,
    gate,
    ledger as ledger_mod,
    mcp_server,
    playbooks,
    report,
    runner,
    setup_agents,
    shrinker,
    verify,
)
from bug_vet.oracles import (
    claims as claims_oracle,
    contracts as contracts_oracle,
    parser_meta as parser_meta_oracle,
    roundtrip as roundtrip_oracle,
    run_all_scans,
    siblings as siblings_oracle,
)
from bug_vet.paths import ensure_run, run_dir

ARTIFACTS = (
    "gate.json",
    "claims.json",
    "siblings-results.json",
    "parser-meta-results.json",
    "contracts-results.json",
    "candidates.json",
    "ledger.json",
    "probes",
    "probe-results.json",
    "probe-baseline.json",
    "verification.json",
    "shrink-results.json",
    "diff-results.json",
    "examples-results.json",
    "callsites-results.json",
    "collision-results.json",
    "issues-results.json",
    "findings.json",
    "REPORT.md",
    "results.sarif",
    "PR.md",
)


def _runs_dir_of(args: argparse.Namespace) -> str | None:
    return getattr(args, "runs_dir_sub", None) or getattr(args, "runs_dir", None)


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def cmd_init(args: argparse.Namespace) -> int:
    path = ensure_run(args.name, runs_dir=_runs_dir_of(args))
    print(f"run directory: {path}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    runs_dir = _runs_dir_of(args)
    if args.mode == "claims":
        res = claims_oracle.run_claims_scan(
            args.name,
            args.path,
            runs_dir=runs_dir,
            include_tests=args.include_tests,
            tier=args.tier,
            max_per_family=args.max_per_family,
            reachable_only=not args.include_unreachable,
        )
        print(f"{res['count']} claims -> {res['claims_file']}")
        for kind, count in sorted(res["by_kind"].items(), key=lambda kv: -kv[1]):
            print(f"  {kind:14s} {count:4d}")
        print(f"{res['obligations']} obligation(s) -> {res['ledger_file']}")
        return 0

    if args.mode == "roundtrip":
        res = roundtrip_oracle.generate_roundtrip_probes(
            args.name,
            args.path,
            import_root=args.import_root,
            runs_dir=runs_dir,
        )
        print(f"{res['count']} round-trip probe(s) generated")
        for item in res["generated"]:
            print(f"  {item['probe']} ({item['obligation_id']}) -> {len(item['pairs'])} pair(s)")
        return 0

    if args.mode == "examples":
        res = claims_oracle.run_examples(
            args.name,
            args.path,
            runs_dir=runs_dir,
            lang=args.lang,
            python=args.python,
            cwd=args.cwd,
            timeout=args.timeout,
        )
        print(f"{res['total']} example(s) -> {res['artifact']}: {res['counts']}")
        for row in res["results"]:
            if row["verdict"] == "FAIL":
                print(f"  FAIL {row['example']} ({Path(row['doc']).name}:{row['line']})")
        return 0

    if args.mode == "callsites":
        if not args.function:
            print("error: --mode callsites requires --function", file=sys.stderr)
            return 2
        res = claims_oracle.scan_callsites(
            args.name,
            args.path,
            args.function,
            runs_dir=runs_dir,
            lang=args.lang,
        )
        print(f"{res['function']}: {len(res['calls'])} call site(s) -> {res['artifact']}")
        for row in res.get("definitions", []):
            if row.get("unwired"):
                print(f"  UNWIRED options in {row['defined_in']}:{row['line']}: {row['unwired']}")
        return 0

    if args.mode == "siblings":
        res = siblings_oracle.run_siblings_scan(
            args.name,
            args.path,
            runs_dir=runs_dir,
            include_ts=not args.no_ts,
            register_obligations=args.register_obligations,
        )
        print(f"{res['count']} sibling candidate(s) -> {res['artifact']}")
        for cand in res["candidates"][:10]:
            print(f"  [{cand['confidence']}] {cand['file']}:{cand['line']} {cand['symbol'] or ''} — {cand['detail'][:90]}")
        return 0

    if args.mode == "parser_meta":
        res = parser_meta_oracle.run_parser_meta_scan(
            args.name,
            args.path,
            runs_dir=runs_dir,
            register_obligations=args.register_obligations,
        )
        print(f"{res['count']} parser-metadata candidate(s) -> {res['artifact']}")
        for cand in res["candidates"][:10]:
            print(f"  [{cand['confidence']}] {cand['file']}:{cand['line']} {cand['symbol'] or ''} — {cand['detail'][:90]}")
        return 0

    if args.mode == "contracts":
        res = contracts_oracle.run_contracts_scan(
            args.name,
            args.path,
            runs_dir=runs_dir,
            register_obligations=args.register_obligations,
        )
        print(f"{res['count']} contract candidate(s) -> {res['artifact']}")
        for cand in res["candidates"][:10]:
            print(f"  [{cand['confidence']}] {cand['file']}:{cand['line']} {cand['symbol'] or ''} — {cand['detail'][:90]}")
        return 0

    if args.mode == "all":
        res = run_all_scans(
            args.name,
            args.path,
            runs_dir=runs_dir,
            include_ts=not args.no_ts,
            apply_falsifier=not args.no_falsifier,
            claim_tier=args.tier,
        )
        print(
            f"{res['count']} candidate(s) (falsified {res['falsified_count']}) -> {res['artifact']}"
        )
        for cand in res["candidates"][:10]:
            print(
                f"  [{cand['oracle']}/{cand['confidence']}] {cand['file']}:{cand['line'] or '-'} "
                f"{cand['symbol'] or ''} — {cand['detail'][:80]}"
            )
        return 0

    print(f"unknown scan mode: {args.mode}", file=sys.stderr)
    return 2


def cmd_new_probe(args: argparse.Namespace) -> int:
    target = runner.scaffold_probe(
        args.name,
        args.claim,
        runs_dir=_runs_dir_of(args),
        family=args.family,
        ecosystem=args.ecosystem,
        description=args.description,
        out=args.out,
    )
    print(f"scaffolded {target}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    res = runner.run_probes(
        args.name,
        runs_dir=_runs_dir_of(args),
        python=args.python,
        cwd=args.cwd,
        env_pairs=args.env,
        timeout=args.timeout,
    )
    for row in res["results"]:
        sc = "" if row["has_self_check"] else " [NO self_check]"
        print(f"{row['verdict']:12s} {row['probe']}{sc}")
    print(f"\n{res['count']} probe(s), {res['failures']} needing attention -> {res['artifact']}")
    print(res["ledger_summary"])
    return 0


def cmd_shrink(args: argparse.Namespace) -> int:
    res = shrinker.run_shrink(
        args.name,
        args.script,
        args.input,
        runs_dir=_runs_dir_of(args),
        python=args.python,
        cwd=args.cwd,
        env_pairs=args.env,
        timeout=args.timeout,
        max_runs=args.max_runs,
    )
    print(f"shrunk {res['original_bytes']} -> {res['minimal_bytes']} bytes in {res['candidates_tried']} runs")
    print(f"minimal: {res['minimal_file']}")
    print(json.dumps(res["minimal"], ensure_ascii=False, indent=2)[:2000])
    if not res["confirmed"]:
        print("FAIL: minimal input no longer reproduces", file=sys.stderr)
        return 1
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    res = verify.snapshot_baseline(
        args.name,
        runs_dir=_runs_dir_of(args),
        python=args.python,
        cwd=args.cwd,
        env_pairs=args.env,
        timeout=args.timeout,
    )
    for row in res["results"]:
        print(f"{row['verdict']:12s} {row['probe']}")
    print(f"\nbaseline: {res['count']} probe(s), {len(res['failing'])} failing -> {res['artifact']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    res = verify.verify_fix(
        args.name,
        runs_dir=_runs_dir_of(args),
        python=args.python,
        cwd=args.cwd,
        env_pairs=args.env,
        timeout=args.timeout,
    )
    print(f"fixed (red -> green): {len(res['fixed'])}")
    for name in res["fixed"]:
        print(f"  {res['before'][name]} -> PASS  {name}")
    if res["still_failing"]:
        print(f"still failing: {res['still_failing']}")
    if res["regressed"]:
        print(f"REGRESSED: {res['regressed']}")
    print(f"artifact: {res['artifact']}")
    return 0 if res["ok"] else 1


def cmd_test_evidence(args: argparse.Namespace) -> int:
    res = verify.run_test_evidence(
        args.name,
        args.obligation,
        args.cmd,
        runs_dir=_runs_dir_of(args),
        python=args.python,
        cwd=args.cwd,
        env_pairs=args.env,
        timeout=args.timeout,
    )
    print(f"test-evidence {res['verdict']} ({res['authority']}) -> {res['evidence_id']}")
    print(res["ledger_summary"])
    return 0 if res["exit_code"] == 0 else 1


def cmd_diff(args: argparse.Namespace) -> int:
    res = verify.run_diff(
        args.name,
        args.scenario,
        args.inputs,
        runs_dir=_runs_dir_of(args),
        old_python=args.old_python,
        new_python=args.new_python,
        pip_version=args.pip_version,
        pip_package=args.pip_package,
        pip_python=args.pip_python,
        old_env=args.old_env,
        new_env=args.new_env,
        cwd=args.cwd,
        timeout=args.timeout,
        max_diffs=args.max_diffs,
        obligation_id=args.obligation,
    )
    print(f"{res['different']} differing result(s) -> {res['artifact']}")
    if "ledger_summary" in res:
        print(res["ledger_summary"])
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    runs_dir = _runs_dir_of(args)
    if args.promote or args.promote_structural:
        path = run_dir(args.name, runs_dir=runs_dir)
        promoted = ledger_mod.promote_candidates(
            path,
            indices=args.promote,
            structural_only=args.promote_structural,
            required=args.required,
        )
        for ob in promoted:
            loc = f"{ob['source']}:{ob['line']}" if ob.get("line") else ob.get("source", "")
            print(f"promoted {ob['id']} [{ob['family']}] -> {loc}")
        state = ledger_mod.load(path)
        print(ledger_mod.summary(state, probe_dir=path / "probes"))
        return 0

    if args.add:
        path = ensure_run(args.name, runs_dir=runs_dir)
        ob = ledger_mod.add_obligation(
            path,
            {
                "id": args.ob_id,
                "text": args.add,
                "source": args.source or "",
                "line": args.line,
                "symbol": args.symbol,
                "kind": args.family,
                "origin": "manual",
                "visibility": "public",
            },
            required=args.required,
        )
        print(f"added {ob['id']} [{ob['family']}] ({ob['required_observation']})")
        state = ledger_mod.load(path)
        print(ledger_mod.summary(state, probe_dir=path / "probes"))
        return 0

    path = run_dir(args.name, runs_dir=runs_dir)
    if not path.exists():
        print(f"error: no run at {path}", file=sys.stderr)
        return 2
    state = ledger_mod.load(path)
    if not state["obligations"]:
        print(f"no obligations under {path}", file=sys.stderr)
        return 2
    report = ledger_mod.evaluate(state, probe_dir=path / "probes")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(f"state: {report['state']}  ({ledger_mod.summary(state, probe_dir=path / 'probes')})")
    rows = (
        state["obligations"]
        if args.all
        else [ob for ob in state["obligations"] if ob.get("disposition") != "probed-pass"]
    )
    for ob in rows[: args.limit]:
        print(
            f"  {ob['id']:12s} {ob['disposition']:12s} [{ob['family']:14s}] "
            f"{ob['source']}:{ob['line']}  {ob['text'][:70]}"
        )
    return 0


def cmd_falsify(args: argparse.Namespace) -> int:
    if args.check_compensation:
        if not args.symbol:
            print("error: --check-compensation requires --symbol", file=sys.stderr)
            return 2
        res = falsifier.check_upstream_compensation(
            args.check_compensation,
            args.symbol,
            env_var=args.env_var,
            file_hint=args.location,
            max_depth=args.max_depth,
        )
        print(f"compensation verdict: {res['verdict']} (hops={res['hops_checked']})")
        for site in res["compensation_sites"]:
            print(f"  {site['direction']}: {site['file']}:{site['line']} {site['symbol']} ({site['env_var']})")
        return 0

    if args.check_vacuity:
        res = falsifier.audit_test_vacuity(args.check_vacuity, target_symbol=args.target_symbol)
        print(f"vacuous={res['vacuous']} ({res['functions_checked']} function(s) checked)")
        for item in res["findings"]:
            print(f"  {item['kind']} {item['function']}:{item['line']} — {item['detail']}")
        return 1 if res["vacuous"] else 0

    if not args.name or not args.obligation or not args.reason:
        print("error: recording falsification requires <name>, --obligation, and --reason", file=sys.stderr)
        return 2
    path = run_dir(args.name, runs_dir=_runs_dir_of(args))
    state, ev = ledger_mod.record_falsification(
        path,
        args.obligation,
        args.reason,
        args.category,
        source_location=args.location,
    )
    print(f"falsified {ev['obligation_id']} ({args.category}) -> {ev['id']}")
    print(ledger_mod.summary(state, probe_dir=path / "probes"))
    return 0


def cmd_playbook(args: argparse.Namespace) -> int:
    query = args.get_query or args.query
    if args.list or not query:
        entries = playbooks.list_playbooks()
        if args.json:
            print(json.dumps(entries, indent=2, ensure_ascii=False))
            return 0
        for entry in entries:
            print(f"{entry['id']:24s} [{entry['family']:20s}] {entry['title']}")
        return 0

    pb = playbooks.get_playbook(query)
    if args.json:
        print(json.dumps(pb, indent=2, ensure_ascii=False))
        return 0
    print(pb["content"])
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    res = report.generate_report(
        args.name,
        runs_dir=_runs_dir_of(args),
        formats=formats,
        include_pr=args.pr,
        include_candidates=args.include_candidates,
    )
    print(
        f"report ({res['state']}): {res['finding_count']} finding(s), "
        f"{res['falsified_count']} falsified, {res['sarif_results']} SARIF result(s)"
    )
    for fmt, out_path in res["artifacts"].items():
        print(f"  {fmt:10s} -> {out_path}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    cases_file = args.cases
    if not cases_file:
        local = Path.cwd() / "bench" / "cases.json"
        bundled = Path(__file__).resolve().parents[2] / "bench" / "cases.json"
        cases_file = str(local if local.exists() else bundled)
    res = bench.run_benchmark(
        cases_file,
        case_id=args.case,
        json_out=args.json_out,
        markdown_out=args.markdown_out,
    )
    s = res["summary"]
    print(
        f"ContractBench: recall@5={s['recall_at_5_count']}/{s['total_cases']} ({s['recall_at_5_pct']}%), "
        f"top1={s['top_1_count']}/{s['total_cases']}, "
        f"post-fix cleared={s['post_fix_cleared_count']}/{s['structural_hit_count']}, "
        f"env falsified={s['total_falsified_filtered']}"
    )
    for row in res["cases"]:
        pre = row["pre_fix"]
        post = row["post_fix"]
        tag = "HIT " if pre["recall_at_5"] else "MISS"
        rank = f"rank={pre['rank_of_first_hit']}" if pre["rank_of_first_hit"] else "rank=-"
        print(
            f"  {tag} {row['id']:32s} {rank:8s} oracles={pre['firing_oracles']} "
            f"cands={pre['total_candidates']} post_cleared={post['cleared']}"
        )
    if "json_out" in res:
        print(f"json -> {res['json_out']}")
    if "markdown_out" in res:
        print(f"md   -> {res['markdown_out']}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    res = gate.evaluate_gate(
        args.name,
        runs_dir=_runs_dir_of(args),
        require_verification=args.require_verification,
        require_collision=args.require_collision,
    )
    counts = res["counts"]
    print(
        f"obligations  fail={counts.get('probed-fail', 0)} pass={counts.get('probed-pass', 0)} "
        f"falsified={counts.get('falsified', 0)} unknown={counts.get('unknown', 0)} "
        f"active={counts.get('active', 0)}"
    )
    for finding in res["findings"]:
        weak = " (weak contract: prose only)" if finding["weak_contract"] else ""
        print(f"FINDING {finding['id']} [{finding['family']}] {finding['source']}:{finding['line']}{weak}")
    for blocker in res["blockers"][:10]:
        print(f"BLOCK   {blocker['kind']}: {blocker.get('obligation') or blocker.get('probe') or ''}")
    print(f"\ngate: {res['state'].upper()} -> {res['artifact']}")
    return int(res["exit_code"])


def cmd_issues(args: argparse.Namespace) -> int:
    res = collide.scan_issues(
        args.repo,
        runs_dir=_runs_dir_of(args),
        name=args.name,
        label=args.label,
        limit=args.limit,
        show=args.show,
    )
    print(f"{res['total']} open issue(s), {res['unclaimed']} unclaimed -> {res['artifact']}")
    for row in res["rows"]:
        state = "UNCLAIMED" if row["unclaimed"] else "claimed"
        links = ",".join(f"#{ref['number']}" for ref in row["linked_prs"]) or "-"
        print(f"  {state:9s} #{row['number']:<5d} {row['created']} links={links:12s} {row['title'][:70]}")
    return 0


def cmd_collide(args: argparse.Namespace) -> int:
    res = collide.check_collision(
        args.repo,
        issue=args.issue,
        keywords=args.keyword,
        files=args.file,
        runs_dir=_runs_dir_of(args),
        name=args.name,
        require_clear=args.require_clear,
    )
    print(f"verdict: {res['verdict']}")
    for finding in res["findings"]:
        print(f" - {finding['kind']}: #{finding['number']} {finding['title']}")
    print(f"artifact: {res['artifact']}")
    return 1 if res["blocked"] else 0


def cmd_status(args: argparse.Namespace) -> int:
    path = run_dir(args.name, runs_dir=_runs_dir_of(args))
    if not path.exists():
        print(f"no run named {args.name!r} at {path}", file=sys.stderr)
        return 2
    print(f"run: {path}")
    for artifact in ARTIFACTS:
        target = path / artifact
        if target.is_dir():
            files = sorted(p.name for p in target.iterdir())
            print(f"  {artifact:22s} dir ({len(files)} files)")
        elif target.exists():
            print(f"  {artifact:22s} {target.stat().st_size} bytes")
        else:
            print(f"  {artifact:22s} -")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    res = setup_agents.run_setup(
        client=args.client,
        apply=args.apply,
        project_dir=args.project_dir,
        plugin_dir=args.plugin_dir,
        command=args.command_bin,
        python_bin=args.python_bin,
    )
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    for name, cfg in res["configs"].items():
        print(f"=== {name} ({cfg['target']}) ===")
        if cfg["format"] == "json":
            print(json.dumps(cfg["payload"], indent=2, ensure_ascii=False))
        else:
            print(cfg["snippet"].rstrip())
    if res["applied"]:
        print("\nApplied:")
        for item in res["applied"]:
            bak = f" (backup: {item['backup']})" if item.get("backup") else ""
            print(f"  wrote {item['file']}{bak}")
    elif not args.apply:
        print(
            "\nPreview only (no files modified). Pass --apply --project-dir <path> "
            "(or --plugin-dir <path> for antigravity) to write configs."
        )
    return 0


def cmd_mcp(_args: argparse.Namespace) -> int:
    return mcp_server.serve_stdio()


def build_parser() -> argparse.ArgumentParser:
    runs_parent = argparse.ArgumentParser(add_help=False)
    runs_parent.add_argument(
        "--runs-dir",
        dest="runs_dir_sub",
        default=None,
        help="base runs directory (default: BUG_VET_RUNS_DIR or ./.bug-vet/runs)",
    )

    parser = argparse.ArgumentParser(
        prog="bug-vet",
        description="bug-vet: vet silent contract bugs with executable red/green proof",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="base runs directory (default: BUG_VET_RUNS_DIR or ./.bug-vet/runs)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", parents=[runs_parent], help="create a run directory")
    p_init.add_argument("name")
    p_init.set_defaults(func=cmd_init)

    p_scan = sub.add_parser("scan", parents=[runs_parent], help="run structural and contract scans")
    p_scan.add_argument("path")
    p_scan.add_argument("--name", required=True)
    p_scan.add_argument(
        "--mode",
        choices=["claims", "roundtrip", "examples", "callsites", "siblings", "parser_meta", "contracts", "all"],
        default="claims",
    )
    p_scan.add_argument("--tier", choices=["A", "B", "all"], default="A")
    p_scan.add_argument("--max-per-family", type=int, default=40)
    p_scan.add_argument("--include-tests", action="store_true")
    p_scan.add_argument("--include-unreachable", action="store_true")
    p_scan.add_argument("--register-obligations", action="store_true")
    p_scan.add_argument("--no-ts", action="store_true")
    p_scan.add_argument("--no-falsifier", action="store_true")
    p_scan.add_argument("--import-root", help="package import root for roundtrip")
    p_scan.add_argument("--function", help="target function for callsites")
    p_scan.add_argument("--lang", default="python")
    p_scan.add_argument("--python")
    p_scan.add_argument("--cwd")
    p_scan.add_argument("--timeout", type=int, default=120)
    p_scan.set_defaults(func=cmd_scan)

    p_new = sub.add_parser("new-probe", parents=[runs_parent], help="scaffold a standalone probe")
    p_new.add_argument("name")
    p_new.add_argument("--claim", required=True)
    p_new.add_argument("--family", default="roundtrip")
    p_new.add_argument("--ecosystem", default="python")
    p_new.add_argument("--description")
    p_new.add_argument("--out")
    p_new.set_defaults(func=cmd_new_probe)

    p_probe = sub.add_parser("probe", parents=[runs_parent], help="run probes and record ledger evidence")
    p_probe.add_argument("name")
    p_probe.add_argument("--python")
    p_probe.add_argument("--cwd")
    p_probe.add_argument("--timeout", type=int, default=300)
    p_probe.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_probe.set_defaults(func=cmd_probe)

    p_shrink = sub.add_parser("shrink", parents=[runs_parent], help="delta-debug a failing JSON input")
    p_shrink.add_argument("name")
    p_shrink.add_argument("--script", required=True)
    p_shrink.add_argument("--input", required=True)
    p_shrink.add_argument("--python")
    p_shrink.add_argument("--cwd")
    p_shrink.add_argument("--timeout", type=int, default=120)
    p_shrink.add_argument("--max-runs", type=int, default=300)
    p_shrink.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_shrink.set_defaults(func=cmd_shrink)

    p_base = sub.add_parser("baseline", parents=[runs_parent], help="snapshot pre-fix probe verdicts")
    p_base.add_argument("name")
    p_base.add_argument("--python")
    p_base.add_argument("--cwd")
    p_base.add_argument("--timeout", type=int, default=300)
    p_base.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_base.set_defaults(func=cmd_baseline)

    p_verify = sub.add_parser("verify", parents=[runs_parent], help="compare post-fix probes to baseline")
    p_verify.add_argument("name")
    p_verify.add_argument("--python")
    p_verify.add_argument("--cwd")
    p_verify.add_argument("--timeout", type=int, default=300)
    p_verify.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_verify.set_defaults(func=cmd_verify)

    p_test = sub.add_parser("test-evidence", parents=[runs_parent], help="run a repo test cmd for test-authority evidence")
    p_test.add_argument("name")
    p_test.add_argument("--obligation", required=True)
    p_test.add_argument("--cmd", required=True)
    p_test.add_argument("--python")
    p_test.add_argument("--cwd")
    p_test.add_argument("--timeout", type=int, default=300)
    p_test.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_test.set_defaults(func=cmd_test_evidence)

    p_diff = sub.add_parser("diff", parents=[runs_parent], help="compare a scenario across two versions")
    p_diff.add_argument("name")
    p_diff.add_argument("--scenario", required=True)
    p_diff.add_argument("--inputs", required=True)
    p_diff.add_argument("--old-python")
    p_diff.add_argument("--new-python")
    p_diff.add_argument("--cwd")
    p_diff.add_argument("--timeout", type=int, default=600)
    p_diff.add_argument("--pip-version")
    p_diff.add_argument("--pip-package")
    p_diff.add_argument("--pip-python", default="3.12")
    p_diff.add_argument("--old-env", action="append", metavar="KEY=VALUE")
    p_diff.add_argument("--new-env", action="append", metavar="KEY=VALUE")
    p_diff.add_argument("--max-diffs", type=int, default=50)
    p_diff.add_argument("--obligation")
    p_diff.set_defaults(func=cmd_diff)

    p_ledger = sub.add_parser("ledger", parents=[runs_parent], help="inspect or register obligations in ledger")
    p_ledger.add_argument("name")
    p_ledger.add_argument("--json", action="store_true")
    p_ledger.add_argument("--all", action="store_true")
    p_ledger.add_argument("--limit", type=int, default=25)
    p_ledger.add_argument("--promote", action="append", type=int, metavar="N", help="1-based candidate index from candidates.json")
    p_ledger.add_argument("--promote-structural", action="store_true", help="promote all non-claims candidates")
    p_ledger.add_argument("--add", metavar="CLAIM_TEXT", help="register a manual obligation in ledger.json")
    p_ledger.add_argument("--id", dest="ob_id", help="optional obligation/claim id for --add")
    p_ledger.add_argument("--source", help="source file path for --add")
    p_ledger.add_argument("--line", type=int, help="line number for --add")
    p_ledger.add_argument("--symbol", help="symbol name for --add")
    p_ledger.add_argument("--family", default="contract", help="bug family for --add")
    p_ledger.add_argument("--required", choices=ledger_mod.AUTHORITIES, default="probe", help="required authority for --add/--promote")
    p_ledger.set_defaults(func=cmd_ledger)

    p_falsify = sub.add_parser(
        "falsify",
        parents=[runs_parent],
        help="run upstream compensation/vacuity checks or record a falsified obligation",
    )
    p_falsify.add_argument("name", nargs="?")
    p_falsify.add_argument("--obligation")
    p_falsify.add_argument("--reason")
    p_falsify.add_argument("--category", default="false-positive")
    p_falsify.add_argument("--location")
    p_falsify.add_argument("--check-compensation", help="repo root to trace upstream compensation")
    p_falsify.add_argument("--symbol")
    p_falsify.add_argument("--env-var")
    p_falsify.add_argument("--max-depth", type=int, default=3)
    p_falsify.add_argument("--check-vacuity", help="test or probe Python file to audit")
    p_falsify.add_argument("--target-symbol")
    p_falsify.set_defaults(func=cmd_falsify)

    p_pb = sub.add_parser("playbook", help="list or inspect compiled bug-family playbooks")
    p_pb.add_argument("query", nargs="?")
    p_pb.add_argument("--list", action="store_true")
    p_pb.add_argument("--get", dest="get_query")
    p_pb.add_argument("--json", action="store_true")
    p_pb.set_defaults(func=cmd_playbook)

    p_rep = sub.add_parser("report", parents=[runs_parent], help="render REPORT.md, findings.json, SARIF, and PR.md")
    p_rep.add_argument("name")
    p_rep.add_argument("--formats", default="markdown,json,sarif")
    p_rep.add_argument("--pr", action="store_true")
    p_rep.add_argument("--include-candidates", action="store_true")
    p_rep.set_defaults(func=cmd_report)

    p_bench = sub.add_parser("bench", help="run ContractBench pre-fix recall & post-fix negative control")
    p_bench.add_argument("--cases")
    p_bench.add_argument("--case")
    p_bench.add_argument("--json-out")
    p_bench.add_argument("--markdown-out")
    p_bench.set_defaults(func=cmd_bench)

    p_gate = sub.add_parser("gate", parents=[runs_parent], help="run fail-closed readiness gate")
    p_gate.add_argument("name")
    p_gate.add_argument("--require-verification", action="store_true")
    p_gate.add_argument("--require-collision", action="store_true")
    p_gate.set_defaults(func=cmd_gate)

    p_issues = sub.add_parser("issues", parents=[runs_parent], help="list open unclaimed GitHub issues")
    p_issues.add_argument("repo")
    p_issues.add_argument("--name")
    p_issues.add_argument("--label")
    p_issues.add_argument("--limit", type=int, default=50)
    p_issues.add_argument("--show", type=int, default=25)
    p_issues.set_defaults(func=cmd_issues)

    p_col = sub.add_parser("collide", parents=[runs_parent], help="check PR/timeline collisions on GitHub")
    p_col.add_argument("repo")
    p_col.add_argument("--issue", type=int)
    p_col.add_argument("--keyword", action="append")
    p_col.add_argument("--file", action="append")
    p_col.add_argument("--name")
    p_col.add_argument("--require-clear", action="store_true")
    p_col.set_defaults(func=cmd_collide)

    p_status = sub.add_parser("status", parents=[runs_parent], help="show artifacts in a run")
    p_status.add_argument("name")
    p_status.set_defaults(func=cmd_status)

    p_setup = sub.add_parser("setup", help="preview or apply MCP/plugin config for coding agents")
    p_setup.add_argument("--client", choices=["all", *setup_agents.SUPPORTED_CLIENTS], default="all")
    p_setup.add_argument("--apply", action="store_true")
    p_setup.add_argument("--project-dir")
    p_setup.add_argument("--plugin-dir", help="target directory for installing the Antigravity plugin bundle")
    p_setup.add_argument("--command-bin", default="bug-vet")
    p_setup.add_argument("--python-bin", help="Python interpreter path baked into Antigravity hooks.json")
    p_setup.add_argument("--json", action="store_true")
    p_setup.set_defaults(func=cmd_setup)

    p_mcp = sub.add_parser("mcp", help="start zero-dependency stdio MCP server")
    p_mcp.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "mcp":
        _configure_stdio_utf8()
    try:
        return int(args.func(args))
    except (FileNotFoundError, NotADirectoryError, ValueError, KeyError, LookupError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
