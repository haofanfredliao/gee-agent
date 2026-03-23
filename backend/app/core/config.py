"""应用配置：从环境变量和 YAML 读取。"""
from pathlib import Path
from typing import Any, Optional

import os
import yaml
from dotenv import load_dotenv

def _project_root() -> Path:
    # backend/app/core/config.py -> parents[3]=backend, parents[4]=project root
    return Path(__file__).resolve().parents[4]

# 加载 .env 文件
_env_path = _project_root() / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _project_root() -> Path:
    # backend/app/core/config.py -> parents[3]=backend, parents[4]=project root
    return Path(__file__).resolve().parents[4]


def get_settings_path() -> Path:
    return _project_root() / "configs" / "settings.yaml"


def get_models_path() -> Path:
    return _project_root() / "configs" / "models.yaml"


def get_gee_tasks_path() -> Path:
    return _project_root() / "configs" / "gee_tasks.yaml"


# 优先读 settings.yaml，否则读 settings.example.yaml
def _settings() -> dict:
    base = _project_root()
    path = base / "configs" / "settings.yaml"
    if not path.exists():
        path = base / "configs" / "settings.example.yaml"
    return _load_yaml(path)


def get_setting(key: str, default: Any = None) -> Any:
    s = _settings()
    return s.get(key, os.environ.get(key.upper(), default))


# 常用配置项
BACKEND_PORT: int = int(get_setting("backend", {}).get("port", 8000))
BACKEND_URL: str = get_setting("backend", {}).get("url", "http://127.0.0.1:8000")
DEFAULT_CENTER_LAT: float = float(get_setting("map", {}).get("center_lat", 22.3193))
DEFAULT_CENTER_LON: float = float(get_setting("map", {}).get("center_lon", 114.1694))
DEFAULT_ZOOM: int = int(get_setting("map", {}).get("zoom", 10))
DEFAULT_MODEL: str = get_setting("llm", {}).get("default_model", "gpt-4")

CHROMA_PERSIST_DIR: str = get_setting("chroma", {}).get("persist_dir", "./data/chroma")

# GEE：project 必填，否则 GEE 无法正常启动
def _gee_settings() -> dict:
    v = get_setting("gee", {})
    if not isinstance(v, dict):
        return {}
    return v


GEE_PROJECT_ID: Optional[str] = os.environ.get("GEE_PROJECT_ID") or _gee_settings().get("project_id")
