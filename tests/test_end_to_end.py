"""Full flow on the bundled demo repo against the fake Otari gateway: scan, evaluate, apply, commits."""

import json
import subprocess
import sys
import textwrap
import urllib.request

import pytest

from otari_switch import pipeline as pl
from otari_switch.config import load
from otari_switch.demo import setup_demo


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root, url = setup_demo(tmp_path_factory.mktemp("e2e") / "acme")
    return root, url


def test_demo_run_produces_all_three_verdicts(demo):
    root, _ = demo
    cfg = load(root)
    events = []
    run = pl.do_eval(cfg, emit=events.append)
    verdicts = {s["site"]["function"]: s["verdict"] for s in run["sites"]}
    assert verdicts["draft_reply"] == "keep"
    assert verdicts["extract_fields"] == "switch_with_fallback"
    assert verdicts["summarize_ticket"] == "switch"
    assert run["summary"]["moved"] == 4 and run["spend_usd"] <= run["budget_usd"]
    ef = next(s for s in run["sites"] if s["site"]["function"] == "extract_fields")
    gpt_oss = next(c for c in ef["candidates"] if c["model"] == "demo/gpt-oss-20b")
    assert gpt_oss["rejected_params"] == ["response_format"] and ef["chosen"] != "demo/gpt-oss-20b"
    assert any(e["type"] == "verdict" for e in events) and events[-1]["type"] == "done"
    assert (cfg.work_dir / "report.html").exists() and (cfg.work_dir / "report.md").exists()
    # every scored sample links to an Otari request id
    assert all(s["request_id"] for site in run["sites"] for c in site["candidates"] for s in c["samples"])


def test_budget_cap_is_respected(demo):
    root, _ = demo
    cfg = load(root)
    cfg.data["eval"]["budget_usd"] = 0.002
    run = pl.do_eval(cfg)
    assert run["spend_usd"] <= 0.002 + 0.0005
    assert any("budget" in f for f in run["findings"])


def test_apply_creates_branch_with_one_commit_per_site(demo):
    root, _ = demo
    cfg = load(root)
    run = pl.do_eval(cfg)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "config", "--allow-empty"], cwd=root, check=True)
    pl.apply_policies(cfg, run)
    out = pl.apply_code(cfg, run, use_git=True)
    assert out["branch"].startswith("otari-switch/")
    assert out["commits"][0] == "Point AI clients at Otari"
    assert sum(1 for c in out["commits"] if c.startswith("Route ")) == 5
    src = (root / "helpdesk" / "tickets.py").read_text()
    compile(src, "tickets.py", "exec")
    assert "OTARI_POLICY_SUMMARIZE_TICKET" in src and "_otari_guard.checked" in src
    # scanning the rewritten repo finds the same sites, now on policies
    sites = pl.do_scan(cfg)["sites"]
    assert all(s["model_env"].startswith("OTARI_POLICY_") for s in sites)


def test_server_requires_token_and_localhost(demo, tmp_path):
    root, _ = demo
    code = textwrap.dedent(f"""
        import sys, threading, json, urllib.request
        sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src')!r})
        from http.server import ThreadingHTTPServer
        from otari_switch.server import App, make_handler
        from pathlib import Path
        app = App(Path({str(root)!r}))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{{srv.server_address[1]}}"
        def get(path, headers=None):
            req = urllib.request.Request(base + path, headers=headers or {{}})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        s1, _ = get("/api/state")
        s2, body = get("/api/state", {{"X-Switch-Token": app.token}})
        s3, _ = get("/api/state", {{"X-Switch-Token": app.token, "Host": "evil.example"}})
        s4, html = get("/")
        print(json.dumps([s1, s2, s3, s4, json.loads(body)["scan"] is not None, app.token in html.decode()]))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [401, 200, 403, 200, True, True]


def test_insights_reads_budget_and_usage_by_role_from_otari(demo):
    from otari_switch.server import App
    root, _ = demo
    pl.do_eval(load(root))
    status, out = App(root).insights({"days": ["7"]}, {})
    assert status == 200 and out["source"] in {"demo", "otari"} and not out["errors"]
    assert out["budget"]["budgets_enforced"] is True and out["budget"]["limits"][0]["scope"] == "user"
    roles = {row["key"] for row in out["usage"]["by_role"]}
    assert {"candidate", "judge", "frontier"} <= roles
    assert out["usage"]["totals"]["request_count"] > 0 and out["usage"]["series"]


def test_tests_gate_marks_chosen_models(demo):
    import importlib.util
    if importlib.util.find_spec("openai") is None:
        pytest.skip("the demo app's tests need the openai package")
    root, _ = demo
    run = pl.do_eval(load(root))
    assert run["summary"]["tests"] == "passed"
    for s in run["sites"]:
        for c in s["candidates"]:
            if c["model"] == s["chosen"]:
                assert c["tests_passed"] is True


def test_record_through_otari_maps_today_models_to_catalog_ids(tmp_path_factory):
    import importlib.util
    if importlib.util.find_spec("openai") is None:
        pytest.skip("the demo app's tests need the openai package")
    root, _ = setup_demo(tmp_path_factory.mktemp("via") / "acme")
    cfg = load(root)
    res = pl.do_record(cfg, "tests", None, fresh=True, via_otari=True)
    assert res["via_otari"] and set(res["models"].values()) == {"openai/gpt-4.1", "openai/gpt-4.1-mini"}
    assert sum(res["counts"].values()) >= 20
    recs = [json.loads(line) for line in (cfg.work_dir / "recordings.jsonl").read_text().splitlines()]
    assert all(r["model"].startswith("openai/") and "extra_headers" not in r["request"] for r in recs)


def test_record_as_one_model_saves_today_models(tmp_path_factory):
    import importlib.util
    if importlib.util.find_spec("openai") is None:
        pytest.skip("the demo app's tests need the openai package")
    root, _ = setup_demo(tmp_path_factory.mktemp("as") / "acme")
    res = pl.do_record(load(root), "tests", None, fresh=True, as_model="demo/qwen3-235b")
    assert set(res["models"].values()) == {"demo/qwen3-235b"}
    assert load(root).data["eval"]["today_models"]["gpt-4.1"] == "demo/qwen3-235b"
