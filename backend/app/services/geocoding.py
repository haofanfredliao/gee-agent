"""Geocoding helpers backed by Nominatim, with local fallbacks.

The same Nominatim search endpoint can return either ordinary geocoding
results or polygon GeoJSON boundaries via ``polygon_geojson=1``. Boundary
caching lives in ``tools.geo.osm_boundary``; this module only performs the
network search and bbox/center extraction.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None


NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"


def osm_user_agent() -> str:
    """Return a policy-friendly User-Agent for OSM/Nominatim requests."""
    return os.environ.get(
        "OSM_USER_AGENT",
        "gee-agent-hku-geog7310/1.0 (educational boundary cache; set OSM_USER_AGENT for contact)",
    )


def nominatim_search(
    place_name: str,
    *,
    polygon_geojson: bool = False,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search Nominatim and return raw JSON results.

    ``polygon_geojson=True`` asks Nominatim to include the OSM boundary
    geometry when available. Callers must cache polygon responses and avoid
    repeated high-frequency requests.
    """
    place_name = (place_name or "").strip()
    if not place_name:
        return []

    params = {
        "q": place_name,
        "format": "jsonv2",
        "addressdetails": "1",
        "extratags": "1",
        "limit": str(max(1, min(int(limit), 50))),
        "dedupe": "1",
    }
    if polygon_geojson:
        params["polygon_geojson"] = "1"

    req = Request(
        NOMINATIM_SEARCH_URL + "?" + urlencode(params),
        headers={
            "User-Agent": osm_user_agent(),
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fallback_places() -> Dict[str, Tuple[float, float, List[float]]]:
    return {
        "香港": (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5]),
        "hong kong": (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5]),
        "九龙": (22.3120, 114.1740, [114.15, 22.28, 114.25, 22.35]),
        "kowloon": (22.3120, 114.1740, [114.15, 22.28, 114.25, 22.35]),
        "北京": (39.9042, 116.4074, [116.2, 39.8, 116.6, 40.0]),
        "beijing": (39.9042, 116.4074, [116.2, 39.8, 116.6, 40.0]),
        "上海": (31.2304, 121.4737, [121.3, 31.1, 121.6, 31.4]),
        "shanghai": (31.2304, 121.4737, [121.3, 31.1, 121.6, 31.4]),
        "深圳": (22.5431, 114.0579, [113.75, 22.4, 114.65, 22.9]),
        "shenzhen": (22.5431, 114.0579, [113.75, 22.4, 114.65, 22.9]),
        "广州": (23.1291, 113.2644, [112.9, 22.9, 113.8, 23.6]),
        "guangzhou": (23.1291, 113.2644, [112.9, 22.9, 113.8, 23.6]),
    }


def _match_fallback_place(place_name: str) -> Tuple[float, float, List[float]] | None:
    place_lower = (place_name or "").strip().lower()
    for name, value in _fallback_places().items():
        if name in place_lower or place_lower in name:
            return value
    return None


def _bbox_from_nominatim(item: Dict[str, Any], lon: float, lat: float) -> List[float]:
    # Nominatim boundingbox order is [south, north, west, east].
    raw = item.get("boundingbox") or []
    if len(raw) == 4:
        try:
            south, north, west, east = [float(v) for v in raw]
            return [west, south, east, north]
        except (TypeError, ValueError):
            pass
    return [lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01]


def _geocode_with_nominatim(place_name: str) -> Tuple[float, float, List[float]] | None:
    try:
        results = nominatim_search(place_name, polygon_geojson=False, limit=5)
    except Exception:
        return None
    if not results:
        return None
    best = results[0]
    try:
        lat = float(best["lat"])
        lon = float(best["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return lat, lon, _bbox_from_nominatim(best, lon, lat)


def _geocode_with_google(place_name: str) -> Tuple[float, float, List[float]] | None:
    api_key = os.environ.get("GEOCODING_API_KEY")
    if not api_key or not httpx:
        return None
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url, params={"address": place_name, "key": api_key})
        data = r.json()
        if data.get("status") != "OK" or not data.get("results"):
            return None
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
        return lat, lon, bbox
    except Exception:
        return None


def geocode_place_name(place_name: str) -> Tuple[float, float, List[float]]:
    """Resolve a place name to center ``lat/lon`` and bbox.

    Fast local fallbacks are used for common class/demo places. Other places
    try Nominatim first, then Google Geocoding if configured, and finally fall
    back to Hong Kong so the UI never crashes.
    """
    place_name = (place_name or "").strip()
    if not place_name:
        return (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5])

    fallback = _match_fallback_place(place_name)
    if fallback:
        return fallback

    resolved = _geocode_with_nominatim(place_name)
    if resolved:
        return resolved

    resolved = _geocode_with_google(place_name)
    if resolved:
        return resolved

    return (22.3193, 114.1694, [114.0, 22.2, 114.4, 22.5])
