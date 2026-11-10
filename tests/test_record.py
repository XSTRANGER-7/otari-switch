"""The recorder wraps the SDK without changing behaviour. Uses a tiny stand-in 'openai' package."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

FAKE_OPENAI = {
    "openai/__init__.py": "from openai._client import OpenAI\n",
    "openai/_client.py": textwrap.dedent("""
        from openai.resources.chat.completions import Completions
        class _Chat:
            def __init__(self):
                self.completions = Completions()
        class OpenAI:
            def __init__(self, **kw):
                self.chat = _Chat()
    """),
    "openai/resources/__init__.py": "",
    "openai/resources/chat/__init__.py": "",
    "openai/resources/chat/completions.py": textwrap.dedent("""
        class _Msg:
            def __init__(self, c): self.content = c
        class _Choice:
            def __init__(self, c): self.message = _Msg(c)
        class _Resp:
            def __init__(self, c): self.choices = [_Choice(c)]
            def model_dump(self): return {"choices": [{"message": {"content": self.choices[0].message.content}}]}
        class Completions:
            def create(self, **kw):
                return _Resp("echo: " + kw["messages"][-1]["content"])
    """),
}


def test_records_through_pytest_without_changing_results(tmp_path):
    for rel, src in FAKE_OPENAI.items():
        p = tmp_path / "vendor" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    (tmp_path / "app.py").write_text(textwrap.dedent("""
        from openai import OpenAI
        client = OpenAI()
        def ask(q):
            return client.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": q}])
    """))
    (tmp_path / "test_app.py").write_text(textwrap.dedent("""
        from app import ask
        def test_ask():
            for q in ["one", "two", "two"]:
                assert ask(q).choices[0].message.content == "echo: " + q
    """))
    env_path = f"{tmp_path / 'vendor'}"
    code = textwrap.dedent(f"""
        import sys, json
        sys.path[:0] = [{str(SRC)!r}, {env_path!r}]
        from otari_switch.config import load
        from otari_switch import pipeline as pl
        import os
        os.environ["PYTHONPATH"] = {env_path!r} + os.pathsep + {str(tmp_path)!r}
        cfg = load({str(tmp_path)!r})
        res = pl.do_record(cfg, "tests", "pytest -q -p no:cacheprovider test_app.py")
        print(json.dumps(res["counts"]))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, timeout=120)
    assert out.returncode == 0, out.stderr
    counts = json.loads(out.stdout.strip().splitlines()[-1])
    assert counts == {"app.py:ask:0": 2}  # 'two' twice is deduplicated
    rec = json.loads((tmp_path / ".otari-switch" / "recordings.jsonl").read_text().splitlines()[0])
    assert rec["request"]["model"] == "gpt-4.1" and rec["response_text"].startswith("echo: ")
