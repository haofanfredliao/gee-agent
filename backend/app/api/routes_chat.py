"""聊天 API：POST /chat -> ChatResponse。"""
from fastapi import APIRouter
from backend.app.models.chat import ChatRequest, ChatResponse
from backend.app.agents.agent_gee_assistant import run_gee_agent

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """接收用户消息，经 GEE 助手 agent 返回回复与可选地图更新。"""
    response = await run_gee_agent(request.message)
    return response
