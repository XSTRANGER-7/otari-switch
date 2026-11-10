"""A demo Otari gateway for `otari-switch demo` and the offline test suite. No key, no network, no cost.

Everything it says is labelled demo. It answers in the same shapes real Otari uses (docs/api-reference.md and
docs/public/openapi.json in mozilla-ai/otari), so the code paths it exercises are the real ones:
GET /models (catalog ids, pricing, supported_parameters), POST /chat/completions, /messages, /responses
(usage.cost_usd as a six-decimal string, Otari-* serving headers, Otari-Tags read for attribution),
errors as {"detail", "code"} plus Otari-Error-Code (Anthropic's envelope on /messages), POST /code/execute
(code_execution.result), /routing/policies (+ /explain), GET /key/budget, GET /key/usage/summary,
GET /usage/requests/{id}, GET /mcp/servers. Candidate models answer from the recorded answers with
model-specific, deterministic degradation, so a demo run produces repeatable verdicts.
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import random
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from otari_switch.privacy import Masker, prompt_text

MODELS = {
    # catalog id: (input $/1M tokens, output $/1M tokens, quality profile, provider, supported params or None=all)
    "openai/gpt-4.1": (2.0, 8.0, "frontier", "openai", None),
    "openai/gpt-4.1-mini": (0.4, 1.6, "frontier", "openai", None),
    "demo/qwen3-235b": (0.22, 0.88, "strong", "demo-provider", None),
    "demo/llama-3.3-70b": (0.13, 0.4, "medium", "demo-provider", None),
    "demo/gpt-oss-20b": (0.03, 0.15, "weak", "demo-provider",
                         ["max_tokens", "temperature", "top_p", "stop", "tools", "tool_choice"]),
    "demo/judge": (0.05, 0.25, "judge", "demo-provider", None),
}
ALL_PARAMS = ["max_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "response_format", "seed",
              "reasoning_effort", "frequency_penalty", "presence_penalty"]
DEMO_BUDGET_USD = 5.0


def resolve(model: str) -> str | None:
    """Accept a catalog id (vendor/model) or a provider:model selector, as Otari does."""
    if model in MODELS:
        return model
    if ":" in model:
        prov, name = model.split(":", 1)
        for mid, spec in MODELS.items():
            if spec[3] == prov and (mid.split("/", 1)[-1] == name or mid == name):
                return mid
    return None


SYNONYMS = {
    "customer": "user", "order": "purchase", "refund": "reimbursement", "wants": "would like",
    "because": "since", "received": "got", "damaged": "broken", "quickly": "soon", "help": "assist",
    "issue": "problem", "replacement": "new unit", "account": "profile", "charged": "billed",
}


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def paraphrase(text: str, strength: float, seed: int) -> str:
    """Swap synonyms with probability `strength`; above 0.5 also drop the tail of the answer."""
    rng = random.Random(seed)
    words = text.split(" ")
    out = []
    for w in words:
        core = w.strip(".,;:!?\n").lower()
        if core in SYNONYMS and rng.random() < strength:
            rep = SYNONYMS[core]
            w = w.replace(w.strip(".,;:!?\n"), rep if w[:1].islower() else rep.capitalize())
        out.append(w)
    if strength >= 0.5:
        keep = max(4, int(len(out) * max(0.45, 1.15 - strength)))
        out = out[:keep]
    return " ".join(out)


class Knowledge:
    def __init__(self, recordings: list[dict]):
        self.by_prompt: dict[str, dict] = {}
        for r in recordings:
            self.by_prompt[self.key(r["request"])] = r
            # also index the masked form, since Otari Switch masks prompts before replay
            m = Masker()
            self.by_prompt[self.key(m.mask(r["request"]))] = r

    @staticmethod
    def key(request: dict) -> str:
        return hashlib.sha256(prompt_text(request).encode()).hexdigest()

    def find(self, request: dict) -> dict | None:
        hit = self.by_prompt.get(self.key(request))
        if hit:
            return hit
        # fuzzy: same system prompt, closest user message (live tests chain one answer into the next prompt)
        text = prompt_text(request)
        head = text[:120]
        best, best_ratio = None, 0.0
        for r in self.by_prompt.values():
            other = prompt_text(r["request"])
            if other[:120] != head:
                continue
            ratio = difflib.SequenceMatcher(None, text, other).ratio()
            if ratio > best_ratio:
                best, best_ratio = r, ratio
        return best if best_ratio > 0.6 else None


DIFFICULTY = {"draft_reply": 0.4}  # long nuanced replies: open models fall short in the demo


def candidate_answer(model: str, rec: dict | None, request: dict) -> tuple[str, dict | None]:
    profile = MODELS[model][2] if model in MODELS else "medium"
    if rec is None:
        return ("I can help with that. Could you share more details?", None)
    text, tool = rec.get("response_text") or "", rec.get("response_tool_call")
    if profile == "frontier":
        return text, tool
    site = rec.get("site_id", "")
    func = site.split(":")[-2].split(".")[-1] if ":" in site else ""
    seed = _h(model, Knowledge.key(request))
    roll = (seed % 1000) / 1000
    hard = DIFFICULTY.get(func, 0.0)
    strength = {"strong": 0.15, "medium": 0.35, "weak": 0.75}[profile] + hard

    if tool:
        args = dict(tool.get("arguments") or {})
        if profile == "weak" and roll < 0.35:
            names = [t.get("function", {}).get("name") for t in request.get("tools") or []]
            other = [n for n in names if n and n != tool["name"]]
            return "", {"name": other[0] if other else tool["name"], "arguments": args}
        if profile == "medium" and roll < 0.3 and args:
            k = sorted(args)[0]
            args[k] = paraphrase(str(args[k]), 0.9, seed) if isinstance(args[k], str) else args[k]
        return "", {"name": tool["name"], "arguments": args}

    try:
        value = json.loads(text)
        is_json = isinstance(value, (dict, list))
    except ValueError:
        is_json = False
    if is_json:
        if (profile == "weak" and roll < 0.3) or (profile == "medium" and 0.4 <= roll < 0.5) \
                or (profile == "strong" and roll >= 0.9):
            return text[: max(10, len(text) // 2)], None  # broken JSON
        if profile == "medium" and roll < 0.4:
            return "```json\n" + json.dumps(value, indent=2) + "\n```", None
        if profile == "medium" and isinstance(value, dict) and roll > 0.85:
            v = dict(value)
            k = sorted(v)[-1]
            if isinstance(v[k], str):
                v[k] = paraphrase(v[k], 1.0, seed)
            return json.dumps(v), None
        return json.dumps(value), None

    if len(text.split()) <= 4:  # a label
        wrong_rate = {"strong": 0.0, "medium": 0.0, "weak": 0.4}[profile]
        if roll < wrong_rate:
            labels = ["low", "medium", "high", "urgent"]
            cur = text.strip().lower()
            alt = labels[(labels.index(cur) + 1) % 4] if cur in labels else "medium"
            return alt, None
        return text, None
    return paraphrase(text, strength, seed), None


def judge_answer(request: dict) -> str:
    user = next((m.get("content") for m in reversed(request.get("messages") or []) if m.get("role") == "user"), "")
    ref = re.search(r"### REFERENCE answer\n(.*?)(?:\n\n### |\n\nScore the CANDIDATE)", user, re.S)
    cand = re.search(r"### CANDIDATE answer\n(.*?)(?:\n\n### |\n\nScore the CANDIDATE)", user, re.S)
    a, b = (ref.group(1) if ref else ""), (cand.group(1) if cand else "")
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    length = len(b) / max(len(a), 1)
    if length < 0.85:
        ratio -= 0.3 * (0.85 - length) / 0.85 + 0.1
    score = 5 if ratio > 0.92 else 4 if ratio > 0.78 else 3 if ratio > 0.6 else 2 if ratio > 0.4 else 1
    reason = {5: "Same meaning and format as the reference.", 4: "Minor wording differences; still complete.",
              3: "Usable but misses some details.", 2: "Leaves out important parts of the reference.",
              1: "Does not answer the task."}[score]
    return json.dumps({"score": score, "reason": reason})


def _tags(header: str | None) -> dict:
    out = {}
    for pair in (header or "").split(","):
        if "=" in pair:
            k, v = pair.strip().split("=", 1)
            out[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
    return out


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _parse_time(v: str | None, default: float) -> float:
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        pass
    try:
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


def _rows(entries: list[dict], key) -> list[dict]:
    groups: dict = {}
    for e in entries:
        k = key(e)
        g = groups.setdefault(k, {"key": k, "label": None, "cost": 0.0, "requests": 0, "tokens": 0})
        g["cost"] = round(g["cost"] + e["cost"], 6)
        g["requests"] += 1
        g["tokens"] += e["tokens"]
    return sorted(groups.values(), key=lambda g: -g["cost"])


def make_handler(knowledge: Knowledge, state: dict):
    lock = threading.Lock()
    state.setdefault("log", [])
    state.setdefault("policies", {})

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _send(self, status: int, body, extra: dict | None = None, rid: str | None = None):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if rid:
                self.send_header("Otari-Request-ID", rid)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, status: int, detail, code: str | None, messages: bool = False,
                   extra: dict | None = None, rid: str | None = None):
            hdr = dict(extra or {})
            if code:
                hdr["Otari-Error-Code"] = code
            if messages:
                kind = {429: "rate_limit_error", 403: "permission_error", 404: "not_found_error"}.get(
                    status, "invalid_request_error")
                body = {"type": "error", "error": {"type": kind, "message": detail}}
            else:
                body = {"detail": detail}
            if code:
                body["code"] = code
            return self._send(status, body, hdr, rid)

        def _authed(self) -> bool:
            auth = self.headers.get("Authorization") or ""
            return auth.startswith("Bearer ") and len(auth) > 8

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def _spent(self) -> float:
            with lock:
                return sum(e["cost"] for e in state["log"])

        def _log(self, **e):
            with lock:
                state["log"].append({"t": time.time(), **e})

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            path, q = u.path.rstrip("/"), urllib.parse.parse_qs(u.query)
            if path.endswith("/health"):
                return self._send(200, {"status": "healthy", "demo": True})
            if not self._authed():
                return self._error(401, "Invalid API key", None)
            if path.endswith("/models"):
                return self._send(200, {"object": "list", "data": [
                    {"id": m, "object": "model", "name": m.split("/")[-1], "owned_by": m.split("/")[0],
                     "type": "chat", "pricing": {"input_price_per_million": i, "output_price_per_million": o},
                     "pricing_source": "defaults", "supported_parameters": sp or ALL_PARAMS}
                    for m, (i, o, _, _p, sp) in MODELS.items()]})
            if path.endswith("/key/budget"):
                spent = self._spent()
                now = dt.datetime.now(dt.timezone.utc)
                nxt = (now.replace(day=1) + dt.timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0,
                                                                            microsecond=0)
                rem = round(max(DEMO_BUDGET_USD - spent, 0.0), 6)
                return self._send(200, {"budgets_enforced": True, "remaining_usd": rem, "limits": [{
                    "scope": "user", "provider": None, "resets_at": nxt.isoformat().replace("+00:00", "Z"),
                    "limit_usd": DEMO_BUDGET_USD, "used_usd": round(spent, 6), "remaining_usd": rem,
                    "limit_tokens": None, "used_tokens": None, "remaining_tokens": None,
                    "limit_requests": None, "used_requests": None, "remaining_requests": None}]})
            if path.endswith("/key/usage/summary"):
                return self._send(200, self._summary(q))
            if "/usage/requests/" in path:
                rid = path.rsplit("/", 1)[-1]
                with lock:
                    rows = [e for e in state["log"] if e["rid"] == rid]
                if not rows:
                    return self._error(404, "Request not found", None)
                return self._send(200, {"request_id": rid, "status": "error" if rows[0]["error"] else "success",
                                        "cost_usd": f"{sum(e['cost'] for e in rows):.6f}",
                                        "prompt_tokens": sum(e["in"] for e in rows),
                                        "completion_tokens": sum(e["out"] for e in rows),
                                        "total_tokens": sum(e["tokens"] for e in rows), "row_count": len(rows)})
            if path.endswith("/mcp/servers"):
                return self._send(200, {"servers": []})
            if path.endswith("/tool-settings/guardrails/profiles"):
                return self._send(200, [])
            if path.endswith("/routing/policies"):
                return self._send(200, list(state["policies"].values()))
            return self._error(404, "Not Found", None)

        def _summary(self, q: dict) -> dict:
            now = time.time()
            start = _parse_time((q.get("start_date") or [None])[0], now - 7 * 86400)
            end = _parse_time((q.get("end_date") or [None])[0], now + 1)
            bucket = (q.get("bucket") or ["day"])[0]
            size = 3600 if bucket == "hour" else 86400
            want: dict[str, set] = {}  # tag=key:value, repeatable: same key any value, different keys all
            for t in q.get("tag") or []:
                k, _, v = t.partition(":")
                want.setdefault(k, set()).add(v)
            with lock:
                entries = [e for e in state["log"] if start <= e["t"] < end
                           and all(e["tags"].get(k) in vs for k, vs in want.items())]
            series: dict[int, dict] = {}
            for e in entries:
                b = int(e["t"] // size * size)
                p = series.setdefault(b, {"bucket_start": _iso(b), "cost": 0.0, "requests": 0, "tokens": 0,
                                          "errors": 0, "input_tokens": 0, "output_tokens": 0})
                p["cost"] = round(p["cost"] + e["cost"], 6)
                p["requests"] += 1
                p["tokens"] += e["tokens"]
                p["input_tokens"] += e["in"]
                p["output_tokens"] += e["out"]
                p["errors"] += 1 if e["error"] else 0
            tag = (q.get("group_by_tag") or [None])[0]
            ok = [e for e in entries if not e["error"]]
            lat = [e["ms"] for e in ok]
            return {
                "start_date": _iso(start), "end_date": _iso(end), "bucket": bucket,
                "totals": {"cost": round(sum(e["cost"] for e in entries), 6),
                           "prompt_tokens": sum(e["in"] for e in entries),
                           "completion_tokens": sum(e["out"] for e in entries),
                           "total_tokens": sum(e["tokens"] for e in entries), "cache_read_tokens": 0,
                           "cache_write_tokens": 0, "cache_write_1h_tokens": 0, "request_count": len(ok),
                           "error_count": len(entries) - len(ok),
                           "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else None},
                "series": [series[k] for k in sorted(series)],
                "by_model": _rows(entries, lambda e: e["model"]),
                "by_provider": _rows(entries, lambda e: e["provider"]),
                "by_user": [], "by_api_key": [], "by_source": [], "by_source_label": [], "by_endpoint": [],
                "errors_by_status_code": [],
                "by_tag": _rows(entries, lambda e: e["tags"].get(tag)) if tag else [],
            }

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            req = self._body()
            rid = str(uuid.uuid4())
            msgs = path.endswith("/messages")
            if not self._authed():
                return self._error(401, "Invalid API key", None)
            state["calls"] = state.get("calls", 0) + 1
            tags = _tags(self.headers.get("Otari-Tags"))
            if path.endswith("/routing/policies/explain"):
                spec = req.get("spec") or (state["policies"].get(req.get("name")) or {}).get("spec") or {}
                cands = [e.get("default") or e.get("target") for e in spec.get("select", [])] + \
                    list(spec.get("on_failure") or [])
                return self._send(200, {"name": req.get("name"), "demo": True, "candidates": [
                    {"model": c, "available": resolve(c or "") is not None} for c in cands]})
            if path.endswith("/routing/policies"):
                if not req.get("name") or not isinstance(req.get("spec"), dict):
                    return self._error(422, [{"loc": ["body", "spec"], "msg": "Field required", "type": "missing"}],
                                       "invalid_request")
                now = _iso(time.time())
                pol = {"name": req["name"], "spec": req["spec"], "source": "api", "is_dynamic": False,
                       "workspace_id": req.get("workspace_id"), "user_id": None, "created_at": now,
                       "updated_at": now}
                state["policies"][req["name"]] = pol
                return self._send(200, pol)
            if path.endswith("/code/execute"):
                code = req.get("code") or ""
                if not code.strip():
                    return self._error(422, [{"loc": ["body", "code"], "msg": "String should have at least 1 "
                                              "character", "type": "string_too_short"}], "invalid_request")
                t0 = time.time()
                try:
                    p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                                       timeout=min(int(req.get("timeout_seconds") or 20), 60))
                    out = {"object": "code_execution.result", "stdout": p.stdout, "stderr": p.stderr,
                           "return_code": p.returncode, "files": []}
                except subprocess.TimeoutExpired:
                    out = {"object": "code_execution.result", "stdout": "", "stderr": "Timed out",
                           "return_code": 124, "files": []}
                self._log(rid=rid, model="otari:code_execution", provider="otari", cost=0.0001, **{"in": 0},
                          out=0, tokens=0, ms=(time.time() - t0) * 1000, error=False, tags=tags)
                return self._send(200, out, rid=rid)
            if "user" in req:
                return self._error(403, "'user' field does not match the authenticated API key's user", None,
                                   msgs, rid=rid)
            model = req.get("model") or ""
            mid, attempts = resolve(model), 1
            pol = state["policies"].get(model)
            if mid is None and pol:  # a routing policy name is a model name: first target that resolves
                chain = [e.get("default") or e.get("target") for e in pol["spec"].get("select", [])] +                     list(pol["spec"].get("on_failure") or [])
                for i, target in enumerate(chain, 1):
                    if resolve(target or ""):
                        mid, attempts = resolve(target), i
                        break
            if mid is None:
                return self._error(400, f"Model '{model}' names no configured provider, alias, policy or "
                                        "catalog model", "invalid_model", msgs, rid=rid)
            if self._spent() >= DEMO_BUDGET_USD:
                return self._error(403, "API key user has exceeded budget limit", "budget_exceeded", msgs,
                                   {"Otari-Budget-Scope": "user"}, rid)
            in_price, out_price, profile, provider, supported = MODELS[mid]
            for bad in ("response_format", "reasoning_effort"):
                if supported and bad in req and bad not in supported:
                    self._log(rid=rid, model=mid, provider=provider, cost=0.0, **{"in": 0}, out=0, tokens=0,
                              ms=5.0, error=True, tags=tags)
                    return self._error(400, f"{bad} is not supported by {mid}", "unsupported_parameter", msgs,
                                       rid=rid)
            t0 = time.time()
            time.sleep(0.03 + (_h(mid, json.dumps(req, sort_keys=True)) % 120) / 1000)
            fallback = attempts > 1 or (profile == "medium" and _h("fb", json.dumps(req, sort_keys=True)) % 15 == 0)
            attempts = max(attempts, 2 if fallback else 1)
            if profile == "judge":
                text, tool = judge_answer(req), None
            else:
                text, tool = candidate_answer(mid, knowledge.find(req), req)
            p_tok = max(20, len(prompt_text(req)) // 4)
            c_tok = max(1, len(text or json.dumps(tool or {})) // 4)
            cost = round((p_tok * in_price + c_tok * out_price) / 1_000_000, 6)
            ms = (time.time() - t0) * 1000
            self._log(rid=rid, model=mid, provider=provider, cost=cost, **{"in": p_tok}, out=c_tok,
                      tokens=p_tok + c_tok, ms=ms, error=False, tags=tags)
            extra = {"Otari-Provider": provider, "Otari-Model": mid.split("/", 1)[-1],
                     "Otari-Fallback": "true" if fallback else "false",
                     "Otari-Attempt-Count": str(attempts),
                     "Otari-Response-Duration-Ms": str(int(ms)),
                     "Server-Timing": f"otari;dur=1.0, upstream;dur={ms:.1f}, total;dur={ms + 1:.1f}"}
            cost_s = f"{cost:.6f}"
            if msgs:
                content = [{"type": "text", "text": text}] if text else []
                if tool:
                    content.append({"type": "tool_use", "id": "toolu_demo", "name": tool["name"],
                                    "input": tool["arguments"]})
                usage = {"input_tokens": p_tok, "output_tokens": c_tok, "cost_usd": cost_s,
                         "pricing_source": "defaults"}
                return self._send(200, {"id": "msg_demo", "type": "message", "role": "assistant", "model": model,
                                        "content": content, "stop_reason": "end_turn", "usage": usage}, extra, rid)
            if path.endswith("/responses"):
                output = [{"type": "message", "content": [{"type": "output_text", "text": text}]}]
                if tool:
                    output = [{"type": "function_call", "name": tool["name"],
                               "arguments": json.dumps(tool["arguments"])}]
                usage = {"input_tokens": p_tok, "output_tokens": c_tok, "total_tokens": p_tok + c_tok,
                         "cost_usd": cost_s, "pricing_source": "defaults"}
                return self._send(200, {"id": "resp_demo", "object": "response", "model": model, "output": output,
                                        "usage": usage}, extra, rid)
            msg = {"role": "assistant", "content": text if not tool else None}
            if tool:
                msg["tool_calls"] = [{"id": "call_demo", "type": "function", "function": {
                    "name": tool["name"], "arguments": json.dumps(tool["arguments"])}}]
            usage = {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok,
                     "cost_usd": cost_s, "pricing_source": "defaults"}
            return self._send(200, {"id": "chatcmpl-demo", "object": "chat.completion", "model": model,
                                    "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                                    "usage": usage}, extra, rid)

    return H


def start(recordings: list[dict], port: int = 0) -> tuple[str, ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Knowledge(recordings), {}))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/api/v1", server
