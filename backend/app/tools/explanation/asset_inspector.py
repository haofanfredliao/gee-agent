"""GEE Asset 元数据检查工具。

用于在 agent 执行代码之前获取数据集的必要信息，
例如 FeatureCollection 的属性字段名、要素数量、几何类型，
或 Image 的波段名称与数据类型。

这些信息会写入 WorkflowState.context，供后续 execute 步骤安全地生成代码
（避免硬编码或猜测字段名）。
"""
from typing import Any, Dict


def inspect_vector_asset(asset_id: str) -> Dict[str, Any]:
    """
    检查 GEE FeatureCollection 的元数据。

    Returns
    -------
    dict with keys:
        status          : "ok" | "error"
        asset_id        : str
        property_names  : List[str]  — 第一个 Feature 的属性字段列表
        feature_count   : int        — 集合中的要素总数
        geometry_type   : str        — 几何类型（如 "Polygon"）
    """
    from backend.app.services.gee_client import init_gee_client
    if not init_gee_client():
        return {"status": "error", "message": "GEE 未初始化", "asset_id": asset_id}
    try:
        import ee
        fc = ee.FeatureCollection(asset_id)
        first = fc.first()
        prop_names = first.propertyNames().getInfo()
        size = fc.size().getInfo()
        geom_type = first.geometry().type().getInfo()
        return {
            "status": "ok",
            "asset_id": asset_id,
            "property_names": prop_names,
            "feature_count": size,
            "geometry_type": geom_type,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "asset_id": asset_id}


def inspect_image_asset(asset_id: str) -> Dict[str, Any]:
    """
    检查 GEE Image 的元数据（波段名、属性等）。

    Returns
    -------
    dict with keys:
        status      : "ok" | "error"
        asset_id    : str
        bands       : List[str]
        properties  : dict
    """
    from backend.app.services.gee_client import init_gee_client
    if not init_gee_client():
        return {"status": "error", "message": "GEE 未初始化", "asset_id": asset_id}
    try:
        import ee
        img = ee.Image(asset_id)
        info = img.getInfo()
        bands = [b["id"] for b in info.get("bands", [])]
        return {
            "status": "ok",
            "asset_id": asset_id,
            "bands": bands,
            "properties": info.get("properties", {}),
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "asset_id": asset_id}


def inspect_asset(asset_id: str) -> Dict[str, Any]:
    """
    自动判断 asset 类型并调用对应检查函数。
    先尝试 FeatureCollection，失败则回退到 Image。
    """
    result = inspect_vector_asset(asset_id)
    if result["status"] == "error":
        result = inspect_image_asset(asset_id)
    return result
