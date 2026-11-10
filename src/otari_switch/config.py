"""Load, validate and write otari-switch.yml."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - pyyaml is a declared dependency
    yaml = None

CONFIG_NAME = "otari-switch.yml"
WORK_DIR = ".otari-switch"

REGIONS = {
    "global": "https://api.otari.ai/api/v1",
    "eu": "https://eu.api.otari.ai/api/v1",
}

DEFAULTS: dict[str, Any] = {
    "otari": {
        "base_url": REGIONS["global"],
        "api_key_env": "OTARI_API_KEY",
        "timeout_s": 120,
        # Routing policies through the API need operator authority (a self-hosted gateway's master key).
        # On otari.ai leave this false: apply writes exact dashboard steps instead.
        "policies_api": False,
        "master_key_env": "OTARI_MASTER_KEY",
        "workspace_id": "",
    },
    "eval": {
        "budget_usd": 2.0,
        "recordings_per_site": 20,
        "concurrency": 4,
        "judge_model": "",
        "summary_model": "",
        "candidates": [],
        "monthly_calls": 100000,
        # how today's models are named in Otari, when not the catalog id, e.g. {gpt-4.1: azure-eu:gpt-4.1}
        "today_models": {},
    },
    "thresholds": {
        "switch_mean": 4.0,
        "switch_min_share": 0.9,
        "fallback_mean": 3.5,
        "fallback_check_rate": 0.85,
        "max_latency_ratio": 4.0,  # a candidate slower than this many times today's model is not chosen; 0 = off
        "max_cost_ratio": 1.0,  # nor one that costs more than this many times today's model; 0 = off
    },
    "privacy": {"mode": "mask", "use_otari_guardrail": False, "guardrail_profile": ""},
    "tests": {"command": "pytest -q", "run_local": True, "sandbox_checks": True},
    "github": {"mcp_server_id": "", "base_branch": "main"},
    "sites": {},
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    root: Path
    data: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))

    # --- convenient accessors -------------------------------------------------
    @property
    def base_url(self) -> str:
        return os.environ.get("OTARI_BASE_URL") or self.data["otari"]["base_url"]

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.data["otari"]["api_key_env"])

    @property
    def work_dir(self) -> Path:
        d = self.root / WORK_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ev(self, key: str):
        return self.data["eval"][key]

    def th(self, key: str) -> float:
        return float(self.data["thresholds"][key])

    def site_cfg(self, site_id: str) -> dict:
        return (self.data.get("sites") or {}).get(site_id, {}) or {}

    def candidates_for(self, site_id: str) -> list[str]:
        return list(self.site_cfg(site_id).get("candidates") or self.ev("candidates"))

    def excluded(self, site_id: str) -> bool:
        return bool(self.site_cfg(site_id).get("exclude"))

    def region(self) -> str:
        url = self.base_url
        for name, u in REGIONS.items():
            if url.rstrip("/") == u:
                return name
        return "custom"

    def validate(self, need_models: bool = True) -> list[str]:
        problems = []
        if not self.api_key:
            problems.append(
                f"No Otari key found. Set the {self.data['otari']['api_key_env']} environment variable."
            )
        if need_models:
            if not self.ev("candidates"):
                problems.append("No candidate models set. Add 2 or 3 open-weight model ids under eval.candidates.")
            if not self.ev("judge_model"):
                problems.append("No judge model set. Add one under eval.judge_model.")
        b = float(self.ev("budget_usd"))
        if b <= 0:
            problems.append("eval.budget_usd must be more than 0.")
        return problems

    def save(self) -> Path:
        path = self.root / CONFIG_NAME
        text = dump(self.data)
        path.write_text(HEADER + text, encoding="utf-8")
        return path


HEADER = """# Otari Switch configuration. The Otari key itself never goes in this file:
# set it in the environment variable named under otari.api_key_env.
# Model ids differ by provider and region: copy them from your Otari model catalog.
"""


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def dump(data: dict) -> str:
    if yaml is not None:
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return json.dumps(data, indent=2)


def load_dotenv(*folders: Path) -> list[str]:
    """Read OTARI_* variables from a `.env` file (repo root, then the current folder) into the environment.

    Only OTARI_* names are read and a variable already set in the environment always wins, so the file is a
    convenience for keeping the key out of shell history and chat. Keep `.env` out of git."""
    loaded = []
    for folder in folders:
        f = Path(folder) / ".env"
        if not f.is_file():
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip().strip('"').strip("'")
            if k.startswith("OTARI_") and v and not os.environ.get(k):
                os.environ[k] = v
                loaded.append(k)
    return loaded


def load(root: str | os.PathLike = ".") -> Config:
    root = Path(root).resolve()
    load_dotenv(root, Path.cwd())
    path = root / CONFIG_NAME
    data = copy.deepcopy(DEFAULTS)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        try:
            loaded = yaml.safe_load(text) if yaml is not None else json.loads(text)
        except Exception as e:  # noqa: BLE001
            raise ConfigError(f"{CONFIG_NAME} could not be read: {e}") from e
        if loaded is not None and not isinstance(loaded, dict):
            raise ConfigError(f"{CONFIG_NAME} must be a mapping at the top level.")
        data = _merge(DEFAULTS, loaded or {})
    return Config(root=root, data=data)


def ensure_gitignore(root: Path) -> None:
    gi = root / ".gitignore"
    line = f"{WORK_DIR}/"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if line not in existing.splitlines():
        with gi.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"# Otari Switch run data (recordings may contain prompts)\n{line}\n")
