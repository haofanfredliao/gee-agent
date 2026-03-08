"""地理编码：地名 -> 经纬度与 bbox。"""
from typing import List, Tuple

import os

try:
    import httpx
except ImportError:
    httpx = None


def geocode_place_name(place_name: str) -> Tuple[float, float, List[float]]:
    """
    将地名解析为中心点 (lat, lon) 与 bbox [min_lon, min_lat, max_lon, max_lat]。
    无 API 时使用占位数据（香港/九龙等）。
    """
    place_name = (place_name or "").strip()
    if not place_name:
        return (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5])

    place_lower = place_name.lower()
    # 占位：常见地名硬编码（API 不可用时回退）
    places = {
        "香港": (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5]),
        "hong kong": (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5]),
        "九龙": (22.3120, 114.1740, [114.15, 22.28, 114.25, 22.35]),
        "kowloon": (22.3120, 114.1740, [114.15, 22.28, 114.25, 22.35]),
        "北京": (39.9042, 116.4074, [116.2, 39.8, 116.6, 40.0]),
        "上海": (31.2304, 121.4737, [121.3, 31.1, 121.6, 31.4]),
    }
    for name, (lat, lon, bbox) in places.items():
        if name in place_lower or place_lower in name:
            return (lat, lon, bbox)

    api_key = os.environ.get("GEOCODING_API_KEY")
    if api_key and httpx:
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    url,
                    params={"address": place_name, "key": api_key},
                )
            data = r.json()
            if data.get("status") == "OK" and data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                lat, lon = loc["lat"], loc["lng"]
                bounds = data["results"][0].get("geometry", {}).get("viewport") or {}
                sw = bounds.get("southwest", {})
                ne = bounds.get("northeast", {})
                bbox = [
                    sw.get("lng", lon - 0.01),
                    sw.get("lat", lat - 0.01),
                    ne.get("lng", lon + 0.01),
                    ne.get("lat", lat + 0.01),
                ]
                return (lat, lon, bbox)
        except Exception:
            pass

    # 默认返回香港中心
    return (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5])
