"""聊天相关 Pydantic 模型。"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class MapContext(BaseModel):
    """当前地图上下文。"""
    center_lat: Optional[float] = None
    center_lon: Optional[float] = None
    zoom: Optional[int] = None
    bbox: Optional[List[float]] = None


class MapUpdate(BaseModel):
    """地图更新指令。"""
    center_lat: float
    center_lon: float
    zoom: int
    bbox: Optional[List[float]] = None
    layer_info: Optional[Dict[str, Any]] = None
    layers: Optional[List[Dict[str, Any]]] = None


class ChatRequest(BaseModel):
    """聊天请求。"""
    message: str
    session_id: Optional[str] = None
    map_context: Optional[MapContext] = None


class WorkflowStatus(BaseModel):
    """工作流执行状态摘要，供前端展示中间状态。"""
    intent: str                        # "execution" | "knowledge"
    status: str                        # 最终状态，通常为 "terminated"
    plan: List[str]                    # 各步骤的描述列表
    steps_completed: int
    steps_total: int
    steps: List[Dict[str, Any]] = []   # 每步详情（含 output_preview）


class ChatResponse(BaseModel):
    """聊天响应。"""
    reply: str
    map_update: Optional[MapUpdate] = None
    workflow_status: Optional[WorkflowStatus] = None
