"""Score a candidate answer against the frontier answer.

Deterministic checks first (exact and cheap), then an LLM judge through Otari for free text.
"""

from __future__ import annotations

import json
import random
import re
import string
from typing import Any

from otari_switch.otari_client import BudgetReached, OtariClient, OtariError, answer_from
from otari_switch.privacy import prompt_text

try:
    import jsonschema  # type: ignore
except ImportError:  # optional
    jsonschema = None

JUDGE_SYSTEM = (
    "You are a strict, fair evaluator of AI answers for a software team deciding whether a cheaper model "
    "can replace their current one. You compare a CANDIDATE answer with a REFERENCE answer to the same task. "
    "Judge correctness, completeness, following the instructions, and format. Do not reward length. "
    "Different wording is fine if the meaning and usefulness match. "
    "Reply with JSON only: {\"score\": <integer 1-5>, \"reason\": \"<one short sentence>\"}. "
    "5 = as good as or better than the reference; 4 = minor differences a user would accept; "
    "3 = usable but noticeably weaker; 2 = significant problems; 1 = wrong or unusable."
)


def parse_json_text(text: str) -> Any:
    t = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1)
    return json.loads(t)


def looks_like_json(text: str) -> bool:
    try:
        v = parse_json_text(text)
        return isinstance(v, (dict, list))
    except (ValueError, TypeError):
        return False


def request_schema(request: dict) -> dict | None:
    rf = request.get("response_format")
    if isinstance(rf, dict) and rf.get("type") == "json_schema":
        js = rf.get("json_schema") or {}
        return js.get("schema") if isinstance(js, dict) else None
    fmt = ((request.get("text") or {}).get("format") or {}) if isinstance(request.get("text"), dict) else {}
    if fmt.get("type") == "json_schema":
        return fmt.get("schema")
    return None


def tool_schema(request: dict, name: str | None) -> dict | None:
    for t in request.get("tools") or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        if fn.get("name") == name:
            return fn.get("parameters") or fn.get("input_schema")
    return None


def validate_schema(value: Any, schema: dict) -> tuple[bool, str]:
    if jsonschema is not None:
        try:
            jsonschema.validate(value, schema)
            return True, ""
        except jsonschema.ValidationError as e:  # type: ignore[attr-defined]
            return False, e.message[:160]
        except jsonschema.SchemaError:  # type: ignore[attr-defined]
            return True, "schema itself is invalid; skipped"
    # minimal fallback: required keys and top-level types
    if schema.get("type") == "object":
        if not isinstance(value, dict):
            return False, "expected an object"
        missing = [k for k in schema.get("required", []) if k not in value]
        if missing:
            return False, f"missing keys: {', '.join(missing)}"
    return True, ""


def _norm_label(s: str) -> str:
    return s.strip().strip(string.punctuation + " \n\t\"'`").lower()


def is_label(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and "\n" not in t and len(t.split()) <= 4 and len(t) <= 40 and not looks_like_json(t)


def deterministic(request: dict, flags: list[str], frontier_text: str, frontier_tool: dict | None,
                  cand_text: str, cand_tool: dict | None) -> tuple[dict, float | None, str]:
    """Return (checks, forced_score or None, reason). checks: name -> True/False."""
    checks: dict[str, bool] = {}
    notes: list[str] = []
    needs_json = "json" in flags or looks_like_json(frontier_text)
    if frontier_tool:
        ok_name = bool(cand_tool) and cand_tool.get("name") == frontier_tool.get("name")
        checks["tool_name"] = ok_name
        if ok_name:
            sch = tool_schema(request, cand_tool.get("name"))
            if sch:
                ok, msg = validate_schema(cand_tool.get("arguments"), sch)
                checks["tool_args_schema"] = ok
                if not ok:
                    notes.append(f"tool arguments: {msg}")
            fa, ca = frontier_tool.get("arguments") or {}, cand_tool.get("arguments") or {}
            if isinstance(fa, dict) and isinstance(ca, dict):
                if fa == ca:
                    return checks, 5.0, "Same tool and same arguments."
                if set(fa) == set(ca):
                    same = sum(1 for k in fa if str(fa[k]).strip().lower() == str(ca[k]).strip().lower())
                    score = 3.0 + 2.0 * same / max(len(fa), 1)
                    return checks, round(score, 2), f"Same tool; {same} of {len(fa)} arguments match."
                return checks, 2.0, "Same tool, different argument keys."
        else:
            got = (cand_tool or {}).get("name") or "no tool call"
            return checks, 1.0, f"Called {got} instead of {frontier_tool.get('name')}."
    if needs_json:
        try:
            value = parse_json_text(cand_text)
            checks["valid_json"] = True
        except (ValueError, TypeError):
            checks["valid_json"] = False
            return checks, 1.0, "Answer is not valid JSON."
        sch = request_schema(request)
        if sch:
            ok, msg = validate_schema(value, sch)
            checks["schema"] = ok
            if not ok:
                return checks, 1.5, f"JSON does not match the schema: {msg}"
        try:
            fv = parse_json_text(frontier_text)
            if fv == value:
                return checks, 5.0, "Identical JSON."
            if isinstance(fv, dict) and isinstance(value, dict) and set(fv) == set(value):
                same = sum(1 for k in fv if fv[k] == value[k])
                if same == len(fv):
                    return checks, 5.0, "Identical JSON."
                notes.append(f"{same} of {len(fv)} fields identical")
        except (ValueError, TypeError):
            pass
    if is_label(frontier_text) and not needs_json:
        ok = _norm_label(cand_text) == _norm_label(frontier_text)
        checks["label_match"] = ok
        return checks, (5.0 if ok else 1.0), ("Same label." if ok else
                                             f"Said \"{cand_text.strip()[:40]}\" instead of \"{frontier_text.strip()}\".")
    return checks, None, "; ".join(notes)


THINK = re.compile(r"<think>.*?</think>", re.S)


def _parse_judge(text: str) -> tuple[float | None, str]:
    text = THINK.sub("", text or "").strip()
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            s = float(d.get("score"))
            if 1 <= s <= 5:
                return s, str(d.get("reason", ""))[:240]
        except (ValueError, TypeError, AttributeError):
            pass
    m = re.search(r'"score"\s*:\s*"?([1-5])(?:\.0)?\b', text)  # a reply cut off after its score
    if m:
        reason = re.search(r'"reason"\s*:\s*"([^"]*)', text)
        return float(m.group(1)), ((reason.group(1) + "...") if reason else "Judge reply was cut off after the score.")[:240]
    m = re.search(r"\b([1-5])\b", text or "") if len(text) <= 40 else None  # a bare score, never a digit in prose
    if m:
        return float(m.group(1)), (text or "").strip()[:240]
    return None, "Judge reply could not be read."


def _judge_call(client: OtariClient, req: dict, model: str):
    """One judge call that copes with reasoning models.

    A model that reasons first can spend its whole max_tokens thinking and return empty content with
    finish_reason "length" (seen live on otari.ai). So: ask for low reasoning effort when the catalog says the
    model reasons (dropped if the provider refuses it), and when the answer still came back empty because thinking
    used the budget, retry once with room to finish."""
    try:
        r = client.chat(req, model, role="judge")
    except BudgetReached:
        raise
    except OtariError as e:
        if "reasoning_effort" not in req or e.status != 400:
            raise
        req = {k: v for k, v in req.items() if k != "reasoning_effort"}
        r = client.chat(req, model, role="judge")
    text, _ = answer_from("chat", r)
    choice = ((r.body or {}).get("choices") or [{}])[0] if isinstance(r.body, dict) else {}
    if choice.get("finish_reason") == "length" and _parse_judge(text)[0] is None:
        r = client.chat({**req, "max_tokens": 2000}, model, role="judge")
    return r


def llm_judge(client: OtariClient, judge_model: str, request: dict, frontier_text: str,
              cand_text: str, rng: random.Random, reasoning: bool = False) -> tuple[float | None, str, str | None]:
    task = prompt_text(request)
    if len(task) > 6000:
        task = task[:3000] + "\n...\n" + task[-3000:]
    ref, cand = frontier_text[:6000], cand_text[:6000]
    blocks = [("REFERENCE answer", ref), ("CANDIDATE answer", cand)]
    rng.shuffle(blocks)  # position bias control
    body = "\n\n".join(f"### {name}\n{txt}" for name, txt in blocks)
    req = {
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"### TASK\n{task}\n\n{body}\n\nScore the CANDIDATE answer."},
        ],
        "temperature": 0,
        "max_tokens": 400,
    }
    if reasoning:
        req["reasoning_effort"] = "low"
    r = _judge_call(client, req, judge_model)
    text, _ = answer_from("chat", r)
    score, reason = _parse_judge(text)
    return score, reason, r.request_id
