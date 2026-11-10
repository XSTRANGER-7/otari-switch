"""Prove the app still works on the chosen models.

1. Local: run the repo's own tests with every call routed through Otari to its chosen model.
2. Otari sandbox: re-run the JSON and schema contract checks in Otari's code-execution sandbox.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

from otari_switch.config import Config
from otari_switch.judge import request_schema
from otari_switch.models import SWITCH_WITH_FALLBACK, SiteResult
from otari_switch.otari_client import BudgetReached, OtariClient, OtariError
from otari_switch.record.runner import load_recordings


def _env(cfg: Config, mapping: dict[str, str]) -> dict:
    env = dict(os.environ)
    base = cfg.base_url.rstrip("/")
    key = cfg.api_key or ""
    env.update({
        "OPENAI_BASE_URL": base, "OPENAI_API_KEY": key,
        "ANTHROPIC_BASE_URL": base.rsplit("/v1", 1)[0], "ANTHROPIC_API_KEY": key,
        "OTARI_SWITCH_OVERRIDE": json.dumps(mapping), "OTARI_SWITCH_BASE_URL": base,
        "OTARI_SWITCH_API_KEY": key, "OTARI_SWITCH_ROOT": str(cfg.root),
        "OTARI_SWITCH_TAGS": "tool=otari-switch,role=tests",
        "OTARI_SWITCH_END_USER": os.environ.get("OTARI_SWITCH_END_USER") or cfg.data["otari"].get("end_user") or "",
        "OTARI_SWITCH_END_USER_BUDGET": (os.environ.get("OTARI_SWITCH_END_USER_BUDGET")
                                         or cfg.data["otari"].get("end_user_budget") or ""),
    })
    env.pop("OTARI_SWITCH_RECORD", None)
    pkg_parent = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join([pkg_parent, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return env


def _run(cfg: Config, mapping: dict[str, str]) -> tuple[bool | None, str]:
    args = shlex.split(cfg.data["tests"].get("command") or "")
    if not args:
        return None, "no test command configured"
    if args[0] == "pytest":
        args = [sys.executable, "-m", "pytest", "-p", "otari_switch.record.pytest_plugin"] + args[1:]
    try:
        p = subprocess.run(args, cwd=cfg.root, env=_env(cfg, mapping), capture_output=True, text=True,
                           timeout=900)
    except subprocess.TimeoutExpired:
        return False, "tests timed out after 15 minutes"
    except FileNotFoundError as e:
        return None, f"test command not found: {e}"
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode == 5:
        return None, "no tests were collected"
    if "No module named 'openai'" in out or "No module named 'anthropic'" in out:
        return None, "the openai/anthropic package is not installed in this environment"
    tail = "\n".join(out.strip().splitlines()[-6:])
    return p.returncode == 0, tail


def _gate_spend(client: OtariClient | None, since: float) -> float | None:
    """What the test gate's own calls cost, read back from Otari's usage (tagged role=tests).

    Those calls happen inside the app's test process, so the client's spend meter never sees them. Usage rows are
    written in the background, so give them a moment to settle."""
    if client is None:
        return None
    import time as _t
    _t.sleep(2.0)
    iso = lambda t: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(t))  # noqa: E731
    try:
        s = client.key_usage_summary(iso(since - 5), iso(_t.time() + 5), bucket="hour",
                                     dimensions=["none"], tags=["tool:otari-switch", "role:tests"])
    except OtariError:
        return None
    cost = (s.get("totals") or {}).get("cost")
    return float(cost) if isinstance(cost, (int, float)) else None


def local_tests(cfg: Config, results: list[SiteResult], emit=lambda e: None,
                client: OtariClient | None = None) -> list[str]:
    """Run the repo's tests with every AI call going through Otari.

    Switched call sites use their chosen open model; every other call site uses today's model under its Otari
    id (Otari does not promise to resolve a bare provider name such as `gpt-4.1`). If the suite fails, a
    baseline run on today's models tells apart "the switch broke it" from "it was already failing".
    """
    findings: list[str] = []
    base = {r.site.id: r.frontier_model for r in results if r.frontier_model}
    chosen: dict = {}
    for r in results:
        if not r.chosen:
            continue
        guarded = r.verdict == SWITCH_WITH_FALLBACK and "json" in r.site.flags and r.frontier_model
        chosen[r.site.id] = {"model": r.chosen, "guard": r.frontier_model} if guarded else r.chosen
    if not chosen or not cfg.data["tests"].get("run_local", True):
        return findings
    meter = client.meter if client is not None else None
    if meter is not None and meter.cap - meter.spent < max(0.05 * meter.cap, 0.01):
        findings.append(f"Repo tests not run: less than 5% of the ${meter.cap:.2f} evaluation budget is left. "
                        "Raise eval.budget_usd to include the test gate.")
        emit({"type": "tests", "ok": None, "detail": "budget"})
        return findings
    emit({"type": "stage", "stage": "tests"})
    import time as _t
    started = _t.time()
    try:
        return _local_tests(cfg, results, emit, findings, base, chosen)
    finally:
        spent = _gate_spend(client, started)
        if spent and meter is not None:
            with meter._lock:  # counted against the cap like every other call
                meter.spent += spent
                meter.by_role["tests"] = meter.by_role.get("tests", 0.0) + spent
            emit({"type": "tests_spend", "spend": round(spent, 6)})


def _local_tests(cfg, results, emit, findings, base, chosen) -> list[str]:
    ok, tail = _run(cfg, {**base, **chosen})
    if ok is None:
        findings.append(f"Repo tests not run: {tail}.")
        emit({"type": "tests", "ok": None, "detail": tail})
        return findings

    def mark(site_id: str, value):
        for r in results:
            if r.site.id == site_id:
                for c in r.candidates:
                    if c.model == r.chosen:
                        c.tests_passed = value

    if ok:
        for sid in chosen:
            mark(sid, True)
        emit({"type": "tests", "ok": True})
        return findings
    base_ok, base_tail = _run(cfg, base)
    if not base_ok:
        findings.append("Repo tests also fail on today's models through Otari, so the test gate is inconclusive: "
                        + (base_tail.splitlines()[-1] if base_tail else "see your test output") + ".")
        emit({"type": "tests", "ok": None, "detail": "tests fail on today's models too"})
        return findings
    # find the call site that breaks the tests
    for sid, model in chosen.items():
        site_ok, _ = _run(cfg, {**base, sid: model})
        mark(sid, site_ok)
        if site_ok is False:
            name = model["model"] if isinstance(model, dict) else model
            findings.append(f"Tests fail when {sid} uses {name}" + (" even with the JSON guard." if isinstance(model, dict) else "."))
    emit({"type": "tests", "ok": False, "detail": tail})
    return findings


SANDBOX_TEMPLATE = textwrap.dedent('''
    import json
    CASES = json.loads(%r)
    def check(value, schema):
        if schema is None:
            return True
        t = schema.get("type")
        if t == "object":
            if not isinstance(value, dict):
                return False
            for k in schema.get("required", []):
                if k not in value:
                    return False
            for k, sub in (schema.get("properties") or {}).items():
                if k in value and isinstance(sub, dict) and "enum" in sub and value[k] not in sub["enum"]:
                    return False
        if t == "array" and not isinstance(value, list):
            return False
        return True
    out = {}
    for c in CASES:
        try:
            v = json.loads(c["text"].strip().removeprefix("```json").removesuffix("```"))
            ok = check(v, c["schema"])
        except Exception:
            ok = False
        out.setdefault(c["site"], []).append(ok)
    print(json.dumps({k: sum(v) / len(v) for k, v in out.items()}))
''')


def sandbox_checks(cfg: Config, client: OtariClient, results: list[SiteResult], emit=lambda e: None) -> list[str]:
    """Run JSON contract checks for chosen candidates in Otari's sandbox."""
    findings: list[str] = []
    if not cfg.data["tests"].get("sandbox_checks", True):
        return findings
    recs = {r.site_id: r for r in load_recordings(cfg.root)}
    cases = []
    for r in results:
        if not r.chosen or "json" not in r.site.flags:
            continue
        rec = recs.get(r.site.id)
        schema = request_schema(rec.request) if rec else None
        cand = next(c for c in r.candidates if c.model == r.chosen)
        for s in cand.samples:
            cases.append({"site": r.site.id, "text": s.candidate, "schema": schema})
    if not cases:
        return findings
    emit({"type": "stage", "stage": "sandbox"})
    try:
        res = client.code_execute(SANDBOX_TEMPLATE % json.dumps(cases))
    except BudgetReached:
        raise
    except OtariError as e:
        why = {400: "no code sandbox is configured for this workspace (turn on Tools > Code execution in Otari)",
               402: "code execution has no price set in Otari (Tools > Code execution) or funds ran out",
               403: "code execution is switched off for this workspace"}.get(e.status or 0, str(e))
        findings.append(f"Otari sandbox checks did not run: {why} (request id {e.request_id or 'n/a'}).")
        emit({"type": "sandbox", "ok": None, "detail": why})
        return findings
    if not res["ok"]:
        findings.append(f"Otari sandbox returned an error: {res['stderr'][:200]}")
        emit({"type": "sandbox", "ok": False})
        return findings
    try:
        rates = json.loads(res["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        findings.append("Otari sandbox output could not be read.")
        return findings
    for r in results:
        if r.site.id in rates:
            rate = rates[r.site.id]
            cand = next(c for c in r.candidates if c.model == r.chosen)
            cand.findings.append(f"Sandbox contract check: {round(rate * 100)}% of answers passed "
                                 f"(request id {res['request_id'] or 'n/a'}).")
    emit({"type": "sandbox", "ok": True, "rates": rates})
    return findings
