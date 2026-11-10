import json

from otari_switch.judge import _parse_judge, deterministic, is_label, parse_json_text
from otari_switch.models import KEEP, SWITCH, SWITCH_WITH_FALLBACK, CallSite, CandidateResult, Sample, SiteResult
from otari_switch.privacy import Masker, mask_recording
from otari_switch.verdict import classify, decide

TH = {"switch_mean": 4.0, "switch_min_share": 0.9, "fallback_mean": 3.5, "fallback_check_rate": 0.85}
SCHEMA_REQ = {"response_format": {"type": "json_schema", "json_schema": {"schema": {
    "type": "object", "required": ["a", "b"], "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}}}}


def test_json_checks():
    checks, score, _ = deterministic(SCHEMA_REQ, ["json", "schema"], '{"a":"x","b":1}', None, '{"a":"x","b":1}', None)
    assert score == 5.0 and checks == {"valid_json": True, "schema": True}
    checks, score, _ = deterministic(SCHEMA_REQ, ["json"], '{"a":"x","b":1}', None, '{"a":"x"', None)
    assert score == 1.0 and checks["valid_json"] is False
    checks, score, reason = deterministic(SCHEMA_REQ, ["json"], '{"a":"x","b":1}', None, '{"a":"x"}', None)
    assert checks["schema"] is False and score == 1.5 and "schema" in reason
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}


def test_label_and_tool_checks():
    _, score, _ = deterministic({}, [], "high", None, "High.", None)
    assert score == 5.0
    _, score, _ = deterministic({}, [], "high", None, "low", None)
    assert score == 1.0
    assert is_label("urgent") and not is_label("a long sentence that goes on and on")
    ft = {"name": "refund", "arguments": {"order_id": "1", "reason": "x"}}
    _, score, _ = deterministic({}, ["tools"], "", ft, "", {"name": "refund", "arguments": {"order_id": "1", "reason": "y"}})
    assert 3.0 < score < 5.0
    _, score, _ = deterministic({}, ["tools"], "", ft, "", {"name": "escalate", "arguments": {}})
    assert score == 1.0


def test_free_text_goes_to_llm_judge():
    _, score, _ = deterministic({}, [], "A long summary of the ticket here.", None, "Another summary entirely.", None)
    assert score is None
    assert _parse_judge('Sure! {"score": 4, "reason": "fine"}') == (4.0, "fine")
    assert _parse_judge("I'd give it a 3")[0] == 3.0
    assert _parse_judge("no idea")[0] is None


def cand(model, mean, share, checks=1.0, cost=1.0, tests=None, n=10, rejected=None):
    c = CandidateResult(model=model, mean_score=mean, share_ok=share, check_pass_rate=checks, cost_per_1k=cost,
                        tests_passed=tests, rejected_params=rejected or [])
    c.samples = [Sample("p", "f", "c", mean, "", {}, None) for _ in range(n)]
    return c


def site(cands, flags=None):
    s = CallSite(id="a.py:f:0", file="a.py", line=1, col=0, end_line=1, end_col=1, function="f", provider="openai",
                 endpoint="chat", model="gpt-4.1", model_source="literal", flags=flags or [])
    return SiteResult(site=s, recordings=10, frontier_model="gpt-4.1", frontier_cost_per_1k=4.0, candidates=cands)


def test_verdict_edges_and_cheapest_pick():
    assert classify(cand("m", 4.0, 0.9), 10, TH) == SWITCH
    assert classify(cand("m", 3.99, 0.95), 10, TH) == SWITCH_WITH_FALLBACK
    assert classify(cand("m", 4.5, 0.89), 10, TH) == SWITCH_WITH_FALLBACK
    assert classify(cand("m", 4.5, 1.0, checks=0.8), 10, TH) == KEEP
    assert classify(cand("m", 5.0, 1.0, tests=False), 10, TH) == KEEP
    assert classify(cand("m", 5.0, 1.0, rejected=["response_format"]), 10, TH) == KEEP
    r = decide(site([cand("big", 4.8, 1.0, cost=2.0), cand("small", 4.2, 0.95, cost=0.5),
                     cand("weak", 3.6, 0.8, cost=0.1)]), TH)
    assert r.verdict == SWITCH and r.chosen == "small" and "cheaper" in r.reason
    r = decide(site([cand("x", 2.0, 0.2)]), TH)
    assert r.verdict == KEEP and r.chosen is None


def test_privacy_masking_is_consistent():
    req = {"model": "gpt", "messages": [{"role": "user", "content": "Mail a@b.com or a@b.com, key sk-abcdefghijklmnopqrstuv, "
                                                                    "card 4242 4242 4242 4242, call +44 7700 900123"}]}
    masked, text, n = mask_recording(req, "Replied to a@b.com")
    content = masked["messages"][0]["content"]
    assert "a@b.com" not in content and content.count("<EMAIL_1>") == 2
    assert "<KEY_1>" in content and "<CARD_1>" in content and "<PHONE_1>" in content
    assert text == "Replied to <EMAIL_1>" and n == 4
    assert masked["model"] == "gpt"
    assert Masker().mask_text("order 1234567890123") == "order 1234567890123"  # not a valid card number


def test_report_markdown_roundtrip(tmp_path):
    from otari_switch import report
    from otari_switch.models import Run, to_dict
    r = decide(site([cand("small", 4.2, 0.95, cost=0.5)], flags=["json"]), TH)
    run = to_dict(Run(id="x", repo=str(tmp_path), commit=None, started="2026-11-10T00:00:00", finished=None,
                      region="global", budget_usd=2, spend_usd=0.1, sites=[r]))
    run["summary"] = report.summarize(run, 1000)
    assert run["summary"]["moved"] == 1 and run["summary"]["monthly_savings_usd"] == 3.5
    md = report.markdown(run)
    assert "1 of 1" in md and "small" in md
    html = report.html_page(run)
    assert "<script" not in html and "1 of 1 calls can switch" in html
    json.dumps(run)


def test_latency_guard_keeps_a_much_slower_candidate_out():
    from otari_switch.models import CandidateResult, Sample
    from otari_switch.verdict import SWITCH, classify, too_slow
    th = {"switch_mean": 4.0, "switch_min_share": 0.9, "fallback_mean": 3.5, "fallback_check_rate": 0.85,
          "max_latency_ratio": 4.0}
    s = Sample("p", "f", "c", 5.0, "ok", {}, "r")
    fast = CandidateResult(model="fast", mean_score=5, share_ok=1, check_pass_rate=1, p50_ms=1800, samples=[s] * 4)
    slow = CandidateResult(model="slow", mean_score=5, share_ok=1, check_pass_rate=1, p50_ms=21000, samples=[s] * 4)
    assert classify(fast, 4, th, today_p50=1500) == SWITCH
    assert too_slow(slow, 1500, th) and classify(slow, 4, th, today_p50=1500) != SWITCH
    assert classify(slow, 4, {**th, "max_latency_ratio": 0}, today_p50=1500) == SWITCH


def test_cost_guard_and_truncated_judge_reply():
    from otari_switch.judge import _parse_judge
    from otari_switch.models import CandidateResult, Sample
    from otari_switch.verdict import KEEP, SWITCH, classify
    th = {"switch_mean": 4.0, "switch_min_share": 0.9, "fallback_mean": 3.5, "fallback_check_rate": 0.85,
          "max_latency_ratio": 0, "max_cost_ratio": 1.0}
    s = Sample("p", "f", "c", 5.0, "ok", {}, "r")
    pricey = CandidateResult(model="pricey", mean_score=5, share_ok=1, check_pass_rate=1, cost_per_1k=3.1, samples=[s] * 4)
    assert classify(pricey, 4, th, today_cost_1k=1.6) == KEEP
    assert classify(pricey, 4, {**th, "max_cost_ratio": 2.5}, today_cost_1k=1.6) == SWITCH
    assert _parse_judge('{"score": 4, "reason": "Covers every point but the sign-off is miss')[0] == 4.0
