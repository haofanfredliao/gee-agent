"""聊天 API：POST /chat -> ChatResponse。"""
from fastapi import APIRouter
from backend.app.models.chat import ChatRequest, ChatResponse
from backend.app.agents.orchestrator import run_workflow

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """接收用户消息，经工作流状态机返回回复与可选地图更新。"""
    response = await run_workflow(
        query=request.message,
        session_id=request.session_id or "",
    )
    return response
