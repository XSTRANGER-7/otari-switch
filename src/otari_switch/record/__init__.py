"""Record what each AI call site really sends, by wrapping the OpenAI and Anthropic SDKs.

The wrapper forwards every call unchanged and only observes it. Activated by the
OTARI_SWITCH_RECORD=1 environment variable (set by `otari-switch record`).
"""

from __future__ import annotations

import functools
import hashlib
import importlib
import inspect
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from otari_switch.otari_client import extract_answer

_LOCK = threading.Lock()
_INSTALLED = False

PATCH_TARGETS = [
    # (module, class, method, provider, endpoint)
    ("openai.resources.chat.completions", "Completions", "create", "openai", "chat"),
    ("openai.resources.chat.completions", "AsyncCompletions", "create", "openai", "chat"),
    ("openai.resources.chat.completions.completions", "Completions", "create", "openai", "chat"),
    ("openai.resources.chat.completions.completions", "AsyncCompletions", "create", "openai", "chat"),
    ("openai.resources.responses", "Responses", "create", "openai", "responses"),
    ("openai.resources.responses", "AsyncResponses", "create", "openai", "responses"),
    ("anthropic.resources.messages", "Messages", "create", "anthropic", "messages"),
    ("anthropic.resources.messages", "AsyncMessages", "create", "anthropic", "messages"),
]


class Recorder:
    def __init__(self, root: str | os.PathLike, limit_per_site: int = 20):
        self.root = Path(root).resolve()
        self.work = self.root / ".otari-switch"
        self.work.mkdir(parents=True, exist_ok=True)
        self.out = self.work / "recordings.jsonl"
        self.limit = limit_per_site
        self.sites = self._load_sites()
        self.counts: dict[str, int] = {}
        self.hashes: set[str] = set()
        self.unmatched = 0
        if self.out.exists():
            for line in self.out.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                self.counts[d["site_id"]] = self.counts.get(d["site_id"], 0) + 1
                self.hashes.add(d.get("hash", ""))

    def _load_sites(self) -> list[dict]:
        p = self.work / "scan.json"
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8")).get("sites", [])

    # map a stack to a scanned call site
    def site_for_stack(self) -> str | None:
        frame = inspect.currentframe()
        candidates = []
        while frame is not None:
            fn = frame.f_code.co_filename
            try:
                rel = Path(fn).resolve().relative_to(self.root).as_posix()
            except ValueError:
                rel = None
            if rel and "site-packages" not in fn and "/otari_switch/" not in fn.replace("\\", "/"):
                candidates.append((rel, frame.f_lineno))
            frame = frame.f_back
        for rel, line in candidates:
            for s in self.sites:
                if s["file"] == rel and s["line"] <= line <= s["end_line"]:
                    return s["id"]
        return None

    def save(self, site_id: str, provider: str, endpoint: str, request: dict, body: Any,
             latency_ms: float) -> None:
        clean = _jsonable(request)
        h = hashlib.sha256(json.dumps([site_id, clean.get("messages"), clean.get("input"),
                                       clean.get("system")], sort_keys=True, default=str).encode()).hexdigest()[:16]
        with _LOCK:
            if h in self.hashes or self.counts.get(site_id, 0) >= self.limit:
                return
            text, tool = extract_answer(endpoint, _jsonable(body))
            rec = {
                "site_id": site_id, "provider": provider, "endpoint": endpoint, "request": clean,
                "response_text": text, "response_tool_call": tool, "latency_ms": round(latency_ms, 1),
                "model": clean.get("model"), "masked": 0, "hash": h,
            }
            with self.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            self.hashes.add(h)
            self.counts[site_id] = self.counts.get(site_id, 0) + 1


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items() if not _skip(k, v)}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    for attr in ("model_dump", "dict", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return _jsonable(fn())
            except Exception:  # noqa: BLE001
                continue
    if inspect.isclass(obj):  # pydantic class passed as response_format
        schema = getattr(obj, "model_json_schema", None)
        if callable(schema):
            return {"type": "json_schema", "json_schema": {"name": obj.__name__, "schema": schema()}}
    return str(obj)


def _skip(k: Any, v: Any) -> bool:
    if k in {"extra_headers", "extra_query", "extra_body", "timeout"}:
        return True
    return type(v).__name__ in {"NotGiven", "Omit"}


class _StreamCollector:
    """Wraps a sync stream so the user still gets every chunk; records the joined text at the end."""

    def __init__(self, stream, on_done):
        self._s = stream
        self._parts: list[str] = []
        self._done = on_done
        self._finished = False

    def __iter__(self):
        try:
            for chunk in self._s:
                try:
                    d = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
                    ch = (d.get("choices") or [{}])[0]
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        self._parts.append(delta["content"])
                    if d.get("type") == "content_block_delta":
                        self._parts.append((d.get("delta") or {}).get("text", ""))
                except Exception:  # noqa: BLE001
                    pass
                yield chunk
        finally:
            if not self._finished:
                self._finished = True
                self._done("".join(self._parts))

    def __getattr__(self, name):
        return getattr(self._s, name)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        close = getattr(self._s, "close", None)
        if callable(close):
            close()


def _wrap(orig, provider: str, endpoint: str, rec: Recorder):
    if inspect.iscoroutinefunction(orig):
        @functools.wraps(orig)
        async def awrapper(self, *args, **kwargs):
            t0 = time.perf_counter()
            result = await orig(self, *args, **kwargs)
            _observe(rec, provider, endpoint, kwargs, result, t0)
            return result
        awrapper.__otari_switch__ = True
        return awrapper

    @functools.wraps(orig)
    def wrapper(self, *args, **kwargs):
        t0 = time.perf_counter()
        result = orig(self, *args, **kwargs)
        if kwargs.get("stream"):
            site = rec.site_for_stack()
            if site:
                def done(text, kw=dict(kwargs), site=site):
                    rec.save(site, provider, endpoint, kw,
                             {"choices": [{"message": {"content": text}}]} if endpoint != "messages"
                             else {"type": "message", "content": [{"type": "text", "text": text}]},
                             (time.perf_counter() - t0) * 1000)
                return _StreamCollector(result, done)
            return result
        _observe(rec, provider, endpoint, kwargs, result, t0)
        return result
    wrapper.__otari_switch__ = True
    return wrapper


def _observe(rec: Recorder, provider, endpoint, kwargs, result, t0):
    try:
        site = rec.site_for_stack()
        if site is None:
            rec.unmatched += 1
            return
        rec.save(site, provider, endpoint, dict(kwargs), result, (time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001 - recording must never break the user's code
        sys.stderr.write(f"[otari-switch] could not record a call: {e}\n")


def install(root: str | os.PathLike | None = None, limit: int | None = None) -> Recorder | None:
    global _INSTALLED
    if _INSTALLED:
        return None
    root = root or os.environ.get("OTARI_SWITCH_ROOT") or os.getcwd()
    limit = limit or int(os.environ.get("OTARI_SWITCH_LIMIT", "20"))
    rec = Recorder(root, limit)
    patched = 0
    for mod, cls, meth, provider, endpoint in PATCH_TARGETS:
        try:
            m = importlib.import_module(mod)
            c = getattr(m, cls)
            orig = getattr(c, meth)
        except (ImportError, AttributeError):
            continue
        if getattr(orig, "__otari_switch__", False):
            continue
        setattr(c, meth, _wrap(orig, provider, endpoint, rec))
        patched += 1
    _INSTALLED = True
    if patched == 0:
        sys.stderr.write("[otari-switch] neither the openai nor the anthropic package is importable; nothing to record.\n")
    return rec


def install_from_env() -> Recorder | None:
    if os.environ.get("OTARI_SWITCH_RECORD") == "1":
        return install()
    return None
