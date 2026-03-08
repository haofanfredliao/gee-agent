"""地理/地理编码相关 Pydantic 模型。"""
from typing import List

from pydantic import BaseModel


class GeoQueryRequest(BaseModel):
    """地名查询请求。"""
    place_name: str


class GeoQueryResponse(BaseModel):
    """地名查询响应：中心点 + 边界框。"""
    center_lat: float
    center_lon: float
    bbox: List[float]  # [min_lon, min_lat, max_lon, max_lat]
