"""Run the user's tests or a script with recording on, or import logs."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from otari_switch.models import Recording


def _env(root: Path, limit: int, base: dict | None = None) -> dict:
    env = dict(base or os.environ)
    env["OTARI_SWITCH_RECORD"] = "1"
    env["OTARI_SWITCH_ROOT"] = str(root)
    env["OTARI_SWITCH_LIMIT"] = str(limit)
    # make sure our package is importable inside the child, even when not pip-installed
    pkg_parent = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join([pkg_parent, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return env


def run_tests(root: Path, command: str, limit: int, base_env: dict | None = None) -> subprocess.CompletedProcess:
    args = shlex.split(command)
    if args and args[0] == "pytest":
        args = [sys.executable, "-m", "pytest", "-p", "otari_switch.record.pytest_plugin"] + args[1:]
    return subprocess.run(args, cwd=root, env=_env(root, limit, base_env), capture_output=True, text=True)


def run_script(root: Path, script: str, limit: int, base_env: dict | None = None) -> subprocess.CompletedProcess:
    code = (
        "import runpy, sys; from otari_switch.record import install_from_env; install_from_env(); "
        "from otari_switch.record.override import install_from_env as o; o(); "
        f"sys.argv=[{script!r}]; runpy.run_path({script!r}, run_name='__main__')"
    )
    return subprocess.run([sys.executable, "-c", code], cwd=root, env=_env(root, limit, base_env),
                          capture_output=True, text=True)


def import_logs(root: Path, log_path: Path, sites: list[dict], limit: int) -> tuple[int, list[str]]:
    """Import JSONL lines shaped like {site_id, request, response_text} (or request/response)."""
    out = root / ".otari-switch" / "recordings.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    by_model: dict[str, list[str]] = {}
    for s in sites:
        if s.get("model"):
            by_model.setdefault(s["model"], []).append(s["id"])
    problems, count, per = [], 0, {}
    with log_path.open(encoding="utf-8") as f, out.open("a", encoding="utf-8") as w:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                problems.append(f"line {n}: not JSON")
                continue
            req = d.get("request") or {}
            site_id = d.get("site_id") or d.get("call_site")
            if not site_id:
                ids = by_model.get(req.get("model") or "", [])
                if len(ids) == 1:
                    site_id = ids[0]
                else:
                    problems.append(f"line {n}: no site_id and the model does not match exactly one call site")
                    continue
            if per.get(site_id, 0) >= limit:
                continue
            text = d.get("response_text")
            if text is None:
                from otari_switch.otari_client import extract_answer
                site = next((s for s in sites if s["id"] == site_id), {})
                text, tool = extract_answer(site.get("endpoint", "chat"), d.get("response") or {})
            else:
                tool = d.get("response_tool_call")
            site = next((s for s in sites if s["id"] == site_id), {})
            rec = Recording(site_id=site_id, provider=site.get("provider", "openai"),
                            endpoint=site.get("endpoint", "chat"), request=req, response_text=text or "",
                            response_tool_call=tool, model=req.get("model"), hash=f"import-{n}")
            from otari_switch.models import to_dict
            w.write(json.dumps(to_dict(rec)) + "\n")
            per[site_id] = per.get(site_id, 0) + 1
            count += 1
    return count, problems


def load_recordings(root: Path) -> list[Recording]:
    p = root / ".otari-switch" / "recordings.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(Recording.from_dict(json.loads(line)))
            except (ValueError, TypeError):
                continue
    return out
