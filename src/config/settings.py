from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


@dataclass(frozen=True)
class EnvStatus:
    loaded: bool
    path: Optional[Path]
    message: str


@dataclass(frozen=True)
class HQASettings:
    project_root: Path
    data_dir: Path
    traces_dir: Path
    orders_dir: Path
    env_status: EnvStatus


_ENV_STATUS: Optional[EnvStatus] = None


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(value: str, base_dir: Path) -> Path:
    raw = (value or "").strip()
    if not raw:
        return base_dir

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def load_project_env(force: bool = False) -> EnvStatus:
    global _ENV_STATUS
    if _ENV_STATUS is not None and not force:
        return _ENV_STATUS

    root = get_project_root()
    env_ai = root / ".env-ai"
    env_default = root / ".env"

    if load_dotenv is None:
        _ENV_STATUS = EnvStatus(
            loaded=False,
            path=None,
            message="python-dotenv 미설치 상태입니다. OS 환경 변수와 기본값만 사용합니다.",
        )
        return _ENV_STATUS

    if env_ai.exists():
        load_dotenv(env_ai, override=False)
        _ENV_STATUS = EnvStatus(
            loaded=True,
            path=env_ai,
            message=f"{env_ai.name} 파일을 로드했습니다.",
        )
        return _ENV_STATUS

    if env_default.exists():
        load_dotenv(env_default, override=False)
        _ENV_STATUS = EnvStatus(
            loaded=True,
            path=env_default,
            message=f"{env_default.name} 파일을 로드했습니다.",
        )
        return _ENV_STATUS

    _ENV_STATUS = EnvStatus(
        loaded=False,
        path=None,
        message=(
            "`.env-ai` 또는 `.env` 파일을 찾지 못했습니다. "
            "기본값과 현재 셸 환경 변수를 사용합니다."
        ),
    )
    return _ENV_STATUS


@lru_cache(maxsize=1)
def get_settings() -> HQASettings:
    env_status = load_project_env()
    project_root = get_project_root()
    data_dir = _resolve_path(os.getenv("HQA_DATA_DIR", "./data"), project_root)
    traces_dir = _resolve_path(
        os.getenv("HQA_TRACES_DIR", str(data_dir / "traces")),
        project_root,
    )
    orders_dir = _resolve_path(
        os.getenv("HQA_ORDERS_DIR", str(data_dir / "orders")),
        project_root,
    )

    return HQASettings(
        project_root=project_root,
        data_dir=data_dir,
        traces_dir=traces_dir,
        orders_dir=orders_dir,
        env_status=env_status,
    )


def get_env_status() -> EnvStatus:
    return get_settings().env_status


def get_data_dir() -> Path:
    return get_settings().data_dir


def get_traces_dir() -> Path:
    return get_settings().traces_dir


def get_orders_dir() -> Path:
    return get_settings().orders_dir


def reset_settings_cache() -> None:
    global _ENV_STATUS
    _ENV_STATUS = None
    get_settings.cache_clear()
