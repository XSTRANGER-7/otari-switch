import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otari_switch.otari_client import (BudgetReached, GuardrailBlocked, OtariClient, OtariError, SpendMeter,
                                       Unreachable, extract_answer)


def serve(responses):
    """responses: list of (status, body, headers) returned in order; the last one repeats."""
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            calls.append(json.loads(self.rfile.read(n) or b"{}"))
            status, body, hdrs = responses[min(len(calls) - 1, len(responses) - 1)]
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/api/v1", calls, srv


OK = (200, {"choices": [{"message": {"content": "hi"}}], "usage": {"cost_usd": 0.01}},
      {"Otari-Request-ID": "r1", "Otari-Model": "m", "Otari-Fallback": "true", "Otari-Attempt-Count": "2"})


def test_headers_cost_and_retry(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client._backoff", lambda *a: 0)
    url, calls, srv = serve([(503, {"error": {"message": "busy"}}, {}), OK])
    c = OtariClient(url, "k", meter=SpendMeter(1.0))
    r = c.chat({"messages": [], "stream": True}, "model-x")
    assert r.request_id == "r1" and r.fallback and r.attempts == 2 and r.cost_usd == 0.01
    assert calls[-1]["model"] == "model-x" and "stream" not in calls[-1]
    assert c.meter.spent == pytest.approx(0.01)
    srv.shutdown()


def test_budget_and_guardrail_and_errors(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client._backoff", lambda *a: 0)
    url, _, srv = serve([(403, {"error": {"code": "budget_exceeded", "message": "over"}}, {})])
    with pytest.raises(BudgetReached):
        OtariClient(url, "k").chat({"messages": []}, "m")
    srv.shutdown()
    url, _, srv = serve([(403, {"code": "guardrail_violation", "message": "blocked"}, {})])
    with pytest.raises(GuardrailBlocked):
        OtariClient(url, "k").chat({"messages": []}, "m")
    srv.shutdown()
    url, _, srv = serve([(400, {"detail": "bad param"}, {"Otari-Request-ID": "r9"})])
    with pytest.raises(OtariError) as e:
        OtariClient(url, "k").chat({"messages": []}, "m")
    assert e.value.status == 400 and e.value.request_id == "r9" and "bad param" in str(e.value)
    srv.shutdown()


def test_meter_stops_before_cap():
    m = SpendMeter(0.05, estimate_per_call=0.02)
    m.add(0.02)
    m.add(0.02)
    with pytest.raises(BudgetReached):
        m.check()


def test_unreachable(monkeypatch):
    monkeypatch.setattr("otari_switch.otari_client._backoff", lambda *a: 0)
    with pytest.raises(Unreachable):
        OtariClient("http://127.0.0.1:9/api/v1", "k", timeout_s=1, max_retries=1).models()


def test_extract_answer_shapes():
    assert extract_answer("chat", {"choices": [{"message": {"content": None, "tool_calls": [
        {"function": {"name": "f", "arguments": '{"a": 1}'}}]}}]}) == ("", {"name": "f", "arguments": {"a": 1}})
    assert extract_answer("messages", {"type": "message", "content": [{"type": "text", "text": "x"},
                                                                       {"type": "tool_use", "name": "g", "input": {}}]}) == ("x", {"name": "g", "arguments": {}})
    assert extract_answer("responses", {"output": [{"type": "message", "content": [{"type": "output_text", "text": "y"}]}]}) == ("y", None)
