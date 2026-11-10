import ast
import textwrap

from otari_switch import rewrite

SRC = textwrap.dedent('''\
    """Module doc."""
    from __future__ import annotations
    from openai import OpenAI

    client = OpenAI(api_key="sk-old")  # keep this comment

    def f(m):
        return client.chat.completions.create(
            model="gpt-4.1",
            messages=m,
            response_format={"type": "json_object"},
        )

    def g(m):
        return client.chat.completions.create(model="gpt-4.1-mini", messages=m)  # trailing
''')

POLICIES = [
    {"site_id": "app.py:f:0", "name": "switch-f", "env_var": "OTARI_POLICY_F", "verdict": "switch_with_fallback",
     "runtime_guard": True, "candidates": [{"model": "open-a"}, {"model": "gpt-4.1"}]},
    {"site_id": "app.py:g:0", "name": "switch-g", "env_var": "OTARI_POLICY_G", "verdict": "switch",
     "runtime_guard": False, "candidates": [{"model": "open-b"}]},
]


def test_rewrite_compiles_keeps_comments_and_is_idempotent(tmp_path):
    (tmp_path / "app.py").write_text(SRC)
    plans = rewrite.plan(tmp_path, POLICIES, {"app.py:f:0": "gpt-4.1", "app.py:g:0": "gpt-4.1-mini"})
    out = plans["app.py"].result()
    ast.parse(out)
    assert "# keep this comment" in out and "# trailing" in out
    assert 'os.environ.get("OTARI_POLICY_F", "switch-f")' in out
    assert 'os.environ.get("OTARI_POLICY_G", "switch-g")' in out
    assert 'api_key=os.environ.get("OTARI_API_KEY")' in out and "sk-old" not in out
    assert 'base_url=os.environ.get("OTARI_BASE_URL"' in out
    assert "_otari_guard.checked(client.chat.completions.create," in out and "_otari_fallback='gpt-4.1'" in out
    lines = out.splitlines()
    assert lines[2] == "import os"  # after the docstring and the __future__ import
    diff = rewrite.diff(plans, tmp_path)
    assert "+++ b/otari_guard.py" in diff
    rewrite.write(plans)
    assert (tmp_path / "otari_guard.py").exists()
    again = rewrite.plan(tmp_path, POLICIES, {"app.py:f:0": "gpt-4.1", "app.py:g:0": "gpt-4.1-mini"})
    assert again == {}, "a second rewrite must change nothing"


def test_guard_behaviour():
    ns = {}
    exec(rewrite.GUARD_SOURCE, ns)

    class R:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    seen = []

    def fake_create(**kw):
        seen.append(kw["model"])
        return R("not json" if kw["model"] == "open" else '{"ok": true}')

    resp = ns["checked"](fake_create, model="open", messages=[], _otari_fallback="frontier")
    assert seen == ["open", "frontier"] and resp.choices[0].message.content == '{"ok": true}'


def test_env_example_and_badge(tmp_path):
    (tmp_path / "README.md").write_text("# App\n")
    rewrite.env_example(tmp_path, POLICIES)
    rewrite.env_example(tmp_path, POLICIES)
    env = (tmp_path / ".env.example").read_text()
    assert env.count("OTARI_POLICY_F=") == 1 and "OTARI_API_KEY=" in env
    rewrite.badge(tmp_path, 0.8)
    rewrite.badge(tmp_path, 0.8)
    assert (tmp_path / "README.md").read_text().count("open--weight") == 1
