"""Build one Otari routing policy per call site, then create it (API) or explain how (dashboard / config.yml).

Otari's policy shape (docs/routing.md, PolicyRequest in docs/public/openapi.json):

    POST /api/v1/routing/policies
    {"name": "switch-summarize", "spec": {"select": [{"default": "<model>"}], "on_failure": ["<backup>", ...]}}

- `select` picks where a request starts; `on_failure` lists what to try when a candidate fails before answering.
- At most five candidates per plan. Names cannot contain `:` or `/`.
- Policy management needs operator authority (the master key) on a self-hosted gateway. On otari.ai, owners and
  admins create policies in the dashboard (Routing page), so Otari Switch writes exact steps instead.
"""

from __future__ import annotations

import json
import os
import re

from otari_switch.config import Config
from otari_switch.models import KEEP, SWITCH_WITH_FALLBACK, SiteResult
from otari_switch.otari_client import OtariClient, OtariError
from otari_switch.verdict import classify

MAX_CANDIDATES = 5


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:32] or "call"


def policy_names(results: list[SiteResult]) -> dict[str, str]:
    names, used = {}, set()
    for r in results:
        base = slug(r.site.function.split(".")[-1] if r.site.function != "<module>" else r.site.file.split("/")[-1][:-3])
        name, n = f"switch-{base}", 2
        while name in used:
            name, n = f"switch-{base}-{n}", n + 1
        used.add(name)
        names[r.site.id] = name
    return names


def env_var(policy_name: str) -> str:
    return "OTARI_POLICY_" + re.sub(r"[^A-Z0-9]+", "_", policy_name.upper().removeprefix("SWITCH-")).strip("_")


def spec_for(chain: list[str]) -> dict:
    """Otari policy spec: first model serves, the rest take over in order if it fails before answering."""
    chain = chain[:MAX_CANDIDATES]
    spec: dict = {"select": [{"default": chain[0]}]}
    if len(chain) > 1:
        spec["on_failure"] = chain[1:]
    return spec


def build(cfg: Config, results: list[SiteResult]) -> list[dict]:
    th = {k: cfg.th(k) for k in ("switch_mean", "switch_min_share", "fallback_mean", "fallback_check_rate", "max_latency_ratio", "max_cost_ratio")}
    names = policy_names(results)
    out = []
    for r in results:
        chain = []
        if r.chosen:
            chain.append(r.chosen)
            # outage backup: the next best open model that also earned a passing verdict
            backups = sorted((c for c in r.candidates if c.model != r.chosen and classify(c, r.recordings, th, r.frontier_p50_ms, r.frontier_cost_per_1k) != KEEP),
                             key=lambda c: -c.mean_score)
            if backups:
                chain.append(backups[0].model)
        if r.frontier_model and r.frontier_model not in chain:
            chain.append(r.frontier_model)
        if not chain:
            continue
        name = names[r.site.id]
        policy = {
            "name": name,
            "description": f"Otari Switch: {r.site.id} ({r.verdict.replace('_', ' ')})",
            "strategy": "failover",
            "candidates": [{"model": m} for m in chain[:MAX_CANDIDATES]],
            "spec": spec_for(chain),
            "site_id": r.site.id,
            "env_var": env_var(name),
            "verdict": r.verdict,
            # the runtime guard re-checks JSON answers; free-text sites rely on the policy's outage fallback
            "runtime_guard": r.verdict == SWITCH_WITH_FALLBACK and "json" in r.site.flags,
        }
        r.policy = policy
        out.append(policy)
    return out


def _spec(p: dict) -> dict:
    return p.get("spec") or spec_for([c["model"] for c in p["candidates"]])


def master_key(cfg: Config) -> str | None:
    env = cfg.data["otari"].get("master_key_env") or "OTARI_MASTER_KEY"
    return os.environ.get(env) or None


def apply(cfg: Config, client: OtariClient, policies: list[dict]) -> tuple[list[dict], str]:
    """Create policies through Otari's API when enabled (operator key); otherwise return dashboard steps.

    Each draft is checked with POST /routing/policies/explain first, so a target the deployment cannot reach is
    reported before anything is saved.
    """
    path = cfg.work_dir / "policies.json"
    path.write_text(json.dumps(policies, indent=2), encoding="utf-8")
    (cfg.work_dir / "otari-routing.yml").write_text(config_yaml(policies), encoding="utf-8")
    applied: list[dict] = []
    if cfg.data["otari"].get("policies_api") and policies:
        key = master_key(cfg)
        if not key:
            applied.append({"name": "*", "ok": False,
                            "error": f"Set ${cfg.data['otari'].get('master_key_env') or 'OTARI_MASTER_KEY'} "
                                     "(operator key) to create policies through the API."})
            return applied, dashboard_steps(policies)
        admin = OtariClient(cfg.base_url, key, cfg.data["otari"]["timeout_s"])
        ws = cfg.data["otari"].get("workspace_id") or None
        for p in policies:
            spec = _spec(p)
            try:
                plan = admin.explain_policy(p["name"], spec, ws)
                left_out = plan.get("dropped") or plan.get("filtered") or []
                admin.create_policy(p["name"], spec, ws)
                applied.append({"name": p["name"], "ok": True, "spec": spec,
                                "note": f"{len(left_out)} candidate(s) filtered by the gateway" if left_out else ""})
            except OtariError as e:
                applied.append({"name": p["name"], "ok": False, "error": str(e), "request_id": e.request_id})
        if all(a["ok"] for a in applied):
            return applied, ""
    return applied, dashboard_steps(policies)


def config_yaml(policies: list[dict]) -> str:
    """The same policies as a `routing:` block for a self-hosted gateway's config.yml."""
    lines = ["# Paste under the top level of your self-hosted Otari config.yml (docs/routing.md).",
             "# Policy targets must be provider selectors your gateway serves, e.g. openai:gpt-4.1.",
             "routing:", "  policies:"]
    for p in policies:
        spec = _spec(p)
        lines.append(f"    {p['name']}:")
        lines.append("      select:")
        lines.append(f"        - default: {json.dumps(spec['select'][0]['default'])}")
        if spec.get("on_failure"):
            lines.append("      on_failure:")
            for m in spec["on_failure"]:
                lines.append(f"        - {json.dumps(m)}")
    return "\n".join(lines) + "\n"


def dashboard_steps(policies: list[dict]) -> str:
    lines = ["# Create these routing policies in Otari", "",
             "On otari.ai: open the dashboard, go to **Routing**, choose your workspace and add one policy per "
             "block below. The first model serves traffic; the next ones take over, in order, only when the one "
             "above fails before answering (Otari's `on_failure`).",
             "",
             "Self-hosted: paste `.otari-switch/otari-routing.yml` into your config.yml, or set "
             "`otari.policies_api: true` and `OTARI_MASTER_KEY` and run `otari-switch apply` again.", ""]
    for p in policies:
        spec = _spec(p)
        lines.append(f"## {p['name']}")
        lines.append(f"- Description: {p['description']}")
        lines.append(f"- 1. `{spec['select'][0]['default']}` (serves traffic)")
        for i, m in enumerate(spec.get("on_failure") or [], 2):
            lines.append(f"- {i}. `{m}` (on failure)")
        lines.append(f"- Your code reads the policy name from `{p['env_var']}`.")
        lines.append("- API body (operator key): `" + json.dumps({"name": p["name"], "spec": spec}) + "`")
        lines.append("")
    lines.append("Rollback without a code change: move today's model to the top of a policy.")
    return "\n".join(lines)
