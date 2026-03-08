"""地理编码 API：POST /geo/resolve。"""
from fastapi import APIRouter
from backend.app.models.geo import GeoQueryRequest, GeoQueryResponse
from backend.app.services import geocoding

router = APIRouter()


@router.post("/resolve", response_model=GeoQueryResponse)
def geo_resolve(request: GeoQueryRequest):
    """地名解析：返回中心点与 bbox。"""
    lat, lon, bbox = geocoding.geocode_place_name(request.place_name)
    return GeoQueryResponse(center_lat=lat, center_lon=lon, bbox=bbox)
