"""Local web server for the app (standard library only). Binds to 127.0.0.1 and needs a per-session token."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from otari_switch import __version__, catalog
from otari_switch import pipeline as pl
from otari_switch import report as rep
from otari_switch.config import REGIONS, load
from otari_switch.otari_client import BudgetReached, OtariError, Unreachable

WEB = Path(__file__).parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
         ".woff2": "font/woff2", ".woff": "font/woff", ".mp4": "video/mp4", ".webm": "video/webm",
         ".json": "application/json", ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon",
         ".vtt": "text/vtt; charset=utf-8"}


class Job:
    def __init__(self):
        self.id = secrets.token_hex(6)
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.done = False
        self.cancel = threading.Event()

    def emit(self, e: dict) -> None:
        with self.cond:
            e = {**e, "t": round(time.time(), 3)}
            self.events.append(e)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()


class App:
    def __init__(self, root: Path):
        self.root = root
        self.token = secrets.token_urlsafe(18)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def cfg(self):
        return load(self.root)

    # --- API handlers: each returns (status, body) -------------------------------------
    def state(self, _q, _b):
        cfg = self.cfg()
        scan = cfg.work_dir / "scan.json"
        latest = pl.latest_run(cfg)
        return 200, {
            "version": __version__, "root": str(self.root), "repo": self.root.name,
            "demo": os.environ.get("OTARI_SWITCH_DEMO") == "1",
            "config": {"base_url": cfg.base_url, "region": cfg.region(), "key_env": cfg.data["otari"]["api_key_env"],
                       "key_set": bool(cfg.api_key), "candidates": cfg.ev("candidates"),
                       "judge_model": cfg.ev("judge_model"), "budget_usd": cfg.ev("budget_usd"),
                       "monthly_calls": cfg.ev("monthly_calls"), "thresholds": cfg.data["thresholds"],
                       "privacy_mode": cfg.data["privacy"]["mode"], "tests_command": cfg.data["tests"]["command"],
                       "excluded": [k for k, v in (cfg.data.get("sites") or {}).items() if (v or {}).get("exclude")]},
            "scan": json.loads(scan.read_text()) if scan.exists() else None,
            "recordings": pl.recording_counts(cfg),
            "latest": {"id": latest["id"], "summary": latest["summary"], "started": latest["started"]} if latest else None,
            "branch": (cfg.work_dir / "branch.txt").read_text().strip() if (cfg.work_dir / "branch.txt").exists() else None,
        }

    def connect(self, _q, b):
        cfg = self.cfg()
        region = b.get("region") or "global"
        cfg.data["otari"]["base_url"] = REGIONS.get(region, b.get("base_url") or cfg.data["otari"]["base_url"])
        if b.get("api_key"):
            # kept in this process only; never written to disk
            os.environ[cfg.data["otari"]["api_key_env"]] = b["api_key"].strip()
        if not cfg.api_key:
            return 400, {"error": f"No key. Paste one, or set ${cfg.data['otari']['api_key_env']} and restart."}
        try:
            models = pl.client_for(cfg).models()
        except OtariError as e:
            return 400, {"error": str(e), "request_id": e.request_id}
        cfg.save()
        rows = catalog.describe(models)
        return 200, {"models": [m.get("id") for m in models], "base_url": cfg.base_url,
                     "catalog": rows, "suggested": catalog.suggest(rows, needs=self._needs(cfg))}

    def models(self, _q, _b):
        cfg = self.cfg()
        try:
            models = pl.client_for(cfg).models()
        except OtariError as e:
            return 400, {"error": str(e)}
        rows = catalog.describe(models)
        return 200, {"models": [m.get("id") for m in models], "catalog": rows,
                     "suggested": catalog.suggest(rows, needs=self._needs(cfg))}

    @staticmethod
    def _needs(cfg) -> set[str]:
        p = cfg.work_dir / "scan.json"
        try:
            return catalog.needs_of(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else set()
        except ValueError:
            return set()

    def save_config(self, _q, b):
        cfg = self.cfg()
        d = cfg.data
        if "candidates" in b:
            d["eval"]["candidates"] = [m for m in b["candidates"] if m][:6]
        for k in ("judge_model", "budget_usd", "monthly_calls"):
            if k in b:
                d["eval"][k] = b[k]
        if b.get("judge_model") and not d["eval"].get("summary_model"):
            d["eval"]["summary_model"] = b["judge_model"]
        if "thresholds" in b:
            d["thresholds"].update({k: float(v) for k, v in b["thresholds"].items()})
        if "privacy_mode" in b:
            d["privacy"]["mode"] = b["privacy_mode"]
        if "tests_command" in b:
            d["tests"]["command"] = b["tests_command"]
        if "excluded" in b:
            sites = d.setdefault("sites", {}) or {}
            for sid in list(sites):
                sites[sid] = {**(sites[sid] or {}), "exclude": sid in b["excluded"]}
            for sid in b["excluded"]:
                sites.setdefault(sid, {})["exclude"] = True
            d["sites"] = sites
        cfg.save()
        return 200, {"ok": True}

    def scan(self, _q, _b):
        return 200, pl.do_scan(self.cfg())

    def record(self, _q, b):
        try:
            return 200, pl.do_record(self.cfg(), b.get("source", "tests"), b.get("arg") or None,
                                     fresh=bool(b.get("fresh")), via_otari=bool(b.get("via_otari")),
                                     as_model=b.get("as_model") or None)
        except (ValueError, FileNotFoundError) as e:
            return 400, {"error": str(e)}

    def doctor(self, _q, _b):
        return 200, {"checks": pl.doctor(self.cfg())}

    def start_eval(self, _q, _b):
        cfg = self.cfg()
        problems = cfg.validate()
        if problems:
            return 400, {"error": " ".join(problems)}
        job = Job()
        with self.lock:
            self.jobs[job.id] = job

        def work():
            try:
                pl.do_eval(cfg, emit=job.emit, cancel=job.cancel)
            except BudgetReached as e:
                job.emit({"type": "error", "message": str(e), "fix": "Raise the budget in Settings."})
            except Unreachable as e:
                job.emit({"type": "error", "message": str(e), "fix": "Check your connection and region."})
            except OtariError as e:
                job.emit({"type": "error", "message": str(e), "request_id": e.request_id})
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "Cancelled":
                    job.emit({"type": "cancelled"})
                else:
                    traceback.print_exc()
                    job.emit({"type": "error", "message": str(e) or type(e).__name__})
            finally:
                job.finish()

        threading.Thread(target=work, daemon=True).start()
        return 200, {"job": job.id}

    def cancel(self, _q, b):
        job = self.jobs.get(b.get("job", ""))
        if job:
            job.cancel.set()
        return 200, {"ok": bool(job)}

    def runs(self, _q, _b):
        return 200, {"runs": pl.list_runs(self.cfg())}

    def run(self, q, _b):
        r = pl.get_run(self.cfg(), (q.get("id") or [""])[0])
        return (200, r) if r else (404, {"error": "Run not found."})

    def preview(self, q, _b):
        cfg = self.cfg()
        r = pl.get_run(cfg, (q.get("id") or [""])[0]) or pl.latest_run(cfg)
        if not r:
            return 404, {"error": "No run yet."}
        return 200, pl.preview(cfg, r)

    def apply(self, _q, b):
        cfg = self.cfg()
        r = pl.get_run(cfg, b.get("id", "")) or pl.latest_run(cfg)
        if not r:
            return 404, {"error": "No run yet."}
        pol = pl.apply_policies(cfg, r)
        try:
            out = pl.apply_code(cfg, r, use_git=True)
        except Exception as e:  # noqa: BLE001
            return 400, {"error": str(e), "policies": pol}
        (cfg.work_dir / "branch.txt").write_text(out["branch"])
        return 200, {**out, "policies": pol}

    def insights(self, q, _b):
        """Real numbers from Otari about this key: budget caps and spend (no estimates, no invented data)."""
        cfg = self.cfg()
        try:
            days = max(1, min(int((q.get("days") or ["7"])[0]), 90))
        except ValueError:
            days = 7
        demo = os.environ.get("OTARI_SWITCH_DEMO") == "1"
        out = {"source": "demo" if demo else "otari", "base_url": cfg.base_url, "days": days,
               "bucket": "hour" if days <= 2 else "day", "budget": None, "usage": None, "errors": {}}
        if not cfg.api_key:
            out["source"] = "offline"
            out["errors"]["key"] = f"No Otari key yet. Connect one, or set ${cfg.data['otari']['api_key_env']}."
            return 200, out
        client = pl.client_for(cfg)
        try:
            out["budget"] = client.key_budget()
        except OtariError as e:
            out["errors"]["budget"] = str(e)
        end = time.time()
        start = end - days * 86400
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))  # noqa: E731
        try:
            s = client.key_usage_summary(iso(start), iso(end), bucket=out["bucket"], group_by_tag="role",
                                         dimensions=["model", "provider"])
            out["usage"] = {
                "start_date": s.get("start_date"), "end_date": s.get("end_date"), "bucket": s.get("bucket"),
                "totals": s.get("totals") or {}, "series": s.get("series") or [],
                "by_model": s.get("by_model") or [], "by_provider": s.get("by_provider") or [],
                "by_role": s.get("by_tag") or [],
            }
        except OtariError as e:
            out["errors"]["usage"] = str(e)
        return 200, out

    def pr(self, _q, b):
        cfg = self.cfg()
        r = pl.get_run(cfg, b.get("id", "")) or pl.latest_run(cfg)
        bf = cfg.work_dir / "branch.txt"
        if not r or not bf.exists():
            return 400, {"error": "Create the branch first."}
        if os.environ.get("OTARI_SWITCH_DEMO") == "1":
            return 200, {"ok": True, "method": "demo", "url": None,
                         "message": "Demo mode: the branch is ready locally; no pull request was pushed."}
        return 200, pl.open_pr(cfg, r, bf.read_text().strip())


ROUTES = {
    ("GET", "/api/state"): "state", ("POST", "/api/connect"): "connect", ("GET", "/api/models"): "models",
    ("POST", "/api/config"): "save_config", ("POST", "/api/scan"): "scan", ("POST", "/api/record"): "record",
    ("GET", "/api/doctor"): "doctor", ("POST", "/api/eval"): "start_eval", ("POST", "/api/cancel"): "cancel",
    ("GET", "/api/runs"): "runs", ("GET", "/api/run"): "run", ("GET", "/api/preview"): "preview",
    ("POST", "/api/apply"): "apply", ("POST", "/api/pr"): "pr", ("GET", "/api/insights"): "insights",
}


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if os.environ.get("OTARI_SWITCH_DEBUG"):
                super().log_message(fmt, *args)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in {"127.0.0.1", "localhost"}

        def _send(self, status, body, ctype="application/json"):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                 "connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def _static(self, name: str):
            """Serve a file under web/ (sub-folders allowed, nothing outside it). Range requests for media."""
            base = WEB.resolve()
            try:
                p = (base / unquote(name)).resolve()
            except (OSError, ValueError):
                return self._send(404, {"error": "not found"})
            if base not in p.parents or not p.is_file():
                return self._send(404, {"error": "not found"})
            ctype = TYPES.get(p.suffix.lower(), "application/octet-stream")
            size = p.stat().st_size
            rng = self.headers.get("Range") or ""
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
                else:
                    start, end = max(size - int(m.group(2)), 0), size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                with p.open("rb") as f:
                    f.seek(start)
                    chunk = f.read(end - start + 1)
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(chunk)
                return
            raw = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def do_HEAD(self):
            u = urlparse(self.path)
            if self._host_ok() and u.path.startswith("/static/"):
                return self._static(u.path.removeprefix("/static/"))
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _authed(self, q) -> bool:
            tok = self.headers.get("X-Switch-Token") or (q.get("token") or [""])[0]
            return secrets.compare_digest(tok, app.token)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "Forbidden host"})
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in {"/", "/index.html"}:
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                return self._send(200, html.encode(), TYPES[".html"])
            if u.path.startswith("/static/"):
                return self._static(u.path.removeprefix("/static/"))
            if u.path.startswith("/report/"):
                if not self._authed(q):
                    return self._send(401, {"error": "bad token"})
                r = pl.get_run(app.cfg(), u.path.removeprefix("/report/"))
                if not r:
                    return self._send(404, {"error": "not found"})
                return self._send(200, rep.html_page(r).encode(), TYPES[".html"])
            if u.path == "/api/events":
                return self._events(q)
            self._api("GET", u.path, q, {})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "Forbidden host"})
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "Body must be JSON."})
            self._api("POST", u.path, parse_qs(u.query), body)

        def _api(self, method, path, q, body):
            name = ROUTES.get((method, path))
            if not name:
                return self._send(404, {"error": "not found"})
            if not self._authed(q):
                return self._send(401, {"error": "Session expired. Reload the page."})
            try:
                status, out = getattr(app, name)(q, body)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                status, out = 500, {"error": str(e) or type(e).__name__}
            self._send(status, out)

        def _events(self, q):
            if not self._authed(q):
                return self._send(401, {"error": "bad token"})
            job = app.jobs.get((q.get("job") or [""])[0])
            if not job:
                return self._send(404, {"error": "no such job"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            sent = 0
            try:
                while True:
                    with job.cond:
                        while sent >= len(job.events) and not job.done:
                            job.cond.wait(timeout=15)
                            if sent >= len(job.events) and not job.done:
                                self.wfile.write(b": ping\n\n")
                                self.wfile.flush()
                        batch = job.events[sent:]
                        finished = job.done
                    for e in batch:
                        self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                    sent += len(batch)
                    self.wfile.flush()
                    if finished and sent >= len(job.events):
                        self.wfile.write(b"event: end\ndata: {}\n\n")
                        self.wfile.flush()
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

    return H


def serve(root: Path, port: int = 4317, open_browser: bool = True) -> None:
    app = App(root)
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app))
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"No free port between {port} and {port + 19}.")
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Otari Switch is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
