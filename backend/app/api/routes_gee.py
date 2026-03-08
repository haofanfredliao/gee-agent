"""GEE 任务 API：POST /gee/run，GET /gee/basemap。"""
from fastapi import APIRouter
from backend.app.models.gee import GeeTaskRequest, GeeTaskResponse
from backend.app.services import gee_client

router = APIRouter()


@router.get("/basemap")
def get_basemap():
    """返回底图配置：默认中心、缩放。"""
    return gee_client.get_basemap_config()


@router.post("/run", response_model=GeeTaskResponse)
def run_gee_task(request: GeeTaskRequest):
    """根据 task_type 执行 load_asset 或 ndvi_example。"""
    task_type = (request.task_type or "").strip().lower()
    params = request.params or {}

    if task_type == "load_asset":
        asset_id = params.get("asset_id", "USGS/SRTMGL1_003")
        result = gee_client.load_simple_asset(asset_id)
        status = "ok" if result.get("status") == "ok" else "error"
        return GeeTaskResponse(status=status, result=result)

    if task_type == "ndvi_example":
        bbox = params.get("bbox", [114.15, 22.28, 114.25, 22.35])
        start_date = params.get("start_date", "2020-01-01")
        end_date = params.get("end_date", "2022-12-31")
        result = gee_client.run_ndvi_example(bbox, start_date, end_date)
        status = "ok" if result.get("status") == "ok" else "error"
        return GeeTaskResponse(status=status, result=result)

    return GeeTaskResponse(status="error", result={"message": f"未知 task_type: {task_type}"})
