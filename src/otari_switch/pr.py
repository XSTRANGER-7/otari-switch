"""Branch, commits and pull request. GitHub MCP through Otari first, then the gh CLI, then manual."""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

from otari_switch import rewrite
from otari_switch.config import Config
from otari_switch.otari_client import McpOutcomeUnknown, OtariClient, OtariError


class GitError(Exception):
    pass


def git(root: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise GitError(p.stderr.strip() or p.stdout.strip() or f"git {' '.join(args)} failed")
    return p.stdout.strip()


def is_repo(root: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
                          capture_output=True, text=True).returncode == 0


def head(root: Path) -> str | None:
    try:
        return git(root, "rev-parse", "--short", "HEAD")
    except GitError:
        return None


def owner_repo(root: Path) -> tuple[str, str] | None:
    try:
        url = git(root, "remote", "get-url", "origin")
    except GitError:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    return (m.group(1), m.group(2)) if m else None


def dirty_files(root: Path) -> list[str]:
    # not via git(): its strip() would eat the leading status column of the first line
    out = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    files = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if not path.startswith(".otari-switch") and path not in {"otari-switch.yml", ".gitignore"}:
            files.append(path)
    return files


def apply_with_commits(cfg: Config, run: dict, policies: list[dict]) -> dict:
    """Create a branch; commit client changes, then one commit per call site."""
    root = cfg.root
    if not is_repo(root):
        raise GitError("This folder is not a git repository. Run `git init` and commit your code first.")
    dirty = dirty_files(root)
    if dirty:
        raise GitError("You have uncommitted changes in: " + ", ".join(dirty[:5]) +
                       ". Commit or stash them first so the PR contains only Otari Switch changes.")
    branch = f"otari-switch/{dt.date.today().isoformat()}"
    existing = git(root, "branch", "--list", branch, check=False)
    if existing:
        branch += "-" + dt.datetime.now().strftime("%H%M%S")
    git(root, "checkout", "-b", branch)
    frontier = {s["site"]["id"]: s["frontier_model"] for s in run["sites"]}
    commits = []

    plans = rewrite.plan(root, policies, frontier, stage="clients")
    if plans:
        rewrite.write(plans)
    rewrite.env_example(root, policies)
    git(root, "add", "-A", ".")
    git(root, "reset", "-q", "--", ".otari-switch", check=False)
    if git(root, "diff", "--cached", "--name-only", check=False):
        git(root, "commit", "-q", "-m", "Point AI clients at Otari\n\nBase URL and key now come from "
            "OTARI_BASE_URL and OTARI_API_KEY.")
        commits.append("Point AI clients at Otari")

    for p in policies:
        plans = rewrite.plan(root, policies, frontier, stage="sites", only_site=p["site_id"])
        if not plans:
            continue
        rewrite.write(plans)
        git(root, "add", "-A", ".")
        git(root, "reset", "-q", "--", ".otari-switch", check=False)
        verdict = p["verdict"].replace("_", " ")
        msg = f"Route {p['site_id']} through Otari policy {p['name']} ({verdict})"
        git(root, "commit", "-q", "-m", msg)
        commits.append(msg)

    share = run["summary"]["open_share"]
    if rewrite.badge(root, share):
        git(root, "add", "README.md")
        git(root, "commit", "-q", "-m", "Add open-weight share badge")
        commits.append("Add open-weight share badge")
    return {"branch": branch, "commits": commits}


def open_pr(cfg: Config, client: OtariClient, branch: str, title: str, body: str) -> dict:
    root = cfg.root
    base = cfg.data["github"].get("base_branch") or "main"
    try:
        git(root, "push", "-u", "origin", branch)
    except GitError as e:
        return {"ok": False, "method": "none", "message": f"Could not push the branch: {e}"}
    orp = owner_repo(root)
    mcp_id = cfg.data["github"].get("mcp_server_id")
    fallback_note = ""
    if mcp_id and orp:
        res = open_pr_mcp(client, mcp_id, orp, branch, base, title, body)
        if res.get("ok") or res.get("method") == "otari-mcp-unknown":
            return res
        fallback_note = res.get("message", "") + " Used the gh CLI instead."
    if shutil.which("gh"):
        p = subprocess.run(["gh", "pr", "create", "--base", base, "--head", branch, "--title", title,
                            "--body", body], cwd=root, capture_output=True, text=True)
        if p.returncode == 0:
            return {"ok": True, "method": "gh", "url": p.stdout.strip().splitlines()[-1], "note": fallback_note}
        fallback_note += f" gh failed: {p.stderr.strip()[:200]}"
    url = f"https://github.com/{orp[0]}/{orp[1]}/compare/{base}...{branch}?expand=1" if orp else None
    return {"ok": False, "method": "manual", "url": url,
            "message": (fallback_note + " Open the link to create the PR; the body is in .otari-switch/report.md.").strip()}


PR_TOOL_NAMES = ("create_pull_request", "create_pr", "pull_request_create")


def open_pr_mcp(client: OtariClient, server_id: str, orp: tuple[str, str], branch: str, base: str,
                title: str, body: str) -> dict:
    """Open the PR with the GitHub MCP server stored in Otari (caller-orchestrated MCP).

    Discovery returns the server's tools and `server_revision`; one exact `create_pull_request` call is then sent
    to POST /mcp/execute. The GitHub token stays in Otari. The call is never retried: an `outcome_unknown` answer
    is reported as such, so a PR is never opened twice.
    """
    owner, repo = orp
    try:
        disc = client.mcp_tools(server_id)
    except OtariError as e:
        return {"ok": False, "method": "otari-mcp", "message": f"GitHub MCP server in Otari not reachable: {e}."}
    tools = {t.get("name"): t for t in disc.get("tools") or []}
    name = next((n for n in PR_TOOL_NAMES if n in tools), None)
    if not name:
        return {"ok": False, "method": "otari-mcp",
                "message": "The MCP server stored in Otari exposes no create_pull_request tool (check its allowlist)."}
    wanted = {"owner": owner, "repo": repo, "title": title, "body": body, "head": branch, "base": base}
    props = ((tools[name].get("input_schema") or {}).get("properties") or {})
    args = {k: v for k, v in wanted.items() if not props or k in props}
    try:
        res = client.mcp_execute(server_id, name, args, disc.get("server_revision") or "")
    except McpOutcomeUnknown as e:
        return {"ok": False, "method": "otari-mcp-unknown", "request_id": e.request_id,
                "message": f"{e} Look for the PR on GitHub before opening it again."}
    except OtariError as e:
        return {"ok": False, "method": "otari-mcp", "message": f"GitHub MCP through Otari failed: {e}."}
    text = " ".join(c.get("text", "") for c in res.get("content") or [] if isinstance(c, dict))
    sc = res.get("structuredContent") if isinstance(res.get("structuredContent"), dict) else {}
    url = sc.get("html_url") or sc.get("url")
    if not url:
        m = re.search(r"https://github\.com/[^\s\"']+/pull/\d+", text + " " + json.dumps(sc))
        url = m.group(0) if m else None
    if res.get("isError"):
        return {"ok": False, "method": "otari-mcp", "message": f"GitHub refused the PR: {text[:300]}"}
    return {"ok": True, "method": "otari-mcp", "url": url, "request_id": res.get("_request_id"),
            "message": "" if url else text[:300]}
