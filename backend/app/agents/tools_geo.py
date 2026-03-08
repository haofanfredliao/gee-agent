"""地理相关 Tool：地名解析。"""
from typing import Any, Dict

from backend.app.services import geocoding


def geo_lookup(place_name: str) -> Dict[str, Any]:
    """根据地名返回中心点与 bbox。"""
    lat, lon, bbox = geocoding.geocode_place_name(place_name)
    return {"center_lat": lat, "center_lon": lon, "bbox": bbox}
