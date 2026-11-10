"""MCP server (stdio) so coding agents can run a migration while the user watches.

Install the extra: pip install "mcp>=1.0"  (or: pip install -e ".[mcp]"). apply_switch defaults to a dry run; agents must ask
the user before passing dry_run=false.
"""

from __future__ import annotations

import json
from pathlib import Path


def main(root: str = ".") -> int:
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP  # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP  # mcp 1.x
        except ImportError:
            print('The MCP server needs the mcp package: pip install "mcp>=1.0"  (or: pip install -e ".[mcp]")')
            return 1

    from otari_switch import pipeline as pl
    from otari_switch.config import load

    base = Path(root).resolve()
    mcp = FastMCP("otari-switch")

    def cfg(path: str | None):
        return load(Path(path).resolve() if path else base)

    @mcp.tool()
    def scan_repo(path: str | None = None) -> str:
        """List every OpenAI/Anthropic SDK call site in the repo (code is parsed, never run)."""
        data = pl.do_scan(cfg(path))
        return json.dumps({"sites": data["sites"], "files_scanned": data["files_scanned"]})

    @mcp.tool()
    def record_prompts(path: str | None = None, source: str = "tests", arg: str | None = None) -> str:
        """Capture real prompts. source: tests | script | import. arg: test command, script path or log file."""
        return json.dumps(pl.do_record(cfg(path), source, arg))

    @mcp.tool()
    def evaluate(path: str | None = None, budget_usd: float | None = None) -> str:
        """Replay recorded prompts on open-weight candidates through Otari, judge, test, and decide per call site."""
        c = cfg(path)
        if budget_usd:
            c.data["eval"]["budget_usd"] = float(budget_usd)
        run = pl.do_eval(c)
        return json.dumps({"run_id": run["id"], "summary": run["summary"], "findings": run["findings"],
                           "verdicts": [{"site": s["site"]["id"], "verdict": s["verdict"], "chosen": s["chosen"],
                                         "reason": s["reason"]} for s in run["sites"] if s["candidates"]]})

    @mcp.tool()
    def get_report(path: str | None = None, run_id: str | None = None) -> str:
        """Return the Markdown report of a run (latest when run_id is empty)."""
        from otari_switch import report
        c = cfg(path)
        run = pl.get_run(c, run_id) if run_id else pl.latest_run(c)
        return report.markdown(run) if run else "No run yet."

    @mcp.tool()
    def apply_switch(path: str | None = None, run_id: str | None = None, dry_run: bool = True) -> str:
        """Show (dry_run=true) or apply the code changes and routing policies on a new git branch.
        Always show the dry run to the user and get their yes before calling with dry_run=false."""
        c = cfg(path)
        run = pl.get_run(c, run_id) if run_id else pl.latest_run(c)
        if not run:
            return "No run yet. Call evaluate first."
        if dry_run:
            pv = pl.preview(c, run)
            return json.dumps({"diff": pv["diff"], "policies": pv["policies"]})
        pol = pl.apply_policies(c, run)
        out = pl.apply_code(c, run, use_git=True)
        (c.work_dir / "branch.txt").write_text(out["branch"])
        return json.dumps({**out, "policies": pol})

    mcp.run()
    return 0
