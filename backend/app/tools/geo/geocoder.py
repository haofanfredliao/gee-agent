"""Geocoding tool: resolve a place name into coordinates and a bounding box.

This tool wraps services/geocoding.py and exposes a clean interface for the
orchestrator to consume. The result dict is also used to update the session
map context.
"""
from typing import Any, Dict

from backend.app.services.geocoding import geocode_place_name


def resolve_place(place_name: str) -> Dict[str, Any]:
    """Resolve *place_name* to map coordinates.

    Returns
    -------
    dict with keys:
        status      : "ok" | "error"
        place_name  : str   — echo of the input
        center_lat  : float
        center_lon  : float
        bbox        : [min_lon, min_lat, max_lon, max_lat]
        zoom        : int   — suggested map zoom level
    """
    place_name = (place_name or "").strip()
    if not place_name:
        return {
            "status": "error",
            "place_name": place_name,
            "message": "地名为空",
        }
    try:
        lat, lon, bbox = geocode_place_name(place_name)
        # Compute a reasonable zoom based on bbox span
        lon_span = abs(bbox[2] - bbox[0])
        zoom = 12 if lon_span < 0.1 else (10 if lon_span < 0.5 else (8 if lon_span < 2 else 6))
        return {
            "status": "ok",
            "place_name": place_name,
            "center_lat": lat,
            "center_lon": lon,
            "bbox": bbox,
            "zoom": zoom,
        }
    except Exception as exc:
        return {
            "status": "error",
            "place_name": place_name,
            "message": str(exc),
        }
