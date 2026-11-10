from otari_switch import catalog as c


def row(mid, inp, out, json=True, typ="chat"):
    sp = ["max_tokens", "tools"] + (["response_format"] if json else [])
    return {"id": mid, "type": typ, "supported_parameters": sp,
            "pricing": {"input_price_per_million": inp, "output_price_per_million": out}}


def test_open_weight_detection():
    assert c.is_open_weight("meta-llama/llama-3.3-70b-instruct")
    assert c.is_open_weight("openai/gpt-oss-120b") and not c.is_open_weight("openai/gpt-4.1")
    assert c.is_open_weight("google/gemma-3-27b-it") and not c.is_open_weight("google/gemini-2.5-pro")
    assert c.is_open_weight("mistralai/mistral-small-3.2-24b-instruct") and not c.is_open_weight("mistralai/mistral-large")
    assert c.is_open_weight("llamacpp:qwen2.5-1.5b") and c.is_open_weight("groq:meta-llama/llama-4-scout")
    assert not c.is_open_weight("anthropic/claude-sonnet-4-6")


def test_describe_skips_non_chat_and_reads_prices():
    rows = c.describe([row("qwen/qwen3-235b-a22b", 0.2, 0.8), row("openai/text-embedding-3-small", 0.02, 0, typ="embedding")])
    assert [r["id"] for r in rows] == ["qwen/qwen3-235b-a22b"]
    assert rows[0]["open_weight"] and rows[0]["json"] and rows[0]["input_per_m"] == 0.2


def test_suggest_three_open_candidates_and_a_judge():
    rows = c.describe([
        row("openai/gpt-4.1", 2, 8), row("meta-llama/llama-3.3-70b-instruct", 0.13, 0.4),
        row("qwen/qwen3-235b-a22b", 0.22, 0.88), row("deepseek/deepseek-v3.2", 0.27, 1.1),
        row("openai/gpt-oss-20b", 0.03, 0.15, json=False), row("mistralai/mistral-small-3.2-24b-instruct", 0.05, 0.1),
    ])
    s = c.suggest(rows)
    assert len(s["candidates"]) == 3 and "openai/gpt-4.1" not in s["candidates"]
    assert all(c.is_open_weight(m) for m in s["candidates"])
    assert len({c.vendor_of(m) for m in s["candidates"]}) == 3
    assert s["judge"] and s["judge"] not in s["candidates"]


def test_suggest_fills_slots_with_one_vendor():
    rows = c.describe([row(f"qwen/q{i}", 0.1 * i, 0.2 * i) for i in range(1, 5)])
    assert len(c.suggest(rows)["candidates"]) == 3
