"""GEE 客户端：初始化与底图配置。

职责范围：
  - init_gee_client()    : 初始化 GEE（OAuth2 + project）
  - get_basemap_config() : 返回前端底图默认参数

具体 GEE 任务（load_simple_asset、run_ndvi_example 等）已迁移至
backend.app.tools.execution.gee_tasks。
"""
import os
from typing import Any, Dict

from backend.app.core.config import (
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LON,
    DEFAULT_ZOOM,
    GEE_PROJECT_ID,
)
#test
_gee_initialized = False


def init_gee_client() -> bool:
    """初始化 GEE（需配置 project）。支持环境变量 GEE_PROJECT_ID 或 configs 中 gee.project_id。"""
    global _gee_initialized
    try:
        import ee
    except ImportError:
        return False
    if _gee_initialized:
        return True
    project = (os.environ.get("GEE_PROJECT_ID") or GEE_PROJECT_ID or "").strip()
    if not project:
        return False
    try:
        # 必须指定 project，否则 GEE 无法正常启动
        ee.Initialize(project=project)
        _gee_initialized = True
        return True
    except Exception:
        return False


def get_basemap_config() -> Dict[str, Any]:
    """返回前端底图所需配置：默认中心、缩放。"""
    return {
        "center_lat": DEFAULT_CENTER_LAT,
        "center_lon": DEFAULT_CENTER_LON,
        "zoom": DEFAULT_ZOOM,
    }



