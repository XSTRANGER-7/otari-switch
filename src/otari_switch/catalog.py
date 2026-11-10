"""Read Otari's model catalog (GET /models) and suggest candidates.

Each entry's `id` is a catalog id (`vendor/model`) or a `provider:model` selector, with `type`, `pricing`
(`input_price_per_million`, `output_price_per_million`) and `supported_parameters` (docs/models.md).
"Open-weight" is decided from the vendor and model family, because the catalog has no licence field. The list
is conservative: a model counts only when its weights are published for download.
"""

from __future__ import annotations

import re

# vendors whose catalog models are open-weight (weights published for download)
OPEN_VENDORS = {
    "meta-llama", "meta", "qwen", "deepseek", "deepseek-ai", "moonshotai", "z-ai", "zai-org", "thudm", "nvidia",
    "ibm-granite", "allenai", "nousresearch", "minimax", "minimaxai", "baidu", "stepfun-ai", "stepfun",
    "inclusionai", "swiss-ai", "huggingfaceh4", "huggingfacetb", "arcee-ai", "liquid", "liquidai", "01-ai",
    "internlm", "tngtech", "cognitivecomputations", "sao10k", "mozilla", "demo",
}
# open families from vendors that also sell closed (API-only) models
OPEN_FAMILIES = [
    r"^google/gemma", r"^openai/gpt-oss",
    r"^mistralai/(mistral-small|mistral-nemo|mixtral|mistral-7b|mistral-large-3|codestral-mamba|magistral-small"
    r"|devstral|ministral|pixtral-12b|voxtral)",
    r"^cohere/(command-r|aya)", r"^microsoft/(phi|wizardlm)", r"^x-ai/grok-(1|2)$",
    r"^(alibaba|qwen)/qwen(?!.*-(max|plus|turbo)\b)(?!3\.\d+-flash)", r"^xiaomi/mimo", r"^bytedance/seed-oss",
    r"^(ibm/)?granite-", r"^tencent/hunyuan", r"^l3(\.\d)?-\d+b-",
]
# local backends serve downloaded weights
LOCAL_PROVIDERS = {"llamacpp", "llama_cpp", "ollama", "lmstudio", "vllm", "llamafile", "sglang", "tgi"}
CLOSED_FAMILIES = [r"^microsoft/(mai|copilot)", r"-max\b"]
# not drop-in replacements for a chat call: safety classifiers, embeddings, vision/audio/code specialists,
# reasoning-only models, previews and spot capacity
NOT_DROP_IN = re.compile(r"(guard|moderation|embed|rerank|ocr|tts|whisper|voxtral|-vl\b|-vl-|vision|coder|code"
                         r"|thinking|reasoner|-r1\b|-r1-|magistral|preview|-exp\b|-spot\b|experimental)")


def vendor_of(model_id: str) -> str:
    if ":" in model_id and "/" not in model_id.split(":", 1)[0]:
        prov, rest = model_id.split(":", 1)
        return rest.split("/", 1)[0].lower() if "/" in rest else prov.lower()
    return model_id.split("/", 1)[0].lower() if "/" in model_id else ""


def is_open_weight(model_id: str) -> bool:
    mid = model_id.lower()
    if ":" in mid and mid.split(":", 1)[0] in LOCAL_PROVIDERS:
        return True
    tail = mid.split(":", 1)[1] if ":" in mid and "/" not in mid.split(":", 1)[0] else mid
    if any(re.search(p, tail) for p in CLOSED_FAMILIES):
        return False
    if any(re.search(p, tail) for p in OPEN_FAMILIES):
        return True
    return vendor_of(model_id) in OPEN_VENDORS


def _price(m: dict) -> tuple[float | None, float | None]:
    p = m.get("pricing") or {}
    i, o = p.get("input_price_per_million"), p.get("output_price_per_million")
    return (float(i) if isinstance(i, (int, float)) else None, float(o) if isinstance(o, (int, float)) else None)


def describe(models: list[dict]) -> list[dict]:
    """Compact catalog rows for the app: id, vendor, type, prices, JSON/tool support, open-weight flag."""
    out = []
    for m in models:
        mid = m.get("id")
        if not mid:
            continue
        typ = m.get("type")
        if typ not in (None, "chat"):
            continue
        i, o = _price(m)
        sp = m.get("supported_parameters")
        out.append({
            "id": mid, "vendor": vendor_of(mid) or (m.get("owned_by") or ""), "open_weight": is_open_weight(mid),
            "drop_in": not NOT_DROP_IN.search(mid.lower()),
            "input_per_m": i, "output_per_m": o,
            "json": None if sp is None else ("response_format" in sp),
            "tools": None if sp is None else ("tools" in sp),
            "context": m.get("context_window"),
        })
    return out


def needs_of(scan: dict | None) -> set[str]:
    """What the repo's call sites need from a model: {'json', 'tools'} from the scan flags."""
    out: set[str] = set()
    for site in (scan or {}).get("sites", []):
        out |= {f for f in site.get("flags", []) if f in {"json", "tools"}}
    return out


def suggest(rows: list[dict], n: int = 3, judge_max_out: float = 3.5, needs: set[str] | None = None) -> dict:
    """Pick up to n open-weight candidates (different vendors first; cheapest, middle and strongest price points;
    JSON support preferred) and a judge: the strongest priced open-weight model up to `judge_max_out` $/1M output
    tokens, from a vendor no candidate uses. Reasoning-only, safety, vision, audio and code models are left out
    because they are not drop-in replacements for a chat call."""
    usable = [r for r in rows if not NOT_DROP_IN.search(r["id"].lower())]
    judges = [r for r in usable if "judge" in r["id"].lower()]
    usable = [r for r in usable if r not in judges]
    needs = needs or set()
    priced_open = [r for r in usable if r["open_weight"] and r["output_per_m"] is not None and r["json"] is not False
                   and not ("tools" in needs and r["tools"] is False)]
    priced_open.sort(key=lambda r: (r["json"] is not True, "tools" in needs and r["tools"] is not True,
                                    (r["input_per_m"] or 0) + (r["output_per_m"] or 0)))
    picks: list[str] = []
    vendors: set[str] = set()
    pool = [r for r in priced_open if 0 < (r["output_per_m"] or 0) <= judge_max_out] or priced_open
    # models the catalog confirms support what the repo needs win over models whose support is unknown
    known = [r for r in pool if r["json"] is True and ("tools" not in needs or r["tools"] is True)]
    if len(known) >= n:
        pool = known
    if pool:
        thirds = [pool[0], pool[len(pool) // 2], pool[-1]] if len(pool) >= 3 else pool
        for r in thirds + pool:  # different vendors first
            if len(picks) >= n:
                break
            if r["id"] not in picks and r["vendor"] not in vendors:
                picks.append(r["id"])
                vendors.add(r["vendor"])
        for r in thirds + pool:  # then fill the remaining slots
            if len(picks) >= n:
                break
            if r["id"] not in picks:
                picks.append(r["id"])
    in_range = [r for r in priced_open if r["id"] not in picks and (r["output_per_m"] or 0) <= judge_max_out]
    judge_pool = [r for r in in_range if r["vendor"] not in vendors] or in_range or \
        [r for r in usable if r["output_per_m"] is not None and r["id"] not in picks]
    judge_pool = judges or judge_pool  # a model built to judge wins
    judge = max(judge_pool, key=lambda r: r["output_per_m"] or 0)["id"] if judge_pool else None
    return {"candidates": picks, "judge": judge}
