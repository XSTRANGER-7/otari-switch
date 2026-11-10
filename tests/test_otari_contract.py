"""The Otari API contract Otari Switch relies on (docs/api-reference.md, docs/routing.md, docs/mcp.md and
docs/public/openapi.json in mozilla-ai/otari). Each test pins one documented behaviour."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otari_switch import policies as pol
from otari_switch.otari_client import (BudgetReached, McpOutcomeUnknown, OtariClient, OtariError, SpendMeter,
                                       tags_header)


def serve(responses):
    """responses: list of (status, body, headers); the last one repeats. Records (method, path, headers, body)."""
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _handle(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else None
            calls.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            status, out, hdrs = responses[min(len(calls) - 1, len(responses) - 1)]
            raw = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = _handle

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/api/v1", calls, srv


CHAT_OK = (200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": "0.000125",
                           "pricing_source": "defaults"}},
           {"Otari-Request-ID": "req-1", "Otari-Provider": "groq", "Otari-Model": "llama-3.3-70b-versatile",
            "Otari-Attempt-Count": "1", "Otari-Fallback": "false", "Otari-Response-Duration-Ms": "812"})


def test_cost_is_a_decimal_string_and_meter_counts_it():
    url, _, srv = serve([CHAT_OK])
    c = OtariClient(url, "otk_test", meter=SpendMeter(1.0))
    r = c.chat({"messages": [{"role": "user", "content": "hi"}]}, "meta-llama/llama-3.3-70b-instruct")
    assert r.cost_usd == pytest.approx(0.000125)
    assert r.pricing_source == "defaults" and r.gateway_ms == 812.0
    assert r.provider == "groq" and r.served_model == "llama-3.3-70b-versatile" and not r.fallback
    assert r.tokens == (10, 2)
    assert c.meter.spent == pytest.approx(0.000125) and c.meter.unknown_cost_calls == 0
    srv.shutdown()


def test_user_field_is_never_sent_and_tags_go_in_the_header():
    url, calls, srv = serve([CHAT_OK])
    c = OtariClient(url, "otk_test", user="asha")
    c.chat({"messages": [], "user": "customer-42", "stream": True}, "m", role="judge")
    method, path, headers, body = calls[0]
    assert path == "/api/v1/chat/completions" and "user" not in body and "stream" not in body
    tags = dict(p.split("=", 1) for p in headers["otari-tags"].split(","))
    assert tags == {"tool": "otari-switch", "teammate": "asha", "role": "judge"}
    assert headers["authorization"] == "Bearer otk_test" and headers.get("idempotency-key")
    srv.shutdown()


def test_tags_header_percent_encodes():
    assert tags_header({"site": "app/x.py:f,0", "empty": ""}) == "site=app/x.py:f%2C0"


def test_retries_reuse_one_idempotency_key(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client._backoff", lambda *a: 0)
    url, calls, srv = serve([(502, {"detail": "The provider failed the request with HTTP 500"}, {}), CHAT_OK])
    OtariClient(url, "k").chat({"messages": []}, "m")
    keys = {h["idempotency-key"] for _, _, h, _ in calls}
    assert len(calls) == 2 and len(keys) == 1
    srv.shutdown()


def test_should_retry_false_stops_retries(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client._backoff", lambda *a: 0)
    url, calls, srv = serve([(502, {"detail": "out of credit"}, {"x-should-retry": "false"}), CHAT_OK])
    with pytest.raises(OtariError):
        OtariClient(url, "k").chat({"messages": []}, "m")
    assert len(calls) == 1
    srv.shutdown()


def test_error_code_header_and_detail_body():
    url, _, srv = serve([(400, {"detail": "response_format is not supported", "code": "unsupported_parameter"},
                          {"Otari-Error-Code": "unsupported_parameter", "Otari-Request-ID": "req-9"})])
    with pytest.raises(OtariError) as e:
        OtariClient(url, "k").chat({"messages": []}, "m")
    assert e.value.code == "unsupported_parameter" and e.value.unsupported_param
    assert e.value.request_id == "req-9" and "not supported" in str(e.value)
    srv.shutdown()


def test_budget_exceeded_is_403_with_scope():
    url, calls, srv = serve([(403, {"detail": "has exceeded budget limit", "code": "budget_exceeded"},
                              {"Otari-Error-Code": "budget_exceeded", "Otari-Budget-Scope": "workspace",
                               "Retry-After": "7200"})])
    with pytest.raises(BudgetReached) as e:
        OtariClient(url, "k").chat({"messages": []}, "m")
    assert "workspace" in str(e.value) and "2.0 h" in str(e.value)
    assert len(calls) == 1  # never retried
    srv.shutdown()


def test_anthropic_error_envelope_on_messages():
    url, _, srv = serve([(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"},
                                "code": "rate_limited"}, {"Retry-After": "0"})])
    c = OtariClient(url, "k", max_retries=0)
    with pytest.raises(OtariError) as e:
        c.messages({"messages": []}, "anthropic/claude-haiku-4-5")
    assert e.value.code == "rate_limited" and "slow down" in str(e.value)
    srv.shutdown()


def test_validation_detail_list_is_readable():
    url, _, srv = serve([(422, {"detail": [{"loc": ["body", "messages"], "msg": "Field required",
                                            "type": "missing"}], "code": "invalid_request"}, {})])
    with pytest.raises(OtariError) as e:
        OtariClient(url, "k").chat({}, "m")
    assert "messages: Field required" in str(e.value)
    srv.shutdown()


def test_code_execute_uses_documented_fields():
    url, calls, srv = serve([(200, {"object": "code_execution.result", "stdout": "2\n", "stderr": "",
                                    "return_code": 0, "files": []}, {"Otari-Request-ID": "req-c"})])
    res = OtariClient(url, "k").code_execute("print(1+1)", timeout_s=30)
    _, path, _, body = calls[0]
    assert path == "/api/v1/code/execute" and body == {"code": "print(1+1)", "timeout_seconds": 30}
    assert res["ok"] and res["stdout"] == "2\n" and res["request_id"] == "req-c"
    srv.shutdown()


def test_code_execute_failure_is_still_200():
    url, _, srv = serve([(200, {"object": "code_execution.result", "stdout": "", "stderr": "Traceback ...",
                                "return_code": 1, "files": []}, {})])
    assert OtariClient(url, "k").code_execute("raise SystemExit(1)")["ok"] is False
    srv.shutdown()


def test_policy_spec_matches_otari_shape():
    assert pol.spec_for(["a", "b", "openai:gpt-4.1"]) == {"select": [{"default": "a"}], "on_failure": ["b", "openai:gpt-4.1"]}
    assert pol.spec_for(["only"]) == {"select": [{"default": "only"}]}
    assert len(pol.spec_for(list("abcdefg"))["on_failure"]) == 4  # at most five candidates per plan
    url, calls, srv = serve([(200, {"name": "switch-x", "spec": {}, "source": "api"}, {})])
    OtariClient(url, "master").create_policy("switch-x", pol.spec_for(["a", "b"]))
    method, path, _, body = calls[0]
    assert (method, path) == ("POST", "/api/v1/routing/policies")
    assert body == {"name": "switch-x", "spec": {"select": [{"default": "a"}], "on_failure": ["b"]}}
    srv.shutdown()


def test_config_yaml_is_a_routing_block():
    import yaml
    pols = [{"name": "switch-x", "verdict": "switch", "candidates": [{"model": "a/b"}, {"model": "openai:gpt-4.1"}]}]
    data = yaml.safe_load(pol.config_yaml(pols))
    assert data["routing"]["policies"]["switch-x"] == {"select": [{"default": "a/b"}], "on_failure": ["openai:gpt-4.1"]}
    canary = yaml.safe_load(pol.config_yaml(pol.with_canary(pols, 20)))["routing"]["policies"]["switch-x"]
    assert canary["select"][0] == {"router": "weighted", "candidates": ["a/b", "openai:gpt-4.1"],
                                   "weights": {"a/b": 20, "openai:gpt-4.1": 80}}
    assert canary["select"][-1] == {"default": "openai:gpt-4.1"}


def test_mcp_execute_single_attempt_and_outcome_unknown(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client._backoff", lambda *a: 0)
    url, calls, srv = serve([(502, {"detail": "MCP execution outcome is unknown", "code": "mcp_outcome_unknown",
                                    "execution_state": "outcome_unknown", "request_id": "req_m"}, {})])
    with pytest.raises(McpOutcomeUnknown):
        OtariClient(url, "k").mcp_execute("2c948a61-dc96-4cd8-96bb-8e1434bf424e", "create_pull_request",
                                          {"title": "t"}, "rev1")
    assert len(calls) == 1
    body = calls[0][3]
    assert set(body) == {"mcp_server_id", "tool_name", "arguments", "server_revision", "client_execution_id"}
    srv.shutdown()


def test_key_budget_and_usage_summary_paths():
    url, calls, srv = serve([(200, {"budgets_enforced": True, "remaining_usd": 0.19, "limits": []}, {}),
                             (200, {"totals": {"cost": 0.1}, "series": [], "by_tag": []}, {})])
    c = OtariClient(url, "k")
    assert c.key_budget()["remaining_usd"] == 0.19
    c.key_usage_summary("2026-11-01T00:00:00Z", None, bucket="day", group_by_tag="role", dimensions=["model"])
    assert calls[0][1] == "/api/v1/key/budget"
    assert calls[1][1].startswith("/api/v1/key/usage/summary?bucket=day&start_date=2026-11-01T00%3A00%3A00Z")
    assert "group_by_tag=role" in calls[1][1] and "dimensions=model" in calls[1][1]
    srv.shutdown()


def test_wrong_region_421_explains():
    url, _, srv = serve([(421, {"detail": "This key belongs to eu.api.otari.ai"}, {})])
    with pytest.raises(OtariError) as e:
        OtariClient(url, "k").models()
    assert "another Otari region" in str(e.value)
    srv.shutdown()


def test_pricing_required_402_is_not_a_budget_stop():
    url, _, srv = serve([(402, {"detail": "Model has no price", "code": "pricing_required"},
                          {"Otari-Error-Code": "pricing_required"})])
    with pytest.raises(OtariError) as e:
        OtariClient(url, "k").chat({"messages": []}, "llamacpp:does-not-exist")
    assert not isinstance(e.value, BudgetReached) and e.value.code == "pricing_required"
    srv.shutdown()


def test_409_in_progress_waits_for_the_first_attempt(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client.time.sleep", lambda s: None)
    busy = (409, {"detail": "A request with this Idempotency-Key is still in progress."}, {"Retry-After": "1"})
    url, calls, srv = serve([busy, busy, busy, busy, CHAT_OK])
    r = OtariClient(url, "k", max_retries=1).chat({"messages": []}, "slow-model")
    assert r.request_id == "req-1" and len(calls) == 5
    assert len({h["idempotency-key"] for _, _, h, _ in calls}) == 1
    srv.shutdown()


def test_reasoning_judge_retries_when_thinking_used_the_budget():
    import random
    from otari_switch.judge import llm_judge
    empty = (200, {"choices": [{"message": {"content": "", "reasoning_content": "thinking..."},
                                "finish_reason": "length"}], "usage": {"cost_usd": "0.000100"}}, {})
    good = (200, {"choices": [{"message": {"content": '<think>ok</think>{"score": 4, "reason": "Close."}'},
                               "finish_reason": "stop"}], "usage": {"cost_usd": "0.000200"}},
            {"Otari-Request-ID": "judge-2"})
    url, calls, srv = serve([empty, good])
    score, reason, rid = llm_judge(OtariClient(url, "k"), "alibaba/qwen3.6-27b", {"messages": [
        {"role": "user", "content": "task"}]}, "ref", "cand", random.Random(1), reasoning=True)
    assert (score, reason, rid) == (4.0, "Close.", "judge-2")
    assert calls[0][3]["reasoning_effort"] == "low" and calls[0][3]["max_tokens"] == 400
    assert calls[1][3]["max_tokens"] == 2000
    srv.shutdown()


def test_judge_never_reads_a_digit_out_of_prose():
    from otari_switch.judge import _parse_judge
    assert _parse_judge("The candidate mentions order AC-1 and 3 items but misses the refund request entirely.")[0] is None
    assert _parse_judge("4")[0] == 4.0


def test_409_that_never_finishes_says_so(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client.time.sleep", lambda s: None)
    busy = (409, {"detail": "A request with this Idempotency-Key is still in progress."}, {})
    url, calls, srv = serve([busy])
    with pytest.raises(OtariError) as e:
        OtariClient(url, "k").chat({"messages": []}, "slow-model")
    assert "No answer in time" in str(e.value) and len(calls) == 13
    srv.shutdown()


def test_guardrail_block_with_code_nested_in_detail():
    from otari_switch.otari_client import GuardrailBlocked
    url, _, srv = serve([(403, {"detail": {"message": "Request blocked by guardrail policy.",
                                           "code": "guardrail_violation",
                                           "guardrails": [{"profile": "pii-local", "explanation": "email address",
                                                           "score": 1.0}]}}, {"Otari-Request-ID": "req-g"})])
    with pytest.raises(GuardrailBlocked) as e:
        OtariClient(url, "k").chat({"messages": [], "guardrails": [{"profile": "pii-local", "mode": "block"}]}, "m")
    assert "email address" in str(e.value) and e.value.request_id == "req-g"
    srv.shutdown()


def test_end_user_budget_combines_spend_and_limit():
    from otari_switch.pipeline import end_user_budget
    info = {"external_id": "alice", "budget_id": "teammate-budget", "spend": 0.004, "reserved": 0.001,
            "next_budget_reset_at": "2026-11-12T00:00:00+00:00"}
    got = end_user_budget(info, [{"budget_id": "teammate-budget", "name": "Teammates", "max_budget": 0.01}])
    assert got["limit_usd"] == 0.01 and got["remaining_usd"] == 0.005 and got["resets_at"].startswith("2026-11-12")


def test_team_mode_with_a_normal_key_explains_the_fix():
    url, _, srv = serve([(403, {"detail": "'user' field does not match the authenticated API key's user"}, {})])
    with pytest.raises(OtariError) as e:
        OtariClient(url, "tk-normal", end_user="alice").chat({"messages": []}, "m")
    assert "service key" in str(e.value)
    srv.shutdown()


def test_gated_model_card_is_unverified_not_an_error(monkeypatch):
    from otari_switch import licenses as lic

    def gated(self, url, max_chars=6000):
        raise OtariError("The request could not be completed by the provider", 502, None, "req-f", None,
                         {"otari-error-status": "401", "otari-error-tag": "http_status"})

    monkeypatch.setattr(OtariClient, "fetch", gated)
    r = lic.check(OtariClient("http://x/api/v1", "k"), "meta/llama-3.2-1b-instruct")
    assert r["status"] == "unknown" and "gated" in r["detail"]
