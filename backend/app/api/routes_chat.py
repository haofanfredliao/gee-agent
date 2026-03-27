"""聊天 API：POST /chat -> ChatResponse，POST /chat/stream -> SSE 流，POST /chat/history -> 历史落盘。"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.models.chat import ChatRequest, ChatResponse
from backend.app.agents.orchestrator import run_workflow, stream_workflow
from backend.app.core.config import DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON, DEFAULT_ZOOM

router = APIRouter()


# ─── 历史记录请求模型 ─────────────────────────────────────────────────────────

class ChatHistorySaveRequest(BaseModel):
    session_id: str
    messages: List[Dict[str, Any]]



@router.get("/basemap")
def chat_basemap():
    """返回聊天页面默认底图配置。"""
    return {
        "center_lat": DEFAULT_CENTER_LAT,
        "center_lon": DEFAULT_CENTER_LON,
        "zoom": DEFAULT_ZOOM,
    }


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """接收用户消息，经工作流状态机返回回复与可选地图更新。"""
    response = await run_workflow(
        query=request.message,
        session_id=request.session_id or "",
        map_context=request.map_context.model_dump(exclude_none=True) if request.map_context else None,
    )
    return response


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """
    流式聊天端点：以 newline-delimited JSON 逐步推送工作流事件。
    每个事件为一行 JSON，前端可按行解析并实时更新 UI。
    """
    return StreamingResponse(
        stream_workflow(
            query=request.message,
            session_id=request.session_id or "",
            map_context=request.map_context.model_dump(exclude_none=True) if request.map_context else None,
        ),
        media_type="application/x-ndjson",
    )


# ─── 历史记录 ─────────────────────────────────────────────────────────────────

@router.post("/history")
async def save_history(request: ChatHistorySaveRequest):
    """保存完整对话历史到 session store。"""
    from backend.app.agents.session_store import save_chat_history
    save_chat_history(request.session_id, request.messages)
    return {"status": "ok", "session_id": request.session_id, "saved": len(request.messages)}


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    """获取指定 session 的对话历史。"""
    from backend.app.agents.session_store import load_chat_history
    messages = load_chat_history(session_id)
    return {"session_id": session_id, "messages": messages}
