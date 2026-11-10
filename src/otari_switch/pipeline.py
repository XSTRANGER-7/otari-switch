"""One place that runs each step. The CLI, the web app, the GitHub Action and the MCP server call these."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from pathlib import Path

from otari_switch import policies as pol
from otari_switch import report, rewrite, testgate
from otari_switch.config import Config, ensure_gitignore
from otari_switch.evaluate import evaluate
from otari_switch.models import CallSite, Run, to_dict
from otari_switch.otari_client import OtariClient, SpendMeter
from otari_switch.pr import head
from otari_switch.record import runner
from otari_switch.scan import scan


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def client_for(cfg: Config, meter: SpendMeter | None = None) -> OtariClient:
    return OtariClient(cfg.base_url, cfg.api_key, cfg.data["otari"]["timeout_s"], meter,
                       user=os.environ.get("OTARI_SWITCH_USER") or None)


# --- doctor --------------------------------------------------------------------------------

def doctor(cfg: Config) -> list[dict]:
    """Return checks: [{name, ok (True/False/None), detail, fix}]."""
    checks = []

    def add(name, ok, detail="", fix=""):
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    import sys
    add("Python 3.10 or newer", sys.version_info >= (3, 10), sys.version.split()[0],
        "Install Python 3.10+.")
    key = cfg.api_key
    add("Otari key", bool(key), f"from ${cfg.data['otari']['api_key_env']}" if key else "not set",
        f"export {cfg.data['otari']['api_key_env']}=otk_...  (otari.ai) or tk-... (self-hosted)")
    models: list[dict] = []
    client = client_for(cfg) if key else None
    if client:
        try:
            models = client.models()
            add("Otari reachable", True, f"{cfg.base_url} ({len(models)} models)")
        except Exception as e:  # noqa: BLE001
            add("Otari reachable", False, str(e), "Check the key and region (Global or EU) in otari-switch.yml.")
    catalog = {m.get("id"): m for m in models}
    ids = set(catalog)

    def price(m: dict) -> str:
        p = m.get("pricing") or {}
        if p.get("input_price_per_million") is None:
            return "no price in the catalog"
        return f"${p['input_price_per_million']}/${p.get('output_price_per_million')} per 1M tokens in/out"

    for m in cfg.ev("candidates") or []:
        ok = (m in ids) if ids else None
        add(f"Candidate {m}", ok, price(catalog[m]) if m in catalog else ("" if not ids else "not in /models"),
            "Copy exact ids from the Otari model catalog (GET /models); routing policy names also work.")
    jm = cfg.ev("judge_model")
    add("Judge model", bool(jm) and ((jm in ids) if ids else None), jm or "not set",
        "Set eval.judge_model to a capable model from your catalog.")
    if client and models:
        try:
            b = client.key_budget()
            rem = b.get("remaining_usd")
            detail = ("no dollar cap" if rem is None else f"${rem:.2f} left") if b.get("budgets_enforced")                 else "budgets not enforced for this key"
            ok = None if rem is None else rem >= float(cfg.ev("budget_usd"))
            add("Key budget (GET /key/budget)", ok, detail,
                f"The evaluation cap is ${float(cfg.ev('budget_usd')):.2f}; raise the key's budget or lower eval.budget_usd.")
        except Exception as e:  # noqa: BLE001
            add("Key budget (GET /key/budget)", None, str(e), "")
    prof = cfg.data["privacy"].get("guardrail_profile")
    if cfg.data["privacy"].get("use_otari_guardrail"):
        names: set[str] = set()
        if client and models:
            try:
                names = {p.get("name") or p.get("id") for p in client.guardrail_profiles()}
            except Exception:  # noqa: BLE001
                names = set()
        add("Guardrail profile", bool(prof) and ((prof in names) if names else None), prof or "not set",
            "Set privacy.guardrail_profile to a profile from the Otari dashboard (Guardrails).")
    mcp_id = cfg.data["github"].get("mcp_server_id")
    if mcp_id and client and models:
        try:
            servers = {s.get("id"): s.get("name") for s in client.mcp_servers()}
            add("GitHub MCP server in Otari", mcp_id in servers, servers.get(mcp_id) or "not reachable with this key",
                "Add GitHub's MCP server under Tools > MCP servers and give this key access.")
        except Exception as e:  # noqa: BLE001
            add("GitHub MCP server in Otari", False, str(e), "")
    if cfg.data["otari"].get("policies_api"):
        from otari_switch.policies import master_key
        add("Operator key for routing policies", bool(master_key(cfg)),
            "set" if master_key(cfg) else "not set",
            f"export {cfg.data['otari'].get('master_key_env') or 'OTARI_MASTER_KEY'}=... or set otari.policies_api: false")
    import importlib.util
    for pkg in ("openai", "anthropic"):
        add(f"{pkg} package (for recording)", importlib.util.find_spec(pkg) is not None or None,
            "installed" if importlib.util.find_spec(pkg) else "not installed here",
            f"pip install {pkg} if your app uses it")
    from otari_switch.pr import is_repo
    add("Git repository", is_repo(cfg.root), str(cfg.root), "git init && git add -A && git commit -m init")
    if not cfg.data["github"].get("mcp_server_id"):
        add("GitHub MCP server id", None, "not set (the gh CLI will open the PR)",
            "Add GitHub's MCP server in Otari (Tools, MCP servers) and paste its id under github.mcp_server_id.")
    return checks


# --- scan / record -------------------------------------------------------------------------

def do_scan(cfg: Config) -> dict:
    res = scan(cfg.root)
    data = {"root": res.root, "files_scanned": res.files_scanned, "parse_errors": res.parse_errors,
            "sites": [to_dict(s) for s in res.sites], "clients": [to_dict(c) for c in res.clients]}
    (cfg.work_dir / "scan.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    ensure_gitignore(cfg.root)
    return data


def recording_counts(cfg: Config) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in runner.load_recordings(cfg.root):
        counts[r.site_id] = counts.get(r.site_id, 0) + 1
    return counts


def otari_record_env(cfg: Config, as_model: str | None = None) -> tuple[dict, dict]:
    """Environment that sends the app's own calls through Otari while recording.

    Each call site keeps today's model, under the id Otari serves it as (eval.today_models, else the catalog
    id, e.g. gpt-4.1 -> openai/gpt-4.1). `as_model` records every call on one model instead (for a gateway that
    does not serve today's model, such as a local one) and is saved to eval.today_models so the evaluation
    compares against the same model. Returns (env, {site_id: otari model})."""
    from otari_switch.evaluate import _catalog, resolve_model
    problems = cfg.validate(need_models=False)
    if problems:
        raise ValueError(" ".join(problems))
    sites = load_sites(cfg, fresh=True)
    today = dict(cfg.data["eval"].get("today_models") or {})
    if as_model:
        for s in sites:
            if s.model:
                today[s.model] = as_model
        cfg.data["eval"]["today_models"] = today
        cfg.save()
    catalog = set(_catalog(client_for(cfg)))
    mapping = {}
    for s in sites:
        if not s.evaluable:
            continue
        m = as_model or today.get(s.model or "") or resolve_model(s.model, s.provider, catalog)
        if m:
            mapping[s.id] = m
    env = testgate._env(cfg, mapping)
    env["OTARI_SWITCH_TAGS"] = "tool=otari-switch,role=record"
    return env, mapping


def do_record(cfg: Config, source: str = "tests", arg: str | None = None, fresh: bool = False,
              via_otari: bool = False, as_model: str | None = None) -> dict:
    if not (cfg.work_dir / "scan.json").exists():
        do_scan(cfg)
    out = cfg.work_dir / "recordings.jsonl"
    if fresh and out.exists():
        out.unlink()
    limit = int(cfg.ev("recordings_per_site"))
    detail, problems = "", []
    base_env, mapping = (otari_record_env(cfg, as_model) if (via_otari or as_model) and source != "import"
                         else (None, {}))
    if source == "tests":
        p = runner.run_tests(cfg.root, arg or cfg.data["tests"]["command"], limit, base_env)
        detail = "\n".join(((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-5:])
    elif source == "script":
        if not arg:
            raise ValueError("Give the script path, for example: otari-switch record --script demo.py")
        p = runner.run_script(cfg.root, arg, limit, base_env)
        detail = "\n".join(((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-5:])
    elif source == "import":
        if not arg:
            raise ValueError("Give the log file, for example: otari-switch record --import logs.jsonl")
        sites = json.loads((cfg.work_dir / "scan.json").read_text())["sites"]
        _, problems = runner.import_logs(cfg.root, Path(arg), sites, limit)
    else:
        raise ValueError(f"Unknown source {source}")
    counts = recording_counts(cfg)
    hint = ""
    if not counts and source != "import":
        hint = ("No calls were recorded. If your tests mock the OpenAI or Anthropic SDK, record from a script "
                "that makes real calls (--script) or import production logs (--import).")
    return {"counts": counts, "detail": detail, "problems": problems[:20], "hint": hint,
            "via_otari": bool(mapping), "models": mapping, "privacy": privacy_counts(cfg)}


def privacy_counts(cfg: Config) -> dict:
    """What the privacy check will do to the saved recordings: values masked, recordings skipped (skip mode)."""
    from otari_switch.privacy import mask_recording
    mode = cfg.data["privacy"].get("mode", "mask")
    masked = skipped = 0
    for r in runner.load_recordings(cfg.root):
        _, _, n = mask_recording(r.request, r.response_text)
        if mode == "skip" and n:
            skipped += 1
        elif mode == "mask":
            masked += n
    return {"mode": mode, "masked": masked, "skipped": skipped}


# --- evaluate ------------------------------------------------------------------------------

def load_sites(cfg: Config, fresh: bool = False) -> list[CallSite]:
    """Call sites from scan.json; `fresh` rescans first so line ranges match the code on disk (site ids,
    which recordings are keyed by, stay the same when lines move)."""
    p = cfg.work_dir / "scan.json"
    if fresh or not p.exists():
        do_scan(cfg)
    return [CallSite.from_dict(d) for d in json.loads(p.read_text())["sites"]]


def do_eval(cfg: Config, emit=lambda e: None, cancel: threading.Event | None = None,
            client: OtariClient | None = None, demo: bool = False) -> dict:
    problems = cfg.validate()
    if problems:
        raise ValueError(" ".join(problems))
    demo = demo or os.environ.get("OTARI_SWITCH_DEMO") == "1"
    sites = load_sites(cfg, fresh=True)
    recs = runner.load_recordings(cfg.root)
    if not recs:
        raise ValueError("No recordings yet. Run `otari-switch record` first.")
    started = _now()
    emit({"type": "stage", "stage": "start", "sites": len(sites), "recordings": len(recs)})
    results, meter, findings = evaluate(cfg, sites, recs, emit=emit, cancel=cancel, client=client)
    c = client or client_for(cfg, meter)
    c.meter = meter
    findings += testgate.local_tests(cfg, results, emit, client=c)
    # tests can change verdicts: decide again
    from otari_switch.verdict import decide
    th = {k: cfg.th(k) for k in ("switch_mean", "switch_min_share", "fallback_mean", "fallback_check_rate", "max_latency_ratio", "max_cost_ratio")}
    for r in results:
        if r.candidates:
            decide(r, th)
    findings += testgate.sandbox_checks(cfg, c, results, emit)
    built = pol.build(cfg, results)
    run = Run(id=uuid.uuid4().hex[:10], repo=str(cfg.root), commit=head(cfg.root), started=started,
              finished=_now(), region=cfg.region(), budget_usd=meter.cap, spend_usd=round(meter.spent, 5),
              sites=results, findings=findings, demo=demo)
    data = to_dict(run)
    data["summary"] = report.summarize(data, int(cfg.ev("monthly_calls")))
    data["policies"] = built
    paths = report.write_all(cfg.work_dir, data)
    (cfg.work_dir / "policies.json").write_text(json.dumps(built, indent=2), encoding="utf-8")
    runs = cfg.work_dir / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{data['id']}.json").write_text(json.dumps(data), encoding="utf-8")
    emit({"type": "done", "run_id": data["id"], "summary": data["summary"], "spend": data["spend_usd"],
          "report": str(paths["html"])})
    return data


def latest_run(cfg: Config) -> dict | None:
    p = cfg.work_dir / "report.json"
    return json.loads(p.read_text()) if p.exists() else None


def list_runs(cfg: Config) -> list[dict]:
    d = cfg.work_dir / "runs"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            r = json.loads(p.read_text())
            out.append({"id": r["id"], "started": r["started"], "repo": r["repo"], "summary": r["summary"],
                        "spend_usd": r["spend_usd"], "demo": r.get("demo", False)})
        except (ValueError, KeyError):
            continue
    return out


def get_run(cfg: Config, run_id: str) -> dict | None:
    p = cfg.work_dir / "runs" / f"{run_id}.json"
    if p.exists():
        return json.loads(p.read_text())
    latest = latest_run(cfg)
    return latest if latest and latest["id"] == run_id else None


# --- apply / PR ----------------------------------------------------------------------------

def preview(cfg: Config, run: dict) -> dict:
    policies = run.get("policies") or []
    frontier = {s["site"]["id"]: s["frontier_model"] for s in run["sites"]}
    plans = rewrite.plan(cfg.root, policies, frontier, stage="all")
    files = []
    for rel, f in sorted(plans.items()):
        import difflib
        files.append({"file": rel, "diff": "".join(difflib.unified_diff(
            f.original.splitlines(keepends=True), f.result().splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))})
    return {"files": files, "diff": rewrite.diff(plans, cfg.root),
            "dashboard_steps": pol.dashboard_steps(policies), "policies": policies,
            "title": pr_title(run), "body": report.markdown(run)}


def pr_title(run: dict) -> str:
    s = run["summary"]
    return f"Route AI calls through Otari: {s['moved']} of {s['evaluated']} on open-weight models"


def apply_policies(cfg: Config, run: dict) -> dict:
    applied, steps = pol.apply(cfg, client_for(cfg), run.get("policies") or [])
    if steps:
        (cfg.work_dir / "POLICIES.md").write_text(steps, encoding="utf-8")
    return {"applied": applied, "dashboard_steps": steps}


def apply_code(cfg: Config, run: dict, use_git: bool = True) -> dict:
    policies = run.get("policies") or []
    if use_git:
        from otari_switch.pr import apply_with_commits
        return apply_with_commits(cfg, run, policies)
    frontier = {s["site"]["id"]: s["frontier_model"] for s in run["sites"]}
    plans = rewrite.plan(cfg.root, policies, frontier, stage="all")
    written = rewrite.write(plans)
    rewrite.env_example(cfg.root, policies)
    return {"written": [str(p.relative_to(cfg.root)) for p in written]}


def open_pr(cfg: Config, run: dict, branch: str) -> dict:
    from otari_switch.pr import open_pr as _open
    return _open(cfg, client_for(cfg), branch, pr_title(run), report.markdown(run))
