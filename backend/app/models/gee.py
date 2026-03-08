"""GEE 任务相关 Pydantic 模型。"""
from typing import Any, Dict

from pydantic import BaseModel


class GeeTaskRequest(BaseModel):
    """GEE 任务请求。"""
    task_type: str  # 如 "load_asset", "ndvi_example"
    params: Dict[str, Any] = {}


class GeeTaskResponse(BaseModel):
    """GEE 任务响应。"""
    status: str  # "ok" | "error"
    result: Dict[str, Any] = {}  # tile_url, stats 等
