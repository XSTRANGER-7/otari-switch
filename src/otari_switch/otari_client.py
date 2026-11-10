"""Every call to Otari goes through this module (standard library only).

Shapes follow Otari's API reference and its committed OpenAPI document
(https://github.com/mozilla-ai/otari, docs/api-reference.md and docs/public/openapi.json):

- Inference: POST /chat/completions, /messages, /responses under the base URL
  (hosted: https://api.otari.ai/api/v1, EU: https://eu.api.otari.ai/api/v1, self-hosted: <host>/api/v1).
- Auth: `Authorization: Bearer <key>` (API keys start with `otk_` on otari.ai, `tk-` on a self-hosted gateway).
- Cost: `usage.cost_usd` is a six-decimal USD *string*, next to `usage.pricing_source`.
- Serving headers: Otari-Request-ID, Otari-Provider, Otari-Model, Otari-Attempt-Count, Otari-Fallback,
  Otari-Response-Duration-Ms, Server-Timing, Otari-Guardrails (monitor mode).
- Errors: `{"detail": ..., "code": ...}` plus an `Otari-Error-Code` header; the Messages routes use
  Anthropic's envelope `{"type": "error", "error": {"type", "message"}, "code"}`.
- Attribution: request tags in the `Otari-Tags` header (never forwarded to the provider). The `user` field is
  NOT free-form in Otari (a key may only name its own user), so it is never sent.
- Safe retries: the same `Idempotency-Key` on every attempt of one non-streaming request, so a lost response is
  replayed by Otari instead of billed twice.
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from otari_switch import __version__

USER_AGENT = f"otari-switch/{__version__}"

BUDGET_CODES = {"budget_exceeded", "insufficient_funds", "budget_reached", "payment_required"}
# 4xx answers that a retry cannot fix are raised at once; these statuses are worth another attempt
RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504, 529}


class OtariError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None,
                 request_id: str | None = None, body: Any = None, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.body = body
        self.headers = headers or {}

    @property
    def unsupported_param(self) -> bool:
        """The model refused a parameter the request carries (for example response_format)."""
        if self.code == "unsupported_parameter":
            return True
        return self.status == 400 and self.code in {None, "invalid_request_error", "all_candidates_rejected"}


class BudgetReached(OtariError):
    pass


class GuardrailBlocked(OtariError):
    pass


class Unreachable(OtariError):
    pass


class McpOutcomeUnknown(OtariError):
    """An MCP tool call may have run: never retry it, show it as indeterminate."""


@dataclass
class Response:
    status: int
    body: Any
    headers: dict[str, str]
    latency_ms: float

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    @property
    def request_id(self) -> str | None:
        return self.header("otari-request-id")

    @property
    def served_model(self) -> str | None:
        h = self.header("otari-model")
        if h:
            return h
        return self.body.get("model") if isinstance(self.body, dict) else None

    @property
    def provider(self) -> str | None:
        return self.header("otari-provider")

    @property
    def fallback(self) -> bool:
        return (self.header("otari-fallback") or "").strip().lower() == "true"

    @property
    def attempts(self) -> int:
        try:
            return int(self.header("otari-attempt-count") or 1)
        except ValueError:
            return 1

    @property
    def gateway_ms(self) -> float | None:
        try:
            return float(self.header("otari-response-duration-ms") or "")
        except ValueError:
            return None

    @property
    def replayed(self) -> bool:
        return (self.header("otari-idempotent-replayed") or "").lower() == "true"

    @property
    def guardrails(self) -> Any:
        raw = self.header("otari-guardrails")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    @property
    def cost_usd(self) -> float | None:
        """`usage.cost_usd` (a decimal string per the API reference), else the Otari-Cost-USD header."""
        if isinstance(self.body, dict):
            usage = self.body.get("usage") or {}
            if isinstance(usage, dict):
                v = _num(usage.get("cost_usd"))
                if v is not None:
                    return v
        return _num(self.header("otari-cost-usd"))

    @property
    def pricing_source(self) -> str | None:
        if isinstance(self.body, dict) and isinstance(self.body.get("usage"), dict):
            return self.body["usage"].get("pricing_source")
        return self.header("otari-pricing-source")

    @property
    def tokens(self) -> tuple[int | None, int | None]:
        """(input, output) tokens from a chat, responses or messages usage object."""
        usage = self.body.get("usage") if isinstance(self.body, dict) else None
        if not isinstance(usage, dict):
            return None, None
        i = usage.get("prompt_tokens", usage.get("input_tokens"))
        o = usage.get("completion_tokens", usage.get("output_tokens"))
        return (int(i) if isinstance(i, (int, float)) else None, int(o) if isinstance(o, (int, float)) else None)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip():
        try:
            return float(v)
        except ValueError:
            return None
    return None


class SpendMeter:
    """Thread-safe spend tracker with a hard cap. Never lets a run pass its budget."""

    def __init__(self, cap_usd: float, estimate_per_call: float = 0.002):
        self.cap = float(cap_usd)
        self.spent = 0.0
        self.calls = 0
        self.unknown_cost_calls = 0
        self.by_role: dict[str, float] = {}
        self._estimate = estimate_per_call
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            if self.spent + self._estimate > self.cap:
                raise BudgetReached(
                    f"Evaluation budget of ${self.cap:.2f} reached (spent ${self.spent:.4f}).",
                    code="budget_reached",
                )

    def add(self, cost: float | None, role: str | None = None) -> None:
        with self._lock:
            self.calls += 1
            if cost is None:
                self.unknown_cost_calls += 1
                cost = self._estimate
            self.spent += cost
            if role:
                self.by_role[role] = self.by_role.get(role, 0.0) + cost
            # keep the estimate close to reality
            self._estimate = max(self._estimate * 0.8 + cost * 0.2, 0.0001)


def tags_header(tags: dict[str, str]) -> str:
    """Encode request tags for the Otari-Tags header (comma-separated key=value, percent-encoded)."""
    q = lambda s: urllib.parse.quote(str(s)[:500], safe="-_.~:/@")  # noqa: E731
    return ",".join(f"{q(k)[:64]}={q(v)}" for k, v in tags.items() if v not in (None, ""))


class OtariClient:
    def __init__(self, base_url: str, api_key: str | None, timeout_s: float = 60,
                 meter: SpendMeter | None = None, max_retries: int = 3, user: str | None = None,
                 tags: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.meter = meter
        self.max_retries = max_retries
        # `user` is kept as a tag only: Otari bills the key's own user and refuses other values
        self.tags = {"tool": "otari-switch", **(tags or {})}
        if user:
            self.tags["teammate"] = user

    # --- low level -------------------------------------------------------------
    def _request(self, method: str, path: str, payload: Any = None, extra_headers: dict | None = None,
                 metered: bool = False, role: str | None = None, retry: bool = True,
                 idempotent: bool = False) -> Response:
        url = self.base_url + path
        data = None if payload is None else json.dumps(payload).encode()
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if metered:
            tags = dict(self.tags)
            if role:
                tags["role"] = role
            headers["Otari-Tags"] = tags_header(tags)
        if idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        if extra_headers:
            headers.update({k: v for k, v in extra_headers.items() if v is not None})

        attempt = 0
        waited_409 = 0
        while True:
            attempt += 1
            if metered and self.meter and attempt == 1:
                self.meter.check()
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    raw = r.read()
                    hdrs = {k.lower(): v for k, v in r.headers.items()}
                    resp = Response(r.status, _parse(raw), hdrs, (time.perf_counter() - t0) * 1000)
                    if metered and self.meter:
                        self.meter.add(resp.cost_usd, role)
                    return resp
            except urllib.error.HTTPError as e:
                raw = e.read()
                body = _parse(raw)
                hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
                rid = hdrs.get("otari-request-id") or (body.get("request_id") if isinstance(body, dict) else None)
                code, msg = _error_info(body)
                code = hdrs.get("otari-error-code") or code
                msg = msg or f"Otari returned HTTP {e.code}"
                args = (e.code, code, rid, body, hdrs)
                if code in BUDGET_CODES or (e.code == 402 and code not in {"pricing_required"}):
                    scope = hdrs.get("otari-budget-scope")
                    raise BudgetReached(_budget_message(msg, scope, hdrs.get("retry-after")), *args) from None
                if code == "guardrail_violation":
                    raise GuardrailBlocked(msg, *args) from None
                if code == "pricing_required":
                    raise OtariError(f"{msg} (the model has no price in Otari, or does not exist on that provider)",
                                     *args) from None
                if e.code == 421:
                    raise OtariError(f"This key belongs to another Otari region: {msg} "
                                     "Switch the region (Global or EU) to match the key.", *args) from None
                if e.code in (401, 403) and code is None and not msg.startswith("Otari"):
                    msg = f"{msg} (check the Otari key)"
                no_retry = (hdrs.get("x-should-retry") or "").lower() == "false"
                if e.code == 409 and idempotent and retry and waited_409 < 12:
                    # the first attempt of this Idempotency-Key is still running at Otari (a slow model, or our
                    # timeout fired first): wait for it, then Otari replays its answer without billing again
                    waited_409 += 1
                    time.sleep(min(_backoff(1, hdrs.get("retry-after")) + 4, 10))
                    continue
                if e.code == 409 and idempotent:
                    raise OtariError("No answer in time: Otari was still working on this request after about two "
                                     "minutes of waiting (a very slow model). Its first attempt may still be billed.",
                                     *args) from None
                if retry and e.code in RETRY_STATUSES and e.code != 409 and not no_retry \
                        and attempt <= self.max_retries:
                    time.sleep(_backoff(attempt, hdrs.get("retry-after")))
                    continue
                raise OtariError(msg, *args) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                # a lost response is replayed (not re-billed) when the request carries an Idempotency-Key
                if retry and attempt <= self.max_retries and (idempotent or method == "GET"):
                    time.sleep(_backoff(attempt, None))
                    continue
                raise Unreachable(f"Could not reach Otari at {self.base_url}: {getattr(e, 'reason', e)}") from None

    # --- catalog ---------------------------------------------------------------
    def models(self) -> list[dict]:
        """GET /models: one entry per model, `id` is the catalog id, with pricing and supported_parameters."""
        r = self._request("GET", "/models")
        body = r.body or {}
        items = body.get("data") if isinstance(body, dict) else body
        return [m if isinstance(m, dict) else {"id": str(m)} for m in (items or [])]

    def guardrail_profiles(self) -> list[dict]:
        r = self._request("GET", "/tool-settings/guardrails/profiles")
        b = r.body
        if isinstance(b, dict):
            b = b.get("profiles") or b.get("data") or []
        return [p if isinstance(p, dict) else {"name": str(p)} for p in (b or [])]

    # --- inference -------------------------------------------------------------
    def chat(self, request: dict, model: str, conversation_id: str | None = None, role: str = "candidate",
             extra_headers: dict | None = None) -> Response:
        payload = dict(request)
        payload["model"] = model
        for k in ("stream", "stream_options", "user"):
            payload.pop(k, None)
        hdr = {"Otari-Conversation-Id": conversation_id, **(extra_headers or {})}
        return self._request("POST", "/chat/completions", payload, hdr, metered=True, role=role, idempotent=True)

    def responses(self, request: dict, model: str, role: str = "candidate") -> Response:
        payload = dict(request)
        payload["model"] = model
        for k in ("stream", "user", "previous_response_id", "store"):
            payload.pop(k, None)
        return self._request("POST", "/responses", payload, metered=True, role=role, idempotent=True)

    def messages(self, request: dict, model: str, role: str = "candidate") -> Response:
        payload = dict(request)
        payload["model"] = model
        payload.pop("stream", None)
        payload.setdefault("max_tokens", 1024)
        return self._request("POST", "/messages", payload, {"anthropic-version": "2023-06-01"}, metered=True,
                             role=role, idempotent=True)

    def send(self, endpoint: str, request: dict, model: str, role: str = "candidate") -> Response:
        if endpoint == "messages":
            return self.messages(request, model, role=role)
        if endpoint == "responses":
            return self.responses(request, model, role=role)
        return self.chat(request, model, role=role)

    # --- sandbox ---------------------------------------------------------------
    def code_execute(self, code: str, timeout_s: int = 60) -> dict:
        """POST /code/execute (Python in Otari's sandbox).

        Returns {'ok', 'stdout', 'stderr', 'exit_code', 'request_id'}. Failing code is still HTTP 200 with a
        nonzero `return_code`; a 400 means no sandbox is configured for the workspace.
        """
        r = self._request("POST", "/code/execute", {"code": code, "timeout_seconds": int(timeout_s)},
                          metered=True, role="sandbox", retry=False)
        return _code_result(r)

    # --- routing policies (operator: master key on a self-hosted gateway) -------
    def list_policies(self) -> list[dict]:
        r = self._request("GET", "/routing/policies")
        b = r.body
        if isinstance(b, dict):
            b = b.get("policies") or b.get("data") or []
        return b or []

    def explain_policy(self, name: str, spec: dict | None = None, workspace_id: str | None = None) -> dict:
        body: dict[str, Any] = {"name": name}
        if spec is not None:
            body["spec"] = spec
        if workspace_id:
            body["workspace_id"] = workspace_id
        r = self._request("POST", "/routing/policies/explain", body, retry=False)
        return r.body if isinstance(r.body, dict) else {"result": r.body}

    def create_policy(self, name: str, spec: dict, workspace_id: str | None = None) -> dict:
        """POST /routing/policies with Otari's PolicyRequest: {name, spec, workspace_id?}."""
        body: dict[str, Any] = {"name": name, "spec": spec}
        if workspace_id:
            body["workspace_id"] = workspace_id
        r = self._request("POST", "/routing/policies", body, retry=False)
        return r.body if isinstance(r.body, dict) else {"result": r.body}

    # --- caller-orchestrated MCP (servers stored in the Otari workspace) --------
    def mcp_servers(self) -> list[dict]:
        r = self._request("GET", "/mcp/servers")
        return (r.body or {}).get("servers") or [] if isinstance(r.body, dict) else []

    def mcp_tools(self, server_id: str) -> dict:
        r = self._request("GET", f"/mcp/servers/{urllib.parse.quote(server_id)}/tools")
        return r.body if isinstance(r.body, dict) else {}

    def mcp_execute(self, server_id: str, tool_name: str, arguments: dict, server_revision: str,
                    conversation_id: str | None = None) -> dict:
        """POST /mcp/execute: exactly one attempt, never retried (the tool may have run)."""
        body = {"mcp_server_id": server_id, "tool_name": tool_name, "arguments": arguments,
                "server_revision": server_revision, "client_execution_id": str(uuid.uuid4())}
        try:
            r = self._request("POST", "/mcp/execute", body, {"Otari-Conversation-Id": conversation_id},
                              retry=False)
        except Unreachable as e:
            raise McpOutcomeUnknown(f"{e} The tool may or may not have run; check before trying again.") from None
        except OtariError as e:
            state = e.body.get("execution_state") if isinstance(e.body, dict) else None
            if state == "outcome_unknown":
                raise McpOutcomeUnknown(f"{e} The tool may or may not have run; check before trying again.",
                                        e.status, e.code, e.request_id, e.body, e.headers) from None
            raise
        out = r.body if isinstance(r.body, dict) else {"content": [{"type": "text", "text": str(r.body)}]}
        out["_request_id"] = r.request_id
        return out

    # --- the key's own budget and usage (API keys may read these) ---------------
    def key_budget(self) -> dict:
        """GET /key/budget: what this key may still spend and every cap that decides it."""
        r = self._request("GET", "/key/budget")
        return r.body if isinstance(r.body, dict) else {}

    def key_usage_summary(self, start_date: str | None = None, end_date: str | None = None,
                          bucket: str = "day", group_by_tag: str | None = None,
                          dimensions: list[str] | None = None, tags: list[str] | None = None) -> dict:
        """GET /key/usage/summary over this key's own requests (series, totals, by_model, by_tag)."""
        params: list[tuple[str, str]] = [("bucket", bucket)]
        if start_date:
            params.append(("start_date", start_date))
        if end_date:
            params.append(("end_date", end_date))
        if group_by_tag:
            params.append(("group_by_tag", group_by_tag))
        for d in dimensions or []:
            params.append(("dimensions", d))
        for t in tags or []:
            params.append(("tag", t))
        r = self._request("GET", "/key/usage/summary?" + urllib.parse.urlencode(params))
        return r.body if isinstance(r.body, dict) else {}

    def request_cost(self, request_id: str) -> dict:
        """GET /usage/requests/{id}: what one request was billed, all fallback attempts included."""
        r = self._request("GET", f"/usage/requests/{urllib.parse.quote(request_id)}")
        return r.body if isinstance(r.body, dict) else {}


# --- helpers ---------------------------------------------------------------------

def _parse(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")


def _error_info(body: Any) -> tuple[str | None, str | None]:
    """(code, message) from Otari's `{detail, code}`, Anthropic's envelope, or OpenAI's `{error: {...}}`."""
    if isinstance(body, dict):
        code = body.get("code")
        msg = body.get("detail") or body.get("message")
        err = body.get("error")
        if isinstance(err, dict):
            code = code or err.get("code") or err.get("type")
            msg = msg or err.get("message")
        elif isinstance(err, str) and not msg:
            msg = err
        if isinstance(msg, list):  # 422 validation detail: [{loc, msg, type}]
            parts = []
            for item in msg[:3]:
                if isinstance(item, dict):
                    loc = ".".join(str(x) for x in item.get("loc", []) if x not in ("body",))
                    parts.append(f"{loc}: {item.get('msg')}" if loc else str(item.get("msg")))
            msg = "Invalid request: " + "; ".join(parts) if parts else json.dumps(msg)[:300]
        elif isinstance(msg, dict):
            msg = json.dumps(msg)[:300]
        return (str(code) if code else None), msg
    if isinstance(body, str) and body.strip().startswith("<"):
        return None, "Otari's edge returned an HTML error page instead of JSON (check the base URL ends in /api/v1)."
    return None, body if isinstance(body, str) else None


def _budget_message(msg: str, scope: str | None, retry_after: str | None) -> str:
    out = f"Otari refused the request: budget reached. {msg}".strip()
    if scope:
        out += f" (budget scope: {scope})"
    if retry_after and retry_after.isdigit():
        hours = int(retry_after) / 3600
        out += f" It resets in about {hours:.1f} h." if hours >= 1 else f" It resets in {int(retry_after)} s."
    return out


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 20.0)
        except ValueError:
            pass
    return min(2 ** (attempt - 1), 8) + random.random() * 0.3


def _code_result(r: Response) -> dict:
    b = r.body if isinstance(r.body, dict) else {"stdout": str(r.body or "")}
    stdout = b.get("stdout") or ""
    stderr = b.get("stderr") or ""
    rc = b.get("return_code")
    if rc is None:
        rc = 1 if "Traceback" in stderr else 0
    return {"ok": rc == 0, "stdout": stdout, "stderr": stderr, "exit_code": rc,
            "request_id": r.request_id, "files": b.get("files") or []}


def answer_from(endpoint: str, r: Response) -> tuple[str, dict | None]:
    """Extract (text, first tool call) from a chat, responses or messages body."""
    return extract_answer(endpoint, r.body)


def extract_answer(endpoint: str, body: Any) -> tuple[str, dict | None]:
    if not isinstance(body, dict):
        return (str(body or ""), None)
    if endpoint == "messages" or body.get("type") == "message":
        text, tool = [], None
        for block in body.get("content") or []:
            if block.get("type") == "text":
                text.append(block.get("text", ""))
            elif block.get("type") == "tool_use" and tool is None:
                tool = {"name": block.get("name"), "arguments": block.get("input") or {}}
        return ("".join(text), tool)
    if "choices" in body:
        msg = (body["choices"] or [{}])[0].get("message") or {}
        tool = None
        calls = msg.get("tool_calls") or []
        if calls:
            fn = calls[0].get("function") or {}
            args = fn.get("arguments")
            try:
                args = json.loads(args) if isinstance(args, str) else (args or {})
            except ValueError:
                args = {"_raw": args}
            tool = {"name": fn.get("name"), "arguments": args}
        content = msg.get("content")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return (content or "", tool)
    if "output" in body:  # responses API
        text, tool = [], None
        for item in body.get("output") or []:
            if item.get("type") == "message":
                for c in item.get("content") or []:
                    if c.get("type") in {"output_text", "text"}:
                        text.append(c.get("text", ""))
            elif item.get("type") == "function_call" and tool is None:
                args = item.get("arguments")
                try:
                    args = json.loads(args) if isinstance(args, str) else (args or {})
                except ValueError:
                    args = {"_raw": args}
                tool = {"name": item.get("name"), "arguments": args}
        if not text and isinstance(body.get("output_text"), str):
            text = [body["output_text"]]
        return ("".join(text), tool)
    return (json.dumps(body), None)
