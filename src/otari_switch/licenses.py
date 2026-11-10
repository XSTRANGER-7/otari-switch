"""Check that a model really is open-weight, by reading its model card through Otari's web fetch.

Otari's catalog has no licence field, so "open-weight" was a guess from the vendor name. Here Otari Switch fetches
the model's official Hugging Face model card (`/raw/main/README.md`) with `POST /api/v1/fetch` and reads the
`license:` line of its front matter:

- open:        weights published under an OSI-style licence (Apache-2.0, MIT, BSD, CC-BY ...)
- restricted:  weights published, but under custom terms (Llama, Gemma, non-commercial, "other" ...); read them
- unknown:     no public model card found where it should be; nothing is claimed
- error:       the fetch itself failed (see `detail`)

Results are cached in `.otari-switch/licenses.json`. Every fetch is metered against the run's budget and tagged
`role=license`, so it shows up in the Trend screen.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from otari_switch.otari_client import BudgetReached, OtariClient, OtariError

# catalog vendor -> Hugging Face organisation that publishes the weights
HF_ORG = {
    "openai": "openai", "deepseek": "deepseek-ai", "deepseek-ai": "deepseek-ai", "alibaba": "Qwen", "qwen": "Qwen",
    "moonshotai": "moonshotai", "meta": "meta-llama", "meta-llama": "meta-llama", "mistralai": "mistralai",
    "google": "google", "z-ai": "zai-org", "zai-org": "zai-org", "thudm": "THUDM", "nvidia": "nvidia",
    "minimax": "MiniMaxAI", "minimaxai": "MiniMaxAI", "xiaomi": "XiaomiMiMo", "microsoft": "microsoft",
    "ibm-granite": "ibm-granite", "ibm": "ibm-granite", "allenai": "allenai", "nousresearch": "NousResearch",
    "baidu": "baidu", "tencent": "tencent", "stepfun": "stepfun-ai", "stepfun-ai": "stepfun-ai",
    "inclusionai": "inclusionAI", "swiss-ai": "swiss-ai", "bytedance": "ByteDance-Seed",
    "huggingfacetb": "HuggingFaceTB", "arcee-ai": "arcee-ai", "liquidai": "LiquidAI", "cohere": "CohereLabs",
}
# model family -> organisation, for provider selectors such as llamacpp:qwen2.5-1.5b-instruct
FAMILY_ORG = [
    (r"^qwen", "Qwen"), (r"^llama", "meta-llama"), (r"^gemma", "google"), (r"^(mistral|mixtral|ministral|devstral)",
                                                                          "mistralai"),
    (r"^phi", "microsoft"), (r"^deepseek", "deepseek-ai"), (r"^gpt-oss", "openai"), (r"^kimi", "moonshotai"),
    (r"^glm", "zai-org"), (r"^granite", "ibm-granite"), (r"^olmo", "allenai"), (r"^smollm", "HuggingFaceTB"),
]
OPEN_LICENSES = {
    "apache-2.0", "mit", "bsd", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0", "cc-by-sa-4.0", "cc0-1.0", "mpl-2.0",
    "gpl-3.0", "lgpl-3.0", "agpl-3.0", "openrail", "openrail++", "bigscience-openrail-m", "creativeml-openrail-m",
    "isc", "unlicense", "artistic-2.0", "ecl-2.0", "odc-by", "afl-3.0", "bsl-1.0", "zlib", "ms-pl",
}


def hf_repo(model_id: str) -> str | None:
    """Best guess at the Hugging Face repo that publishes this catalog model's weights."""
    mid = model_id.strip()
    if ":" in mid and "/" not in mid.split(":", 1)[0]:
        mid = mid.split(":", 1)[1]  # provider selector: keep what the provider calls it
    if "/" in mid:
        vendor, name = mid.split("/", 1)
        org = HF_ORG.get(vendor.lower())
    else:
        name, org = mid, None
    name = name.split("/")[-1]
    if not org:
        org = next((o for pat, o in FAMILY_ORG if re.search(pat, name.lower())), None)
    return f"{org}/{name}" if org and name else None


def card_url(repo: str) -> str:
    return f"https://huggingface.co/{repo}/raw/main/README.md"


def parse_license(text: str) -> tuple[str | None, str | None]:
    """(license, license_name) from a model card's YAML front matter; Hugging Face's own card format."""
    t = text or ""
    m = re.search(r"(?ms)^\s*---\s*$(.*?)^\s*---\s*$", t)
    head = m.group(1) if m else t[:3000]
    lic = re.search(r"(?mi)^\s*license\s*:\s*['\"]?([A-Za-z0-9.+_-]+)", head)
    name = re.search(r"(?mi)^\s*license_name\s*:\s*['\"]?([A-Za-z0-9.+_ -]+)", head)
    if not lic:  # a fetcher that turned the front matter into prose still keeps "license: x"
        lic = re.search(r"(?i)\blicen[sc]e\s*[:=]\s*['\"]?([A-Za-z0-9.+_-]{2,40})", t[:4000])
    return (lic.group(1).lower() if lic else None, name.group(1).strip().lower() if name else None)


def classify(lic: str | None, name: str | None) -> str:
    if not lic:
        return "unknown"
    if lic in OPEN_LICENSES or (lic == "other" and name and re.search(r"\b(mit|apache)\b", name)):
        return "open"
    return "restricted"


def check(client: OtariClient, model_id: str) -> dict:
    """Fetch one model card through Otari and classify the licence. Never raises except BudgetReached."""
    repo = hf_repo(model_id)
    out = {"model": model_id, "status": "unknown", "license": None, "source_url": None,
           "via": "otari_web_fetch", "request_id": None, "detail": "",
           "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    if not repo:
        out["detail"] = "No known Hugging Face organisation for this vendor, so there is no model card to read."
        return out
    url = card_url(repo)
    out["source_url"] = f"https://huggingface.co/{repo}"
    try:
        res = client.fetch(url, max_chars=6000)
    except BudgetReached:
        raise
    except OtariError as e:
        out["request_id"] = e.request_id
        upstream = e.headers.get("otari-error-status") or ""
        if e.status == 502 and (upstream == "404" or "404" in str(e)):
            out["detail"] = f"No public model card at {out['source_url']}."
            return out
        if e.status == 502 and upstream in {"401", "403"}:
            out["detail"] = ("The model card is gated on Hugging Face (a login is needed to read it), so the licence "
                             "could not be confirmed. Gated weights usually come with custom terms.")
            return out
        out["status"] = "error"
        out["detail"] = str(e)
        if fetch_disabled(e):
            out["detail"] = FETCH_OFF
        return out
    out["request_id"] = res.get("_request_id")
    lic, name = parse_license(res.get("text") or "")
    out["license"] = (name if lic == "other" and name else lic)
    out["status"] = classify(lic, name)
    final = res.get("final_url") or res.get("url") or url
    out["source_url"] = final.replace("/raw/main/README.md", "") if final else out["source_url"]
    out["detail"] = {"open": "Weights published under an open licence.",
                     "restricted": "Weights are published, but under custom terms: read them before production.",
                     "unknown": "The model card has no license line."}[out["status"]]
    return out


FETCH_OFF = "Web fetch is off for this workspace. Turn on Web fetch in Otari (Tools), then check again."


def fetch_disabled(e: OtariError) -> bool:
    txt = str(e).lower()
    return e.code == "web_access_disabled" or (e.status == 400 and ("fetch" in txt and ("enabled" in txt or
                                                                                         "off" in txt or "disabled" in txt)))


def check_many(client: OtariClient, models: list[str], work_dir: Path | None = None, refresh: bool = False,
               emit=lambda e: None) -> dict:
    """Check each model once (cached). Returns {available, reason, results:{model: result}}."""
    cache = load_cache(work_dir) if work_dir else {}
    results, available, reason = {}, True, None
    for m in dict.fromkeys(x for x in models if x):
        if not refresh and m in cache and cache[m].get("status") in {"open", "restricted", "unknown"}:
            results[m] = cache[m]
            emit({"type": "license", "model": m, "status": cache[m]["status"], "license": cache[m].get("license"),
                  "request_id": cache[m].get("request_id"), "cached": True})
            continue
        if not available:
            results[m] = {"model": m, "status": "error", "license": None, "source_url": None,
                          "via": "otari_web_fetch", "request_id": None, "detail": reason}
            continue
        r = check(client, m)
        if r["status"] == "error" and r["detail"] == FETCH_OFF:
            available, reason = False, FETCH_OFF
        results[m] = r
        emit({"type": "license", "model": m, "status": r["status"], "license": r["license"],
              "request_id": r["request_id"]})
    if work_dir:
        cache.update({k: v for k, v in results.items() if v.get("status") != "error"})
        (work_dir / "licenses.json").write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return {"available": available, "reason": reason, "results": results}


def load_cache(work_dir: Path) -> dict:
    p = work_dir / "licenses.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        return {}
