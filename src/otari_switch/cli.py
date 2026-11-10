"""otari-switch command line. Every command works without the web app."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from otari_switch import __version__
from otari_switch import catalog
from otari_switch import pipeline as pl
from otari_switch.config import REGIONS, ConfigError, ensure_gitignore, load
from otari_switch.otari_client import BudgetReached, OtariError, Unreachable
from otari_switch.report import VERDICT_ICON, VERDICT_LABEL

EXIT_OK, EXIT_USER, EXIT_BUDGET, EXIT_UNREACHABLE = 0, 1, 2, 3

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def ok(t):
    return c(t, "32")


def warn(t):
    return c(t, "33")


def bad(t):
    return c(t, "31")


def dim(t):
    return c(t, "2")


def bold(t):
    return c(t, "1")


VERDICT_COLOR = {"switch": "32", "switch_with_fallback": "33", "keep": "2"}


def _cfg(args):
    try:
        return load(args.dir)
    except ConfigError as e:
        print(bad(str(e)))
        raise SystemExit(EXIT_USER)


def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    cfg = load(root)
    d = cfg.data
    interactive = sys.stdin.isatty() and not args.yes
    print(bold("Otari Switch setup") + dim(f"  ({root})"))
    region = args.region or (input("Region [global/eu] (global): ").strip().lower() or "global" if interactive else "global")
    d["otari"]["base_url"] = REGIONS.get(region, region)
    if args.key_env:
        d["otari"]["api_key_env"] = args.key_env
    key = cfg.api_key
    sug = {"candidates": [], "judge": None}
    if not key:
        print(warn(f"No key in ${d['otari']['api_key_env']}. Set it, then run `otari-switch doctor`."))
    else:
        try:
            models = pl.client_for(cfg).models()
            rows = catalog.describe(models)
            opened = [r for r in rows if r["open_weight"] and r["drop_in"]]
            try:  # suggest models that support what the repo's calls use (JSON output, tools)
                needs = catalog.needs_of(pl.do_scan(cfg))
            except Exception:  # noqa: BLE001
                needs = set()
            sug = catalog.suggest(rows, needs=needs)
            print(ok(f"✓ Connected to {cfg.base_url}: {len(models)} models, {len(opened)} open-weight chat models you can switch to"))
            for r in sorted(opened, key=lambda r: (r["output_per_m"] is None, r["output_per_m"] or 0))[:12]:
                price = (f"${r['input_per_m']:g}/${r['output_per_m']:g} per 1M in/out"
                         if r["output_per_m"] is not None else "no price")
                print(dim(f"  {r['id']}  {price}{'  JSON' if r['json'] else ''}"))
        except OtariError as e:
            print(bad(f"✗ Could not connect: {e}"))
    if args.candidates:
        d["eval"]["candidates"] = [m.strip() for m in args.candidates.split(",") if m.strip()]
    elif not d["eval"]["candidates"]:
        default = ",".join(sug["candidates"])
        raw = input(f"Open-weight candidate model ids, comma separated ({default or 'none suggested'}): ").strip()             if interactive else ""
        d["eval"]["candidates"] = [m.strip() for m in (raw or default).split(",") if m.strip()]
    if args.judge:
        d["eval"]["judge_model"] = args.judge
    elif not d["eval"]["judge_model"]:
        raw = input(f"Judge model id ({sug['judge'] or 'none suggested'}): ").strip() if interactive else ""
        d["eval"]["judge_model"] = raw or sug["judge"] or ""
    if not d["eval"].get("summary_model"):
        d["eval"]["summary_model"] = d["eval"]["judge_model"]
    if args.budget:
        d["eval"]["budget_usd"] = float(args.budget)
    path = cfg.save()
    ensure_gitignore(root)
    print(ok(f"✓ Wrote {path.name}") + dim(" and added .otari-switch/ to .gitignore"))
    print("Next: " + bold("otari-switch run"))
    return EXIT_OK


def cmd_doctor(args) -> int:
    cfg = _cfg(args)
    failed = 0
    for chk in pl.doctor(cfg):
        mark = ok("✓") if chk["ok"] else (dim("–") if chk["ok"] is None else bad("✗"))
        print(f" {mark} {chk['name']}  {dim(chk['detail'])}")
        if chk["ok"] is False:
            failed += 1
            print(f"     {warn('fix:')} {chk['fix']}")
    return EXIT_USER if failed else EXIT_OK


def cmd_scan(args) -> int:
    cfg = _cfg(args)
    data = pl.do_scan(cfg)
    if args.json:
        print(json.dumps(data, indent=2))
        return EXIT_OK
    sites = data["sites"]
    print(bold(f"{len(sites)} AI call site{'s' if len(sites) != 1 else ''} found") +
          dim(f" in {data['files_scanned']} Python files"))
    for s in sites:
        tags = " ".join(f"[{t}]" for t in s["flags"])
        model = s["model"] or dim(s["model_source"])
        note = dim(f"  {s['note']}") if s["note"] else ""
        print(f"  {s['file']}:{s['line']}  {bold(s['function'])}  {s['provider']}/{s['endpoint']}  {model} {dim(tags)}{note}")
    for e in data["parse_errors"]:
        print(warn(f"  could not parse {e}"))
    return EXIT_OK


def cmd_record(args) -> int:
    cfg = _cfg(args)
    source, arg = "tests", args.tests
    if args.script:
        source, arg = "script", args.script
    elif args.import_logs:
        source, arg = "import", args.import_logs
    print(dim(f"Recording from {source}..."))
    try:
        res = pl.do_record(cfg, source, arg, fresh=args.fresh, via_otari=args.via_otari, as_model=args.as_model)
    except ValueError as e:
        print(bad(str(e)))
        return EXIT_USER
    for site, n in sorted(res["counts"].items()):
        print(f"  {ok(str(n)):>4} prompts  {site}")
    if res["hint"]:
        print(warn(res["hint"]))
    if res["detail"] and not res["counts"]:
        print(dim(res["detail"]))
    for p in res["problems"]:
        print(warn(f"  {p}"))
    return EXIT_OK if res["counts"] else EXIT_USER


def _progress(e: dict) -> None:
    t = e.get("type")
    if t == "stage":
        names = {"privacy": "Privacy check", "replay": "Replaying prompts on open-weight models",
                 "tests": "Running your tests on the chosen models", "sandbox": "Contract checks in Otari's sandbox",
                 "licenses": "Checking model licences through Otari's web fetch"}
        if e["stage"] in names:
            print(dim("• " + names[e["stage"]]))
    elif t == "privacy" and (e["masked"] or e["skipped"] or e.get("guardrail")):
        print(dim(f"  masked {e['masked']} value(s), skipped {e['skipped']} recording(s)"))
        g = e.get("guardrail")
        if g:
            print(dim(f"  guardrail {g['profile']}: {g['checked']} checked, {g['blocked']} blocked"))
    elif t == "budget":
        print(warn("  ! ") + e["note"])
    elif t == "license":
        mark = {"open": ok("open"), "restricted": warn("restricted"), "unknown": dim("unverified"),
                "error": bad("error")}.get(e["status"], e["status"])
        print(f"  {mark:<12} {e['model']}" + (dim(f"  {e['license']}") if e.get("license") else ""))
    elif t == "sample" and _TTY:
        total = max(e["total"], 1)
        width = 28
        filled = int(width * e["done"] / total)
        sys.stdout.write(f"\r  [{'#' * filled}{'.' * (width - filled)}] {e['done']}/{total}  ${e['spend']:.4f}   ")
        sys.stdout.flush()
    elif t == "verdict":
        if _TTY:
            sys.stdout.write("\r" + " " * 70 + "\r")
        v = e["verdict"]
        print(f"  {c(VERDICT_ICON[v] + ' ' + VERDICT_LABEL[v], VERDICT_COLOR[v])}  {e['site']}"
              + (dim(f"  → {e['chosen']}") if e["chosen"] else ""))
    elif t == "tests":
        state = {True: ok("passed"), False: bad("failed"), None: dim("not run")}[e["ok"]]
        print(f"  tests: {state}" + (dim(f"  {e.get('detail', '')[:120]}") if e.get("detail") and e["ok"] is not True else ""))


def _print_summary(run: dict) -> None:
    s = run["summary"]
    print()
    print(bold(f"{s['moved']} of {s['evaluated']} calls can switch") +
          dim(f"  · {round(s['open_share'] * 100)}% open-weight · tests {s['tests']}"))
    if s["monthly_savings_usd"] is not None:
        v = s["monthly_savings_usd"]
        word, paint = ("savings", ok) if v >= 0 else ("extra cost", warn)
        print(f"  Estimated {word}: {paint(('-' if v < 0 else '') + '$' + format(abs(v), ',.2f'))} a month "
              + dim(f"(at {s['monthly_calls_assumed']:,} calls)"))
    print(dim(f"  Spent ${run['spend_usd']:.4f} of ${run['budget_usd']:.2f}"))
    for f in run.get("findings", []):
        print(warn("  ! ") + f)


def cmd_licenses(args) -> int:
    from otari_switch import licenses as lic
    cfg = _cfg(args)
    if not cfg.api_key:
        print(bad(f"No key in ${cfg.data['otari']['api_key_env']}."))
        return EXIT_USER
    models = args.models or list(dict.fromkeys((cfg.ev("candidates") or []) + [cfg.ev("judge_model")]))
    models = [m for m in models if m]
    if not models:
        print(bad("No models given and none configured. Example: otari-switch licenses openai/gpt-oss-20b"))
        return EXIT_USER
    print(dim(f"Reading {len(models)} model card(s) through Otari's web fetch..."))
    res = lic.check_many(pl.client_for(cfg), models, cfg.work_dir, refresh=args.refresh)
    for m, r in res["results"].items():
        mark = {"open": ok("open"), "restricted": warn("restricted"), "unknown": dim("unverified"),
                "error": bad("error")}.get(r["status"], r["status"])
        print(f"  {mark:<12} {m}" + (dim(f"  {r['license']}") if r.get("license") else "")
              + dim(f"  {r.get('source_url') or ''}"))
        if r["status"] in {"unknown", "error"}:
            print(dim(f"               {r['detail']}"))
    if not res["available"]:
        print(warn(res["reason"]))
        return EXIT_USER
    return EXIT_OK


def cmd_eval(args) -> int:
    cfg = _cfg(args)
    if args.budget:
        cfg.data["eval"]["budget_usd"] = float(args.budget)
    if args.sites:
        wanted = set(args.sites.split(","))
        for s in pl.load_sites(cfg):
            if s.id not in wanted:
                cfg.data.setdefault("sites", {}).setdefault(s.id, {})["exclude"] = True
    try:
        run = pl.do_eval(cfg, emit=_progress)
    except ValueError as e:
        print(bad(str(e)))
        return EXIT_USER
    except Unreachable as e:
        print(bad(str(e)))
        return EXIT_UNREACHABLE
    except BudgetReached as e:
        print(bad(str(e)))
        return EXIT_BUDGET
    _print_summary(run)
    print(dim(f"  Report: {cfg.work_dir / 'report.html'}"))
    return EXIT_OK


def cmd_report(args) -> int:
    cfg = _cfg(args)
    run = pl.latest_run(cfg)
    if not run:
        print(bad("No run yet. Run `otari-switch eval` first."))
        return EXIT_USER
    from otari_switch import report
    paths = report.write_all(cfg.work_dir, run)
    print(f"{paths['html']}\n{paths['md']}")
    if args.open:
        import webbrowser
        webbrowser.open(paths["html"].as_uri())
    return EXIT_OK


def _confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(warn("Not a terminal: pass --yes to confirm."))
        return False
    return input(prompt + " [y/N] ").strip().lower() in {"y", "yes"}


def cmd_apply(args) -> int:
    cfg = _cfg(args)
    run = pl.latest_run(cfg)
    if not run:
        print(bad("No run yet. Run `otari-switch eval` first."))
        return EXIT_USER
    pv = pl.preview(cfg, run, int(getattr(args, "canary", 0) or 0))
    print(pv["diff"] or dim("No code changes needed."))
    if args.dry_run:
        print(dim("\n(dry run: nothing written)"))
        return EXIT_OK
    if not _confirm("Create the routing policies and write these changes on a new branch?", args.yes):
        return EXIT_USER
    res = pl.apply_policies(cfg, run, int(getattr(args, "canary", 0) or 0))
    if res["dashboard_steps"]:
        print(warn("Create the routing policies in the Otari dashboard: steps in .otari-switch/POLICIES.md"))
    try:
        out = pl.apply_code(cfg, run, use_git=not args.no_git)
    except Exception as e:  # noqa: BLE001
        print(bad(str(e)))
        return EXIT_USER
    if "branch" in out:
        print(ok(f"✓ Branch {out['branch']}") + dim(f" with {len(out['commits'])} commit(s)"))
        (cfg.work_dir / "branch.txt").write_text(out["branch"])
    else:
        print(ok(f"✓ Wrote {len(out['written'])} file(s)"))
    return EXIT_OK


def cmd_pr(args) -> int:
    cfg = _cfg(args)
    run = pl.latest_run(cfg)
    bfile = cfg.work_dir / "branch.txt"
    if not run or not bfile.exists():
        print(bad("Run `otari-switch apply` first."))
        return EXIT_USER
    res = pl.open_pr(cfg, run, bfile.read_text().strip())
    if res.get("ok"):
        print(ok(f"✓ Pull request opened ({res['method']}): {res['url']}"))
        return EXIT_OK
    print(warn(res.get("message", "Could not open the pull request.")))
    if res.get("url"):
        print(res["url"])
    return EXIT_USER


def cmd_run(args) -> int:
    cfg = _cfg(args)
    print(bold("1/4 Scan"))
    args.json = False
    if cmd_scan(args) != EXIT_OK:
        return EXIT_USER
    print(bold("2/4 Record"))
    if not pl.recording_counts(cfg):
        args.tests = args.script = args.import_logs = None
        args.fresh = False
        if cmd_record(args) != EXIT_OK:
            return EXIT_USER
    else:
        print(dim("  using existing recordings (delete .otari-switch/recordings.jsonl to record again)"))
    print(bold("3/4 Evaluate"))
    args.sites = None
    rc = cmd_eval(args)
    if rc != EXIT_OK:
        return rc
    print(bold("4/4 Apply"))
    args.dry_run = False
    args.no_git = False
    rc = cmd_apply(args)
    if rc == EXIT_OK and not args.no_pr:
        return cmd_pr(args)
    return rc


def cmd_ui(args) -> int:
    from otari_switch.server import serve
    serve(Path(args.dir).resolve(), port=args.port, open_browser=not args.no_browser)
    return EXIT_OK


def cmd_demo(args) -> int:
    from otari_switch.demo import setup_demo
    root, fake_url = setup_demo(Path(args.target).resolve(), port=args.fake_port)
    print(ok(f"✓ Demo repo ready at {root}"))
    print(dim(f"  Demo Otari gateway running at {fake_url} (labelled demo data; no key, no cost)"))
    if args.cli:
        args.dir = str(root)
        args.yes = True
        args.no_pr = True
        args.budget = None
        return cmd_run(args)
    from otari_switch.server import serve
    serve(root, port=args.port, open_browser=not args.no_browser)
    return EXIT_OK


def cmd_mcp(args) -> int:
    from otari_switch.mcp_server import main as mcp_main
    return mcp_main(args.dir)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="otari-switch",
                                description="Move AI calls to open-weight models through Otari, with proof.")
    p.add_argument("--version", action="version", version=f"otari-switch {__version__}")
    p.add_argument("-C", "--dir", default=".", help="repo folder (default: current folder)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="create otari-switch.yml and test your Otari key")
    s.add_argument("--region", help="global, eu, or a full base URL")
    s.add_argument("--key-env", help="environment variable holding the Otari key (default OTARI_API_KEY)")
    s.add_argument("--candidates", help="comma-separated open-weight model ids")
    s.add_argument("--judge", help="judge model id")
    s.add_argument("--budget", help="evaluation budget in USD")
    s.add_argument("--yes", action="store_true", help="no prompts")
    s.set_defaults(fn=cmd_init)

    sub.add_parser("doctor", help="check the setup").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("licenses", help="check model licences by reading model cards through Otari's web fetch")
    s.add_argument("models", nargs="*", help="model ids (default: configured candidates and judge)")
    s.add_argument("--refresh", action="store_true", help="ignore cached results")
    s.set_defaults(fn=cmd_licenses)

    s = sub.add_parser("scan", help="list AI call sites")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("record", help="capture real prompts")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--tests", nargs="?", const=None, help="test command (default from config: pytest -q)")
    g.add_argument("--script", help="run a Python script that makes real calls")
    g.add_argument("--import", dest="import_logs", help="import a JSONL log file")
    s.add_argument("--fresh", action="store_true", help="delete old recordings first")
    s.add_argument("--via-otari", action="store_true",
                   help="send the app's calls through Otari while recording (no direct provider key needed)")
    s.add_argument("--as", dest="as_model", metavar="MODEL",
                   help="record every call on this Otari model (implies --via-otari); saved as eval.today_models")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("eval", help="replay, judge, test and decide")
    s.add_argument("--budget", help="override the budget cap in USD")
    s.add_argument("--sites", help="comma-separated call site ids to evaluate")
    s.set_defaults(fn=cmd_eval)

    s = sub.add_parser("report", help="write report.html and report.md")
    s.add_argument("--open", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("apply", help="create policies and rewrite code on a new branch")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--no-git", action="store_true", help="write files without a branch or commits")
    s.add_argument("--canary", type=int, default=0, metavar="PCT",
                   help="start switched call sites as a weighted canary: PCT%% of traffic on the open model")
    s.set_defaults(fn=cmd_apply)

    sub.add_parser("pr", help="push the branch and open a pull request").set_defaults(fn=cmd_pr)

    s = sub.add_parser("run", help="scan, record, eval, then apply after a yes")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--no-pr", action="store_true")
    s.add_argument("--budget")
    s.set_defaults(fn=cmd_run, tests=None, script=None, import_logs=None)

    s = sub.add_parser("ui", help="open the app in your browser")
    s.add_argument("--port", type=int, default=4317)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_ui)

    s = sub.add_parser("demo", help="try it with a sample repo and a local demo Otari gateway, no key needed")
    s.add_argument("target", nargs="?", default="otari-switch-demo")
    s.add_argument("--cli", action="store_true", help="run in the terminal instead of the app")
    s.add_argument("--port", type=int, default=4317)
    s.add_argument("--fake-port", type=int, default=0)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_demo)

    sub.add_parser("mcp", help="start the MCP server (stdio) for coding agents").set_defaults(fn=cmd_mcp)
    return p


def _safe_streams() -> None:
    """Never crash on a ✓ or → when output is piped to a legacy code page (Windows cp1252)."""
    for s in (sys.stdout, sys.stderr):
        try:
            if s.isatty():
                s.reconfigure(errors="replace")
            else:
                s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return EXIT_OK
    try:
        return args.fn(args) or EXIT_OK
    except KeyboardInterrupt:
        print(dim("\nStopped."))
        return EXIT_USER


if __name__ == "__main__":
    raise SystemExit(main())
