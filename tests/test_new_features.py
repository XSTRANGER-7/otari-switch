"""User stories for the web fetch licence check, per-user budgets (team mode), guardrails, canary rollout and the
status routes, run end to end against the demo gateway (which answers in Otari's shapes)."""

import json
import os

import pytest

from otari_switch import licenses as lic
from otari_switch import pipeline as pl
from otari_switch import policies as pol
from otari_switch.config import load
from otari_switch.demo import DEMO_KEY_ENV, setup_demo
from otari_switch.otari_client import BudgetReached, OtariClient, OtariError


@pytest.fixture()
def demo(tmp_path):
    root, url = setup_demo(tmp_path / "acme")
    yield root, url
    os.environ[DEMO_KEY_ENV] = "demo-key"


# --- licence check through Otari's web fetch -------------------------------------------------------

def test_hf_repo_mapping_and_license_parsing():
    assert lic.hf_repo("openai/gpt-oss-20b") == "openai/gpt-oss-20b"
    assert lic.hf_repo("alibaba/qwen3.6-27b") == "Qwen/qwen3.6-27b"
    assert lic.hf_repo("deepseek/deepseek-v4.1-flash") == "deepseek-ai/deepseek-v4.1-flash"
    assert lic.hf_repo("llamacpp:qwen2.5-1.5b-instruct") == "Qwen/qwen2.5-1.5b-instruct"
    assert lic.hf_repo("llamacpp-llama:llama-3.2-1b-instruct") == "meta-llama/llama-3.2-1b-instruct"
    assert lic.hf_repo("acme/secret-model") is None
    card = "---\nlanguage: en\nlicense: other\nlicense_name: modified-mit\n---\n# Kimi"
    assert lic.parse_license(card) == ("other", "modified-mit")
    assert lic.classify("apache-2.0", None) == "open"
    assert lic.classify("llama3.2", None) == "restricted"
    assert lic.classify("other", "modified-mit") == "open"
    assert lic.classify(None, None) == "unknown"


def test_license_check_reads_model_cards_through_otari_and_caches(demo):
    root, _ = demo
    cfg = load(root)
    client = pl.client_for(cfg)
    events = []
    res = lic.check_many(client, ["demo/qwen3-235b", "demo/llama-3.3-70b", "demo/judge"], cfg.work_dir,
                         emit=events.append)
    r = res["results"]
    assert res["available"] is True
    assert (r["demo/qwen3-235b"]["status"], r["demo/qwen3-235b"]["license"]) == ("open", "apache-2.0")
    assert (r["demo/llama-3.3-70b"]["status"], r["demo/llama-3.3-70b"]["license"]) == ("restricted", "llama3.3")
    assert r["demo/judge"]["status"] == "unknown"  # no known organisation: nothing is claimed
    assert r["demo/qwen3-235b"]["request_id"] and r["demo/qwen3-235b"]["via"] == "otari_web_fetch"
    assert [e["model"] for e in events if e["type"] == "license"] == list(r)
    cached = json.loads((cfg.work_dir / "licenses.json").read_text())
    assert cached["demo/qwen3-235b"]["license"] == "apache-2.0"


def test_license_check_reports_web_fetch_off_once(monkeypatch):
    calls = []

    def off(self, url, max_chars=6000):
        calls.append(url)
        raise OtariError("web_fetch_enabled is off for this workspace", 400, None, "req-x")

    monkeypatch.setattr(OtariClient, "fetch", off)
    res = lic.check_many(OtariClient("http://x/api/v1", "k"), ["openai/gpt-oss-20b", "alibaba/qwen3-32b"])
    assert res["available"] is False and res["reason"] == lic.FETCH_OFF
    assert len(calls) == 1 and all(v["status"] == "error" for v in res["results"].values())


def test_evaluation_runs_the_license_check_and_stores_it(demo):
    root, _ = demo
    run = pl.do_eval(load(root))
    assert run["licenses"]["demo/qwen3-235b"]["status"] == "open"
    assert any("custom terms" in f and "llama" in f for f in run["findings"])
    assert run["spend_by_role"].get("license", 0) > 0


# --- per-user budgets (team mode with a service key) -------------------------------------------------

def test_team_mode_bills_the_teammate_and_lowers_the_cap(demo):
    root, _ = demo
    os.environ[DEMO_KEY_ENV] = "svc-demo-key"  # a service key: requests may name end users
    cfg = load(root)
    cfg.data["otari"]["end_user"] = "alice"
    client = pl.client_for(cfg)
    assert client.end_user == "alice"
    client.chat({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}, "demo/judge", role="judge")
    room = pl.budget_room(cfg, client)
    assert room["label"] == "alice's Otari budget" and room["limit_usd"] == 0.05
    cap, note, _ = pl.budget_cap(cfg, client)
    assert cap < 0.05 and "alice's Otari budget" in note
    events = []
    run = pl.do_eval(cfg, emit=events.append)
    assert run["budget"]["end_user"] == "alice" and run["budget"]["cap"] < 0.05
    assert any(e["type"] == "budget" for e in events)
    assert run["spend_usd"] <= run["budget_usd"] + 0.0005


def test_team_mode_refusal_names_the_teammate(demo):
    root, url = demo
    os.environ[DEMO_KEY_ENV] = "svc-demo-key"
    c = OtariClient(url, "svc-demo-key", end_user="bob")
    with pytest.raises(BudgetReached):
        for _ in range(400):  # spend bob's $0.05 demo budget
            c.chat({"messages": [{"role": "user", "content": "x" * 4000}]}, "openai/gpt-4.1", role="candidate")
    with pytest.raises(BudgetReached) as e:
        c.chat({"messages": [{"role": "user", "content": "again"}]}, "demo/judge")
    assert e.value.body["end_user"] == "bob"
    cfg = load(root)
    cfg.data["otari"]["end_user"] = "bob"
    with pytest.raises(BudgetReached):
        pl.budget_cap(cfg)


def test_a_normal_key_never_sends_a_user(demo):
    root, _ = demo
    cfg = load(root)
    client = pl.client_for(cfg)
    assert client.end_user is None
    client.chat({"messages": [{"role": "user", "content": "hi"}], "user": "customer-7"}, "demo/judge")


# --- guardrails ---------------------------------------------------------------------------------------

def test_guardrail_blocks_unmasked_personal_data_and_counts_it(demo):
    root, _ = demo
    cfg = load(root)
    cfg.data["privacy"].update({"use_otari_guardrail": True, "guardrail_profile": "demo-pii", "mode": "off"})
    events = []
    run = pl.do_eval(cfg, emit=events.append)
    g = next(e for e in events if e["type"] == "privacy")["guardrail"]
    assert g["profile"] == "demo-pii" and g["checked"] > 0 and g["blocked"] > 0 and g["request_ids"]
    assert any("guardrail 'demo-pii'" in f for f in run["findings"])


def test_guardrail_passes_masked_prompts(demo):
    root, _ = demo
    cfg = load(root)
    cfg.data["privacy"].update({"use_otari_guardrail": True, "guardrail_profile": "demo-pii", "mode": "mask"})
    events = []
    pl.do_eval(cfg, emit=events.append)
    g = next(e for e in events if e["type"] == "privacy")["guardrail"]
    assert g["checked"] > 0 and g["blocked"] == 0


# --- canary rollout through Otari's weighted router ------------------------------------------------------

def test_canary_policies_are_created_and_split_traffic(demo):
    root, url = demo
    cfg = load(root)
    run = pl.do_eval(cfg)
    res = pl.apply_policies(cfg, run, canary_pct=30)
    assert res["applied"] and all(a["ok"] for a in res["applied"])
    switched = [a for a in res["applied"] if a["spec"]["select"][0].get("router") == "weighted"]
    assert switched and switched[0]["spec"]["select"][0]["weights"] and switched[0]["spec"]["select"][-1]["default"]
    name = switched[0]["name"]
    c = OtariClient(url, "demo-key")
    served = {c.chat({"messages": [{"role": "user", "content": f"q{i}"}]}, name).served_model for i in range(40)}
    assert len(served) == 2  # both the open model and today's model answer under one policy name
    pv = pl.preview(cfg, run, canary_pct=30)
    assert "Weighted canary" in pv["dashboard_steps"] and all("canary_pct" in p for p in pv["policies"])


def test_keep_frontier_sites_are_never_made_canaries():
    pols = [{"name": "k", "verdict": "keep", "candidates": [{"model": "openai/gpt-4.1"}]},
            {"name": "s", "verdict": "switch", "candidates": [{"model": "a/b"}, {"model": "openai/gpt-4.1"}]}]
    out = pol.with_canary(pols, 50)
    assert out[0]["canary_pct"] == 0 and out[0]["spec"] == {"select": [{"default": "openai/gpt-4.1"}]}
    assert out[1]["canary_pct"] == 50
    assert pol.with_canary(pols, 0)[1]["spec"] == {"select": [{"default": "a/b"}], "on_failure": ["openai/gpt-4.1"]}


# --- status routes the app uses -------------------------------------------------------------------------------

def test_app_routes_for_licenses_mcp_guardrails_and_team_mode(demo):
    from otari_switch.server import App
    root, _ = demo
    app = App(root)
    st, body = app.licenses({}, {"models": ["demo/qwen3-235b"]})
    assert st == 200 and body["results"]["demo/qwen3-235b"]["status"] == "open"
    st, body = app.licenses({}, {})
    assert st == 200 and "demo/qwen3-235b" in body["results"]
    st, body = app.mcp({}, {})
    assert st == 200 and body["servers"] == [] and body["error"] is None
    st, body = app.guardrails({}, {})
    assert st == 200 and body["available"] and body["profiles"][0]["name"] == "demo-pii"
    st, _ = app.save_config({}, {"end_user": "carol", "license_check": True, "guardrail_profile": "demo-pii"})
    cfg = load(root)
    assert cfg.data["otari"]["end_user"] == "carol" and cfg.data["privacy"]["guardrail_profile"] == "demo-pii"
    st, body = app.state({}, {})
    assert body["config"]["end_user"] == "carol"
    st, body = app.preview({"canary_pct": ["25"]}, {})
    assert st in (200, 404)


def test_team_mode_with_a_normal_key_fails_fast(demo):
    from otari_switch.server import App
    root, _ = demo
    cfg = load(root)
    cfg.data["otari"]["end_user"] = "alice"
    cfg.save()
    with pytest.raises(OtariError) as e:
        pl.budget_cap(load(root))
    assert e.value.code == "service_key_required" and "service key" in str(e.value)
    st, body = App(root).start_eval({}, {})
    assert st == 400 and "service key" in body["error"]


def test_run_stores_the_guardrail_result(demo):
    root, _ = demo
    cfg = load(root)
    cfg.data["privacy"].update({"use_otari_guardrail": True, "guardrail_profile": "demo-pii", "mode": "off"})
    run = pl.do_eval(cfg)
    assert run["guardrail"]["profile"] == "demo-pii" and run["guardrail"]["blocked"] > 0


def test_doctor_accepts_provider_selectors_and_real_guardrail_profiles(demo):
    root, _ = demo
    cfg = load(root)
    cfg.data["eval"]["candidates"] = ["demo/qwen3-235b", "demo-provider:qwen3-235b"]
    cfg.data["privacy"].update({"use_otari_guardrail": True, "guardrail_profile": "demo-pii"})
    checks = {c["name"]: c for c in pl.doctor(cfg)}
    assert checks["Candidate demo/qwen3-235b"]["ok"] is True
    assert checks["Candidate demo-provider:qwen3-235b"]["ok"] is None  # valid selector, just not listed
    assert checks["Guardrail profile"]["ok"] is True
    assert checks["Web fetch (licence check)"]["ok"] is True
