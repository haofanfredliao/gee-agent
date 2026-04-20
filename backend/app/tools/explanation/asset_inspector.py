"""GEE Asset 元数据检查工具。

用于在 agent 执行代码之前获取数据集的必要信息，
例如 FeatureCollection 的属性字段名、要素数量、几何类型，
或 Image 的波段名称与数据类型。

这些信息会写入 WorkflowState.context，供后续 execute 步骤安全地生成代码
（避免硬编码或猜测字段名）。
"""
from typing import Any, Dict

ASSET_ID_NORMALIZATION_MAP = {
    "COPERNICUS/S2_SR": "COPERNICUS/S2_SR_HARMONIZED",
    "COPERNICUS/S2_SR_HARMONIZED_HARMONIZED": "COPERNICUS/S2_SR_HARMONIZED",
}


def _normalize_asset_id(asset_id: str) -> str:
    normalized = asset_id.strip()
    for _ in range(3):
        nxt = ASSET_ID_NORMALIZATION_MAP.get(normalized)
        if not nxt or nxt == normalized:
            break
        normalized = nxt
    return normalized


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
        normalized_asset_id = _normalize_asset_id(asset_id)
        fc = ee.FeatureCollection(normalized_asset_id)
        first = fc.first()
        prop_names = first.propertyNames().getInfo()
        size = fc.size().getInfo()
        geom_type = first.geometry().type().getInfo()
        out = {
            "status": "ok",
            "asset_id": normalized_asset_id,
            "property_names": prop_names,
            "feature_count": size,
            "geometry_type": geom_type,
        }
        if normalized_asset_id != asset_id:
            out["normalized_from"] = asset_id
        return out
    except Exception as e:
        return {"status": "error", "message": str(e), "asset_id": asset_id}


def inspect_image_asset(asset_id: str) -> Dict[str, Any]:
    """
    检查 GEE Image 的元数据（波段名、分辨率、属性等）。

    Returns
    -------
    dict with keys:
        status      : "ok" | "error"
        asset_id    : str
        bands       : List[str]
        scales : Dict[str, float | None]  — 各波段分辨率（米），从 crs_transform 推导
        properties  : dict
    """
    from backend.app.services.gee_client import init_gee_client
    if not init_gee_client():
        return {"status": "error", "message": "GEE 未初始化", "asset_id": asset_id}
    try:
        import ee
        normalized_asset_id = _normalize_asset_id(asset_id)
        img = ee.Image(normalized_asset_id)
        info = img.getInfo()
        bands = [b["id"] for b in info.get("bands", [])]
        # crs_transform = [x_scale, 0, x_origin, 0, y_scale, y_origin]
        # 第一个元素的绝对值即为该波段的地面分辨率（单位：米，投影依赖 CRS）
        band_scales: Dict[str, Any] = {}
        for b in info.get("bands", []):
            ct = b.get("crs_transform")
            band_scales[b["id"]] = abs(ct[0]) if ct and len(ct) >= 1 else None
        out = {
            "status": "ok",
            "asset_id": normalized_asset_id,
            "bands": bands,
            "scales": band_scales,
            "properties": info.get("properties", {}),
        }
        if normalized_asset_id != asset_id:
            out["normalized_from"] = asset_id
        return out
    except Exception as e:
        return {"status": "error", "message": str(e), "asset_id": asset_id}


def inspect_asset(asset_id: str) -> Dict[str, Any]:
    """
    自动判断 asset 类型并调用对应检查函数。
    优先用 ee.data.getAsset 判型，避免不必要的双重失败重试。
    """
    normalized_asset_id = _normalize_asset_id(asset_id)
    from backend.app.services.gee_client import init_gee_client
    if not init_gee_client():
        return {"status": "error", "message": "GEE 未初始化", "asset_id": asset_id}

    try:
        import ee
        meta = ee.data.getAsset(normalized_asset_id)
        asset_type = str(meta.get("type") or "").upper()
    except Exception as e:
        # Fail fast for missing/inaccessible assets instead of trying multiple loaders.
        return {
            "status": "error",
            "message": str(e),
            "asset_id": normalized_asset_id,
            "normalized_from": asset_id if normalized_asset_id != asset_id else None,
        }

    if asset_type in {"TABLE", "FEATURE_COLLECTION"}:
        return inspect_vector_asset(normalized_asset_id)
    if asset_type == "IMAGE":
        return inspect_image_asset(normalized_asset_id)
    if asset_type == "IMAGE_COLLECTION":
        try:
            import ee
            first = ee.ImageCollection(normalized_asset_id).first()
            bands = first.bandNames().getInfo() if first is not None else []
            return {
                "status": "ok",
                "asset_id": normalized_asset_id,
                "asset_type": "IMAGE_COLLECTION",
                "bands": bands or [],
                "note": "ImageCollection inspected with lightweight first-image band check.",
                "normalized_from": asset_id if normalized_asset_id != asset_id else None,
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "asset_id": normalized_asset_id,
                "normalized_from": asset_id if normalized_asset_id != asset_id else None,
            }

    return {
        "status": "error",
        "message": f"Unsupported asset type: {asset_type or 'unknown'}",
        "asset_id": normalized_asset_id,
        "normalized_from": asset_id if normalized_asset_id != asset_id else None,
    }
