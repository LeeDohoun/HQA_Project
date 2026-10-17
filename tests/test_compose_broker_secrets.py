"""The AI-side Compose services must not receive broker credentials or the backend's credential key."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _kis_names_read_by_python():
    names = set()
    for folder in ("src", "ai_server", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if "__pycache__" not in path.parts:
                names.update(re.findall(r"getenv\(\s*[\"'](KIS_[A-Z0-9_]+)[\"']", path.read_text(encoding="utf-8")))
    return names


def test_ai_side_containers_get_no_broker_secrets_whatever_env_holds():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    secrets = _kis_names_read_by_python() | {"HQA_KIS_ENC_KEY"}
    assert {"KIS_APP_KEY", "KIS_PAPER_APP_KEY", "KIS_PAPER_ACCOUNT_NO"} <= secrets  # the scan found them
    python_services = [name for name, service in compose["services"].items()
                       if ".env" in (service.get("env_file") or []) and name != "backend"]
    assert {"ai", "analysis-scheduler", "signal-monitor"} <= set(python_services)
    for name in python_services:
        environment = compose["services"][name].get("environment") or {}
        if isinstance(environment, list):  # KEY=value entries; a bare KEY passes the host value through
            environment = dict(item.split("=", 1) if "=" in item else (item, None) for item in environment)
        # Only an explicit empty value overrides what env_file supplies.
        leaked = sorted(key for key in secrets if environment.get(key) != "")
        assert not leaked, f"{name} would receive {leaked} from .env"
