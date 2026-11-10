import os

from otari_switch.config import load_dotenv


def test_dotenv_reads_only_otari_vars_and_never_overrides(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('# comment\nexport OTARI_API_KEY="otk_from_file"\nOPENAI_API_KEY=sk-x\n'
                                   "OTARI_BASE_URL=http://localhost:8000/api/v1\n", encoding="utf-8")
    monkeypatch.delenv("OTARI_API_KEY", raising=False)
    monkeypatch.setenv("OTARI_BASE_URL", "https://already.set/api/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert load_dotenv(tmp_path) == ["OTARI_API_KEY"]
    assert os.environ["OTARI_API_KEY"] == "otk_from_file"
    assert os.environ["OTARI_BASE_URL"] == "https://already.set/api/v1"
    assert "OPENAI_API_KEY" not in os.environ
