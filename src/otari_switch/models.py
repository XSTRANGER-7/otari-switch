"""Plain data types shared by every layer. Everything serialises to JSON."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any

SWITCH = "switch"
SWITCH_WITH_FALLBACK = "switch_with_fallback"
KEEP = "keep"


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


@dataclass
class CallSite:
    id: str
    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    function: str
    provider: str  # openai | anthropic
    endpoint: str  # chat | responses | messages | embeddings | other
    model: str | None  # literal model, or None if dynamic
    model_source: str  # literal | constant | env | dynamic | missing
    model_span: list[int] | None = None  # [line, col, end_line, end_col] of the model expression
    model_env: str | None = None
    flags: list[str] = field(default_factory=list)  # json, schema, tools, stream
    evaluable: bool = True
    note: str = ""

    @property
    def short(self) -> str:
        return self.function.replace("_", "-") if self.function != "<module>" else "module"

    @classmethod
    def from_dict(cls, d: dict) -> "CallSite":
        return cls(**{k: d[k] for k in d if k in {f.name for f in dataclasses.fields(cls)}})


@dataclass
class ClientSite:
    """A client constructor such as OpenAI(...) that the rewrite points at Otari."""

    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    provider: str
    cls: str
    has_base_url: bool
    has_api_key: bool


@dataclass
class Recording:
    site_id: str
    provider: str
    endpoint: str
    request: dict
    response_text: str
    response_tool_call: dict | None = None
    latency_ms: float | None = None
    model: str | None = None
    masked: int = 0
    hash: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Recording":
        return cls(**{k: d[k] for k in d if k in {f.name for f in dataclasses.fields(cls)}})


@dataclass
class Answer:
    text: str
    tool_call: dict | None
    request_id: str | None
    served_model: str | None
    provider: str | None
    fallback: bool
    attempts: int
    cost_usd: float
    latency_ms: float
    error: str | None = None


@dataclass
class Sample:
    prompt_preview: str
    frontier: str
    candidate: str
    score: float
    reason: str
    checks: dict
    request_id: str | None
    judge_request_id: str | None = None


@dataclass
class CandidateResult:
    model: str
    mean_score: float = 0.0
    share_ok: float = 0.0  # share of answers scoring 3+
    check_pass_rate: float = 1.0
    cost_per_1k: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    errors: int = 0
    findings: list[str] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    tests_passed: bool | None = None
    rejected_params: list[str] = field(default_factory=list)  # params the model refused; the app still sends them


@dataclass
class SiteResult:
    site: CallSite
    recordings: int
    frontier_model: str | None
    frontier_cost_per_1k: float | None
    candidates: list[CandidateResult]
    verdict: str = KEEP
    chosen: str | None = None
    reason: str = ""
    policy: dict | None = None
    app_model: str | None = None  # the model id the app sends its provider today (frontier_model is Otari's id)
    frontier_p50_ms: float | None = None  # today's model through Otari, same prompts


@dataclass
class Run:
    id: str
    repo: str
    commit: str | None
    started: str
    finished: str | None
    region: str
    budget_usd: float
    spend_usd: float
    sites: list[SiteResult]
    summary: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    demo: bool = False

    def to_json(self) -> str:
        return json.dumps(to_dict(self), indent=2)


def load_run(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
