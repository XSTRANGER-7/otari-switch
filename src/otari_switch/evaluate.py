"""Replay recorded prompts on open-weight candidates through Otari, judge them, decide."""

from __future__ import annotations

import json
import random
import re
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from otari_switch.config import Config
from otari_switch.judge import deterministic, llm_judge
from otari_switch.models import CallSite, CandidateResult, Recording, Sample, SiteResult
from otari_switch.otari_client import (
    BudgetReached, GuardrailBlocked, OtariClient, OtariError, SpendMeter, Unreachable, answer_from,
)
from otari_switch.privacy import mask_recording, prompt_text
from otari_switch.verdict import decide

OPTIONAL_PARAMS = ["reasoning_effort", "parallel_tool_calls", "seed", "logprobs", "top_logprobs",
                   "service_tier", "store", "metadata", "prediction", "modalities", "audio", "user",
                   "frequency_penalty", "presence_penalty", "logit_bias"]

Emit = Callable[[dict], None]


def _noop(_: dict) -> None:
    pass


class Cancelled(Exception):
    pass


def prepare_recordings(cfg: Config, recs: list[Recording], client: OtariClient | None, emit: Emit,
                       findings: list[str]) -> list[Recording]:
    mode = cfg.data["privacy"].get("mode", "mask")
    out, masked_total, skipped = [], 0, 0
    for r in recs:
        req, text, n = mask_recording(r.request, r.response_text)
        if mode == "skip" and n:
            skipped += 1
            continue
        if mode == "mask":
            r.request, r.response_text, r.masked = req, text, n
            masked_total += n
        out.append(r)
    profile = cfg.data["privacy"].get("guardrail_profile") or ""
    guard = None
    if cfg.data["privacy"].get("use_otari_guardrail") and profile and client is not None and cfg.ev("judge_model"):
        # Otari runs the guardrail before any provider call; block mode answers 403 guardrail_violation
        guard = {"profile": profile, "checked": 0, "blocked": 0, "request_ids": []}
        kept = []
        for i, r in enumerate(out):
            try:
                resp = client.chat({"messages": [{"role": "user", "content": prompt_text(r.request)[:4000]}],
                                    "max_tokens": 1, "guardrails": [{"profile": profile, "mode": "block"}]},
                                   cfg.ev("judge_model"), role="guardrail")
                guard["checked"] += 1
                if resp.request_id and len(guard["request_ids"]) < 20:
                    guard["request_ids"].append(resp.request_id)
                kept.append(r)
            except GuardrailBlocked as e:
                guard["checked"] += 1
                guard["blocked"] += 1
                skipped += 1
                if e.request_id and len(guard["request_ids"]) < 20:
                    guard["request_ids"].append(e.request_id)
            except BudgetReached:
                raise
            except OtariError as e:
                findings.append(f"Otari guardrail check could not run ({e}); recordings kept, masking still applied.")
                kept.extend(out[i:])
                break
        out = kept
        findings.append(f"Otari guardrail '{profile}' checked {guard['checked']} recording(s) and blocked "
                        f"{guard['blocked']}.")
    if masked_total:
        findings.append(f"Privacy check masked {masked_total} value(s) (emails, keys, card or phone numbers) "
                        "before replay.")
    if skipped:
        findings.append(f"Privacy check skipped {skipped} recording(s).")
    emit({"type": "privacy", "masked": masked_total, "skipped": skipped, "guardrail": guard})
    return out


def _strip(req: dict, keys: list[str]) -> dict:
    return {k: v for k, v in req.items() if k not in keys}


def _preflight(client: OtariClient, site: CallSite, model: str, rec: Recording, findings: list[str]):
    """Send one request; adapt to unsupported optional params. Returns (strip_keys, answer_response)."""
    try:
        return [], client.send(site.endpoint, rec.request, model)
    except OtariError as e:
        if e.status != 400 or isinstance(e, BudgetReached) or e.code in {"invalid_model", "context_length_exceeded",
                                                                         "wrong_model_type"}:
            raise
        present = [k for k in OPTIONAL_PARAMS if k in rec.request]
        if present:
            try:
                r = client.send(site.endpoint, _strip(rec.request, present), model)
                findings.append(f"{model} rejected optional parameter(s) {', '.join(present)} on {site.id}; "
                                "evaluated without them.")
                return present, r
            except OtariError as e2:
                if e2.status != 400:
                    raise
        if "response_format" in rec.request:
            keys = present + ["response_format"]
            r = client.send(site.endpoint, _strip(rec.request, keys), model)
            findings.append(f"{model} rejected response_format on {site.id}; evaluated without it "
                            "(format checks still apply).")
            return keys, r
        raise


def _judge_one(client: OtariClient, cfg: Config, site: CallSite, rec: Recording, text: str,
               tool: dict | None, rng: random.Random,
               judge_reasoning: bool = False) -> tuple[float | None, str, dict, str | None]:
    checks, forced, reason = deterministic(rec.request, site.flags, rec.response_text,
                                           rec.response_tool_call, text, tool)
    if forced is not None:
        return forced, reason, checks, None
    score, jreason, jrid = llm_judge(client, cfg.ev("judge_model"), rec.request, rec.response_text, text, rng,
                                     reasoning=judge_reasoning)
    if reason:
        jreason = f"{jreason} ({reason})"
    return score, jreason, checks, jrid


def _measure_frontier(client: OtariClient, site: CallSite, recs: list[Recording], model: str | None,
                      findings: list[str], lats: list[float] | None = None) -> float | None:
    if not model:
        return None
    costs = []
    for r in recs[:3]:
        try:
            resp = client.send(site.endpoint, r.request, model, role="frontier")
            if resp.cost_usd is not None:
                costs.append(resp.cost_usd)
            if lats is not None:
                lats.append(resp.latency_ms)
        except BudgetReached:
            raise
        except OtariError as e:
            findings.append(f"Could not price today's model {model} through Otari for {site.id} ({e}); "
                            "savings for this call site are not estimated.")
            return None
    return statistics.mean(costs) * 1000 if costs else None


def resolve_model(name: str | None, provider: str, catalog: set[str]) -> str | None:
    """Map the model an app sends its provider (gpt-4.1) to the id Otari serves it under.

    Otari takes `provider:model` selectors and `vendor/model` catalog ids (docs/models.md).
    """
    if not name:
        return None
    if name in catalog or ":" in name:
        return name
    vendor = {"openai": "openai", "anthropic": "anthropic"}.get(provider, provider)
    if f"{vendor}/{name}" in catalog:
        return f"{vendor}/{name}"
    tails = sorted(c for c in catalog if c.endswith("/" + name))
    if len(tails) == 1:
        return tails[0]
    # a provider's own model name gets its provider prefix; anything else may be an alias or a routing
    # policy name, which Otari accepts as a model name as it is
    if re.match(r"^(gpt-|o[1-9]|chatgpt-|text-|davinci|babbage|claude-)", name):
        return f"{vendor}:{name}"
    return name


def previous_policies(cfg: Config) -> dict[str, str]:
    """{policy name: today's model} from an earlier `apply`, so a switched repo is evaluated against the model
    its policies fall back to, not against the policy itself."""
    p = cfg.work_dir / "policies.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except ValueError:
        return {}
    out = {}
    for pol in data:
        chain = [c.get("model") for c in pol.get("candidates") or [] if c.get("model")]
        if pol.get("name") and chain:
            out[pol["name"]] = chain[-1]
    return out


def _catalog(client: OtariClient) -> dict[str, dict]:
    try:
        return {m["id"]: m for m in client.models() if m.get("id")}
    except OtariError:
        return {}


def evaluate(cfg: Config, sites: list[CallSite], recordings: list[Recording], *, emit: Emit = _noop,
             cancel: threading.Event | None = None, client: OtariClient | None = None,
             measure_frontier: bool = True,
             meter: SpendMeter | None = None) -> tuple[list[SiteResult], SpendMeter, list[str]]:
    cancel = cancel or threading.Event()
    findings: list[str] = []
    meter = meter or SpendMeter(float(cfg.ev("budget_usd")))
    if client is None:
        from otari_switch.otari_client import from_config
        client = from_config(cfg, meter)
    client.meter = meter
    rng = random.Random(7)
    th = {k: cfg.th(k) for k in ("switch_mean", "switch_min_share", "fallback_mean", "fallback_check_rate", "max_latency_ratio", "max_cost_ratio")}

    by_site: dict[str, list[Recording]] = {}
    for r in recordings:
        by_site.setdefault(r.site_id, []).append(r)

    targets = [s for s in sites if s.evaluable and not cfg.excluded(s.id) and by_site.get(s.id)]
    emit({"type": "stage", "stage": "privacy"})
    prepared = prepare_recordings(cfg, [r for s in targets for r in by_site[s.id]], client, emit, findings)
    by_site = {}
    for r in prepared:
        by_site.setdefault(r.site_id, []).append(r)

    results: list[SiteResult] = []
    total_jobs = sum(len(by_site.get(s.id, [])) * len(cfg.candidates_for(s.id)) for s in targets)
    done_jobs = 0
    lock = threading.Lock()
    emit({"type": "stage", "stage": "replay", "total": total_jobs,
          "sites": [s.id for s in targets]})

    catalog = _catalog(client)
    jsp = (catalog.get(cfg.ev("judge_model")) or {}).get("supported_parameters") or []
    judge_reasoning = "reasoning" in jsp or "reasoning_effort" in jsp
    prior = previous_policies(cfg)
    budget_hit = False
    for site in targets:
        recs = by_site.get(site.id, [])
        app_model = site.model or next((r.model for r in recs if r.model), None)
        today = cfg.data["eval"].get("today_models") or {}
        if app_model in today:  # how this deployment names today's model
            frontier_model = today[app_model]
        elif app_model in prior:  # already switched: compare against the model the policy falls back to
            frontier_model = prior[app_model]
        else:
            frontier_model = resolve_model(app_model, site.provider, set(catalog)) if catalog else app_model
        sr = SiteResult(site=site, recordings=len(recs), frontier_model=frontier_model,
                        frontier_cost_per_1k=None, candidates=[])
        sr.app_model = app_model
        results.append(sr)
        if not recs or budget_hit:
            continue
        if measure_frontier:
            try:
                flats: list[float] = []
                sr.frontier_cost_per_1k = _measure_frontier(client, site, recs, frontier_model, findings, flats)
                if flats:
                    sr.frontier_p50_ms = round(sorted(flats)[len(flats) // 2], 1)
            except BudgetReached:
                budget_hit = True
                break
        for model in cfg.candidates_for(site.id):
            if cancel.is_set():
                raise Cancelled()
            cr = CandidateResult(model=model)
            sr.candidates.append(cr)
            info = catalog.get(model) or {}
            supported = info.get("supported_parameters")
            if supported and "response_format" in recs[0].request and "response_format" not in supported:
                cr.findings.append(f"Otari's catalog does not list response_format for {model}; "
                                   "the preflight below checks whether it is refused.")
            emit({"type": "candidate_start", "site": site.id, "model": model, "total": len(recs)})
            try:
                strip_keys, first = _preflight(client, site, model, recs[0], cr.findings)
                cr.rejected_params = list(strip_keys)
            except BudgetReached:
                budget_hit = True
                break
            except Unreachable:
                raise
            except OtariError as e:
                cr.errors = len(recs)
                cr.findings.append(f"Failed on the first request: {e} (request id {e.request_id or 'n/a'}).")
                with lock:
                    done_jobs += len(recs)
                emit({"type": "candidate_done", "site": site.id, "model": model, "skipped": True,
                      "error": str(e), "request_id": e.request_id,
                      "done": done_jobs, "total": total_jobs, "spend": meter.spent})
                continue

            costs, lats = [], []

            def run_one(i: int, rec: Recording, first_resp=first):
                if cancel.is_set():
                    raise Cancelled()
                resp = first_resp if i == 0 else client.send(site.endpoint, _strip(rec.request, strip_keys), model)
                text, tool = answer_from(site.endpoint, resp)
                score, reason, checks, jrid = _judge_one(client, cfg, site, rec, text, tool, random.Random(i),
                                                         judge_reasoning)
                return rec, resp, text, tool, score, reason, checks, jrid

            with ThreadPoolExecutor(max_workers=max(1, int(cfg.ev("concurrency")))) as pool:
                futs = [pool.submit(run_one, i, r) for i, r in enumerate(recs)]
                for fut in as_completed(futs):
                    try:
                        rec, resp, text, tool, score, reason, checks, jrid = fut.result()
                    except BudgetReached:
                        budget_hit = True
                        for f in futs:
                            f.cancel()
                        continue
                    except Cancelled:
                        for f in futs:
                            f.cancel()
                        raise
                    except OtariError as e:
                        cr.errors += 1
                        if len(cr.findings) < 5:
                            cr.findings.append(f"Request failed: {e} (request id {e.request_id or 'n/a'}).")
                        with lock:
                            done_jobs += 1
                        continue
                    if resp.cost_usd is not None:
                        costs.append(resp.cost_usd)
                    lats.append(resp.latency_ms)
                    sample = Sample(
                        prompt_preview=prompt_text(rec.request)[-400:], frontier=rec.response_text[:4000]
                        if not rec.response_tool_call else _tool_str(rec.response_tool_call),
                        candidate=(text[:4000] if not tool else _tool_str(tool)),
                        score=score if score is not None else 0.0, reason=reason, checks=checks,
                        request_id=resp.request_id, judge_request_id=jrid)
                    if score is None:
                        cr.errors += 1
                    cr.samples.append(sample)
                    with lock:
                        done_jobs += 1
                    emit({"type": "sample", "site": site.id, "model": model, "score": sample.score,
                          "done": done_jobs, "total": total_jobs, "spend": round(meter.spent, 5),
                          "request_id": resp.request_id, "fallback": resp.fallback,
                          "served_model": resp.served_model})
            _aggregate(cr, costs, lats)
            emit({"type": "candidate_done", "site": site.id, "model": model, "mean": cr.mean_score,
                  "done": done_jobs, "total": total_jobs, "spend": round(meter.spent, 5)})
            if budget_hit:
                break
        decide(sr, th)
        emit({"type": "verdict", "site": site.id, "verdict": sr.verdict, "chosen": sr.chosen,
              "reason": sr.reason})
        if budget_hit:
            break

    if budget_hit:
        findings.append(f"The evaluation budget (${meter.cap:.2f}) was reached; later call sites were not "
                        "fully evaluated. Raise eval.budget_usd to finish.")
    if meter.unknown_cost_calls:
        findings.append(f"{meter.unknown_cost_calls} response(s) carried no cost; spend for those is estimated.")
    for s in sites:
        if s.evaluable and not by_site.get(s.id) and not cfg.excluded(s.id):
            findings.append(f"No recordings for {s.id}; it was not evaluated.")
    return results, meter, findings


def _tool_str(tool: dict) -> str:
    import json
    return f"{tool.get('name')}({json.dumps(tool.get('arguments'), ensure_ascii=False)})"


def _aggregate(cr: CandidateResult, costs: list[float], lats: list[float]) -> None:
    scored = [s for s in cr.samples if s.score > 0]
    if scored:
        cr.mean_score = round(statistics.mean(s.score for s in scored), 3)
        cr.share_ok = round(sum(1 for s in scored if s.score >= 3) / len(scored), 3)
    # share of answers whose format checks all pass (answers with no checks count as passing)
    if cr.samples:
        cr.check_pass_rate = round(sum(1 for s in cr.samples if all(s.checks.values())) / len(cr.samples), 3)
    cr.cost_per_1k = round(statistics.mean(costs) * 1000, 4) if costs else 0.0
    if lats:
        ls = sorted(lats)
        cr.p50_ms = round(ls[len(ls) // 2], 1)
        cr.p95_ms = round(ls[min(len(ls) - 1, int(len(ls) * 0.95))], 1)
    cr.samples.sort(key=lambda s: s.score)  # worst first: what a reviewer needs to see
