"""聊天相关 Pydantic 模型。"""
from typing import Any, Dict, Optional

from pydantic import BaseModel


class MapContext(BaseModel):
    """当前地图上下文。"""
    center_lat: Optional[float] = None
    center_lon: Optional[float] = None
    zoom: Optional[int] = None


class MapUpdate(BaseModel):
    """地图更新指令。"""
    center_lat: float
    center_lon: float
    zoom: int
    layer_info: Optional[Dict[str, Any]] = None


class ChatRequest(BaseModel):
    """聊天请求。"""
    message: str
    session_id: Optional[str] = None
    map_context: Optional[MapContext] = None


class ChatResponse(BaseModel):
    """聊天响应。"""
    reply: str
    map_update: Optional[MapUpdate] = None
