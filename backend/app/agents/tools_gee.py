"""GEE 相关 Tools：加载 Asset、NDVI 示例。"""
from typing import Any, Dict, List

from backend.app.services import gee_client


def gee_load_simple_asset(asset_id: str) -> Dict[str, Any]:
    """加载一个 GEE 官方 Asset，返回 tile_url 等。"""
    return gee_client.load_simple_asset(asset_id)


def gee_run_ndvi_example(
    bbox: List[float],
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """在给定区域和日期范围内运行 NDVI 示例。"""
    return gee_client.run_ndvi_example(bbox, start_date, end_date)
