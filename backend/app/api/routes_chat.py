"""聊天 API：POST /chat -> ChatResponse，POST /chat/stream -> SSE 流。"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from backend.app.models.chat import ChatRequest, ChatResponse
from backend.app.agents.orchestrator import run_workflow, stream_workflow

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """接收用户消息，经工作流状态机返回回复与可选地图更新。"""
    response = await run_workflow(
        query=request.message,
        session_id=request.session_id or "",
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
        ),
        media_type="application/x-ndjson",
    )
