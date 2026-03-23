"""GEE 客户端：初始化、底图配置、加载 Asset、NDVI 示例。"""
import os
from typing import Any, Dict, List, Optional

from backend.app.core.config import (
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LON,
    DEFAULT_ZOOM,
    GEE_PROJECT_ID,
)

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


def load_simple_asset(asset_id: str) -> Dict[str, Any]:
    """
    加载一个 GEE 官方 Asset（如 USGS/SRTMGL1_003），返回 tile_url 或 layer 配置。
    未初始化 GEE 时返回占位。
    """
    if not init_gee_client():
        return {
            "status": "placeholder",
            "message": "GEE 未初始化，请配置并运行 test_gee_connection.py",
            "tile_url": None,
            "asset_id": asset_id,
        }
    try:
        import ee
        asset = ee.Image(asset_id)
        # 获取用于 Map 的 tile URL 需要 getMapId
        vis = {"min": 0, "max": 3000}  # SRTM 高程范围
        map_id = asset.getMapId(vis)
        return {
            "status": "ok",
            "tile_url": map_id.get("tile_fetcher").url_format if map_id else None,
            "asset_id": asset_id,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "tile_url": None, "asset_id": asset_id}


def run_ndvi_example(
    bbox: List[float],
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """
    在给定 bbox 和日期范围内计算 NDVI 示例（如 MODIS MOD13Q1），返回图层或统计。
    bbox: [min_lon, min_lat, max_lon, max_lat]
    """
    if not init_gee_client():
        return {
            "status": "placeholder",
            "message": "GEE 未初始化",
            "tile_url": None,
            "stats": None,
        }
    try:
        import ee
        roi = ee.Geometry.Rectangle(bbox)
        # MODIS NDVI 产品
        col = (
            ee.ImageCollection("MODIS/006/MOD13Q1")
            .filterDate(start_date, end_date)
            .filterBounds(roi)
            .select("NDVI")
        )
        ndvi = col.mean().clip(roi)
        vis = {"min": 0, "max": 9000, "palette": ["white", "green"]}
        map_id = ndvi.getMapId(vis)
        tile_url = map_id.get("tile_fetcher").url_format if map_id else None
        # 简单统计
        stat = ndvi.reduceRegion(
            ee.Reducer.mean(),
            geometry=roi,
            scale=250,
            maxPixels=1e9,
        )
        return {
            "status": "ok",
            "tile_url": tile_url,
            "stats": stat.getInfo() if stat else None,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "tile_url": None, "stats": None}

def execute_gee_code_simple(code: str) -> Dict[str, Any]:
    """执行包含 GEE API 调用的 Python 代码并返回输出及图层 URL。"""
    if not init_gee_client():
        return {"status": "error", "log": "GEE 未初始化", "tile_url": None}
    
    import io
    import sys
    import ee
    
    class MockMap:
        def __init__(self):
            self.tile_url = None
        def addLayer(self, ee_object, vis_params=None, name=None, shown=True, opacity=1):
            try:
                map_id = ee_object.getMapId(vis_params or {})
                self.tile_url = map_id.get("tile_fetcher").url_format if map_id else None
            except Exception as e:
                print(f"Error adding layer: {e}")
        def centerObject(self, ee_object, zoom=None):
            pass  # 前端由 map_update 控制中心点，此处忽略
        def setCenter(self, lon, lat, zoom=None):
            pass  # 同上
                
    m = MockMap()
    old_stdout = sys.stdout
    sys.stdout = captured_stdout = io.StringIO()
    
    local_env = {"ee": ee, "Map": m, "print": print}
    
    try:
        exec(code, local_env, local_env)
        stdout_str = captured_stdout.getvalue()
        return {"status": "ok", "log": stdout_str, "tile_url": m.tile_url}
    except Exception as e:
        return {"status": "error", "log": captured_stdout.getvalue() + f"\nError: {e}", "tile_url": None}
    finally:
        sys.stdout = old_stdout
