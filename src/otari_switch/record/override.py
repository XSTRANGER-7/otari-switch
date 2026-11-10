"""During the test gate: send each call site's requests to its chosen model through Otari.

Reads OTARI_SWITCH_OVERRIDE = JSON {site_id: model} or {site_id: {"model": m, "guard": today's model}}.
A guard does what the PR's runtime JSON guard does: if the answer is not valid JSON, retry once on today's
model, so the tests run the code as it will behave after the merge. The base URL and key come from the
standard OPENAI_BASE_URL / OPENAI_API_KEY and ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY variables,
which the test gate points at Otari.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import json
import os

from otari_switch.record import PATCH_TARGETS, Recorder

_DONE = False


def install_from_env() -> bool:
    global _DONE
    raw = os.environ.get("OTARI_SWITCH_OVERRIDE")
    if not raw or _DONE:
        return False
    mapping: dict = json.loads(raw)
    rec = Recorder(os.environ.get("OTARI_SWITCH_ROOT") or os.getcwd(), limit_per_site=0)
    base = os.environ.get("OTARI_SWITCH_BASE_URL")
    key = os.environ.get("OTARI_SWITCH_API_KEY")

    def target(site):
        v = mapping.get(site)
        if isinstance(v, dict):
            return v.get("model"), v.get("guard")
        return v, None

    tags = os.environ.get("OTARI_SWITCH_TAGS")
    end_user = os.environ.get("OTARI_SWITCH_END_USER") or ""
    end_user_budget = os.environ.get("OTARI_SWITCH_END_USER_BUDGET") or ""

    def wrap(orig, provider):
        def fix(self, kwargs, model):
            if model:
                kwargs["model"] = model
            headers = dict(kwargs.get("extra_headers") or {})
            if tags:  # attribute this traffic in Otari's usage (never forwarded to the provider)
                headers["Otari-Tags"] = tags
            if end_user:  # team mode: bill the teammate's own end-user budget (Otari service key)
                if provider == "anthropic":
                    kwargs["metadata"] = {**(kwargs.get("metadata") or {}), "user_id": end_user}
                else:
                    kwargs["user"] = end_user
                if end_user_budget:
                    headers["Otari-End-User-Budget"] = end_user_budget
            if headers:
                kwargs["extra_headers"] = headers
            # clients built with an explicit base_url still go to Otari during the gate
            client = getattr(self, "_client", None)
            if client is not None and base:
                try:
                    url = base if provider == "openai" else base.rsplit("/v1", 1)[0]
                    client.base_url = url
                    if key:
                        client.api_key = key
                except Exception:  # noqa: BLE001
                    pass
            return kwargs

        if inspect.iscoroutinefunction(orig):
            @functools.wraps(orig)
            async def a(self, *args, **kwargs):
                model, guard = target(rec.site_for_stack())
                kw = fix(self, kwargs, model)
                resp = await orig(self, *args, **kw)
                if guard and not kw.get("stream") and not _json_ok(resp):
                    resp = await orig(self, *args, **{**kw, "model": guard})
                return resp
            return a

        @functools.wraps(orig)
        def s(self, *args, **kwargs):
            model, guard = target(rec.site_for_stack())
            kw = fix(self, kwargs, model)
            resp = orig(self, *args, **kw)
            if guard and not kw.get("stream") and not _json_ok(resp):
                resp = orig(self, *args, **{**kw, "model": guard})
            return resp
        return s

    for mod, cls, meth, provider, _endpoint in PATCH_TARGETS:
        try:
            c = getattr(importlib.import_module(mod), cls)
            setattr(c, meth, wrap(getattr(c, meth), provider))
        except (ImportError, AttributeError):
            continue
    _DONE = True
    return True


def _json_ok(resp) -> bool:
    """The same check as the runtime guard the PR adds (rewrite.GUARD_MODULE)."""
    text = ""
    try:
        text = resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        try:
            text = "".join(getattr(b, "text", "") for b in resp.content)
        except (AttributeError, TypeError):
            text = getattr(resp, "output_text", "") or ""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        json.loads(text)
        return True
    except ValueError:
        return False
