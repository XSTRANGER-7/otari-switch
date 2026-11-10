"""`otari-switch demo`: a sample repo plus a local demo Otari gateway, so anyone can try the full flow."""

from __future__ import annotations

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from otari_switch import fake_otari
from otari_switch import pipeline as pl
from otari_switch.config import load
from otari_switch.demo.data import TICKETS

TEMPLATE = Path(__file__).parent / "acme_helpdesk"


def _force_remove(func, path, _exc):
    """Git marks its object files read-only; Windows refuses to delete them until that is cleared."""
    os.chmod(path, stat.S_IWRITE)
    func(path)
MARKER = ".otari-switch-demo"
DEMO_KEY_ENV = "OTARI_SWITCH_DEMO_KEY"


def _constants(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def build_recordings(sites: list[dict], root: Path) -> list[dict]:
    k = _constants(root / "helpdesk" / "tickets.py")
    by_func = {s["function"]: s["id"] for s in sites}
    recs = []

    def add(func, model, request, text="", tool=None):
        recs.append({"site_id": by_func[func], "provider": "openai", "endpoint": "chat",
                     "request": {"model": model, **request}, "response_text": text,
                     "response_tool_call": tool, "latency_ms": 900.0, "model": model, "masked": 0,
                     "hash": f"demo-{func}-{len(recs)}"})

    for t in TICKETS:
        ticket = t["text"]
        add("summarize_ticket", "gpt-4.1", {"messages": [
            {"role": "system", "content": k["SUMMARY_PROMPT"]}, {"role": "user", "content": ticket}],
            "temperature": 0.2}, t["summary"])
        add("classify_priority", "gpt-4.1-mini", {"messages": [
            {"role": "system", "content": k["PRIORITY_PROMPT"]}, {"role": "user", "content": ticket}],
            "temperature": 0}, t["priority"])
        add("extract_fields", "gpt-4.1", {"messages": [
            {"role": "system", "content": k["FIELDS_PROMPT"]}, {"role": "user", "content": ticket}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "ticket_fields", "schema": k["FIELDS_SCHEMA"], "strict": True}}},
            json.dumps(t["fields"]))
        add("draft_reply", "gpt-4.1", {"messages": [
            {"role": "system", "content": k["REPLY_PROMPT"]},
            {"role": "user", "content": f"Ticket:\n{ticket}\n\nAgent summary: {t['summary']}"}],
            "temperature": 0.5}, t["reply"])
        add("choose_action", "gpt-4.1", {"messages": [
            {"role": "system", "content": k["ACTION_PROMPT"]}, {"role": "user", "content": ticket}],
            "tools": k["TOOLS"], "tool_choice": "required"}, "", t["action"])
    return recs


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)


def setup_demo(target: Path, port: int = 0) -> tuple[Path, str]:
    if target.exists() and any(target.iterdir()):
        if not (target / MARKER).exists():
            raise SystemExit(f"{target} exists and is not an Otari Switch demo folder. Pick another folder.")
        shutil.rmtree(target, onexc=_force_remove) if sys.version_info >= (3, 12) else \
            shutil.rmtree(target, onerror=lambda f, p, e: _force_remove(f, p, e))
    shutil.copytree(TEMPLATE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (target / MARKER).write_text("This folder was created by `otari-switch demo` and can be deleted.\n")
    (target / "tests" / "__init__.py").write_text("")
    (target / "conftest.py").write_text("# makes the repo root importable for the tests\n")
    tickets = [t["text"] for t in TICKETS]
    (target / "tests" / "sample_tickets.py").write_text(
        '"""Sample support tickets used by the tests."""\n\nTICKETS = ' + json.dumps(tickets, indent=4) + "\n")
    (target / ".gitignore").write_text("__pycache__/\n.otari-switch/\n.env\n")

    _git(target, "init", "-q", "-b", "main")
    _git(target, "config", "user.email", "demo@otari-switch.local")
    _git(target, "config", "user.name", "Otari Switch Demo")
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "Acme Helpdesk: five AI features on frontier models")

    cfg = load(target)
    # the demo gateway plays the operator too, so `apply` creates real policy objects there
    cfg.data["otari"].update({"api_key_env": DEMO_KEY_ENV, "policies_api": True, "master_key_env": DEMO_KEY_ENV})
    cfg.data["eval"].update({
        "candidates": ["demo/llama-3.3-70b", "demo/qwen3-235b", "demo/gpt-oss-20b"],
        "judge_model": "demo/judge", "summary_model": "demo/judge", "budget_usd": 1.0,
        "monthly_calls": 200000, "recordings_per_site": 20,
    })
    os.environ[DEMO_KEY_ENV] = "demo-key"
    scan = pl.do_scan(cfg)
    recs = build_recordings(scan["sites"], target)
    (cfg.work_dir / "recordings.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    url, _server = fake_otari.start(recs, port)
    cfg.data["otari"]["base_url"] = url
    cfg.save()
    _git(target, "add", "otari-switch.yml", ".gitignore")
    _git(target, "commit", "-q", "-m", "Add Otari Switch config")
    os.environ["OTARI_SWITCH_DEMO"] = "1"
    return target, url
