import textwrap

from otari_switch.scan import scan


def write(root, rel, src):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def test_finds_sync_async_aliases_and_models(tmp_path):
    write(tmp_path, "app/llm.py", """
        import os
        from openai import OpenAI as OAI, AsyncOpenAI
        import anthropic

        MODEL = "gpt-4.1"
        client = OAI()
        aclient = AsyncOpenAI(base_url="https://example.com")
        claude = anthropic.Anthropic()

        def a(msgs):
            return client.chat.completions.create(model=MODEL, messages=msgs, response_format={"type": "json_object"})

        async def b(msgs):
            return await aclient.chat.completions.create(model=os.getenv("MY_MODEL", "gpt-4o"), messages=msgs, stream=True)

        class Bot:
            def c(self, m):
                return claude.messages.create(model="claude-sonnet-4", max_tokens=10, messages=m, tools=[])

        def d(text):
            return client.embeddings.create(model="text-embedding-3-small", input=text)

        def e(m, name):
            return client.responses.create(model=name, input=m)
    """)
    write(tmp_path, "app/other.py", """
        from app.llm import client
        def f(m):
            return client.chat.completions.create(model="gpt-4.1-mini", messages=m)
    """)
    write(tmp_path, ".venv/lib/x.py", "client.chat.completions.create(model='x')\n")
    res = scan(tmp_path)
    by_func = {s.function: s for s in res.sites}
    assert set(by_func) == {"a", "b", "Bot.c", "d", "e", "f"}
    assert by_func["a"].model == "gpt-4.1" and by_func["a"].model_source == "constant"
    assert by_func["a"].flags == ["json"]
    assert by_func["b"].model == "gpt-4o" and by_func["b"].model_env == "MY_MODEL" and "stream" in by_func["b"].flags
    assert by_func["Bot.c"].provider == "anthropic" and by_func["Bot.c"].endpoint == "messages"
    assert "tools" in by_func["Bot.c"].flags
    assert by_func["d"].evaluable is False
    assert by_func["e"].model_source == "dynamic"
    assert by_func["f"].model == "gpt-4.1-mini"
    assert {c.cls for c in res.clients} == {"OpenAI", "AsyncOpenAI", "Anthropic"}
    assert any(c.has_base_url for c in res.clients)


def test_ignores_unrelated_create_calls(tmp_path):
    write(tmp_path, "x.py", """
        class Repo:
            def save(self):
                return self.db.chat.completions.create(model="no")
    """)
    assert scan(tmp_path).sites == []


def test_ids_are_stable_per_function(tmp_path):
    write(tmp_path, "m.py", """
        from openai import OpenAI
        client = OpenAI()
        def g(m):
            client.chat.completions.create(model="a", messages=m)
            return client.chat.completions.create(model="b", messages=m)
    """)
    ids = [s.id for s in scan(tmp_path).sites]
    assert ids == ["m.py:g:0", "m.py:g:1"]


def test_finds_calls_already_wrapped_by_the_json_guard(tmp_path):
    write(tmp_path, "app/x.py", """
        import os
        from openai import OpenAI
        from app import _otari_guard

        client = OpenAI()

        def extract(msgs):
            return _otari_guard.checked(client.chat.completions.create,
                                        model=os.environ.get("OTARI_POLICY_EXTRACT", "switch-extract"),
                                        messages=msgs, response_format={"type": "json_object"},
                                        _otari_fallback="openai/gpt-4.1")
    """)
    res = scan(tmp_path)
    assert [s.function for s in res.sites] == ["extract"]
    s = res.sites[0]
    assert s.model_env == "OTARI_POLICY_EXTRACT" and s.model == "switch-extract" and "json" in s.flags
