"""GEE 内置任务工具：加载简单 Asset、NDVI 示例等。

这些函数从 services/gee_client.py 迁移而来，属于具体 GEE 任务，
而非 GEE 连接管理（gee_client 职责）。
"""
from typing import Any, Dict, List


def load_simple_asset(asset_id: str) -> Dict[str, Any]:
    """
    加载一个 GEE Asset（Image），返回 tile_url 配置。

    Parameters
    ----------
    asset_id : str
        GEE Asset 路径，例如 "USGS/SRTMGL1_003"。
    """
    from backend.app.services.gee_client import init_gee_client

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
        vis = {"min": 0, "max": 3000}
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
    在给定 bbox 和日期范围内计算 NDVI 示例（MODIS MOD13Q1），返回图层及统计。

    Parameters
    ----------
    bbox       : [min_lon, min_lat, max_lon, max_lat]
    start_date : ISO 日期字符串，例如 "2023-01-01"
    end_date   : ISO 日期字符串，例如 "2023-12-31"
    """
    from backend.app.services.gee_client import init_gee_client

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
        col = (
            ee.ImageCollection("MODIS/006/MOD13Q1")
            .filterDate(start_date, end_date)
            .filterBounds(roi)
            .select("NDVI")
        )
        # MOD13Q1 NDVI is scaled by 0.0001; convert to physical range [-1, 1].
        ndvi = col.mean().multiply(0.0001).rename("NDVI").clamp(-1, 1).clip(roi)
        vis = {
            "min": -0.2,
            "max": 0.8,
            "palette": ["#8c510a", "#d8b365", "#f6e8c3", "#c7eae5", "#5ab4ac", "#01665e"],
        }
        map_id = ndvi.getMapId(vis)
        tile_url = map_id.get("tile_fetcher").url_format if map_id else None
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
