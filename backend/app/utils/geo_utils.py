"""地理相关工具（占位，可后续扩展）。"""
from typing import List, Tuple


def bbox_to_center(bbox: List[float]) -> Tuple[float, float]:
    """bbox [min_lon, min_lat, max_lon, max_lat] -> (center_lat, center_lon)。"""
    if len(bbox) < 4:
        return 0.0, 0.0
    min_lon, min_lat, max_lon, max_lat = bbox[:4]
    return (min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0
