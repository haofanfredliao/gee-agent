"""前端调用后端 API 的客户端。"""
import json
import os
from typing import Any, Dict, Generator, Optional

import httpx

# 默认后端地址，可通过环境变量 OVERRIDE_BACKEND_URL 覆盖
BASE_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
# 工作流需要串行多次 LLM + GEE 调用，保守设置 5 分钟
TIMEOUT = float(os.environ.get("CHAT_TIMEOUT", "300"))


def _url(path: str) -> str:
    return f"{BASE_URL.rstrip('/')}{path}"


def chat(message: str, session_id: Optional[str] = None, map_context: Optional[Dict] = None) -> Dict[str, Any]:
    """发送聊天消息，返回 ChatResponse 字典。"""
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    if map_context:
        payload["map_context"] = map_context
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(_url("/chat"), json=payload)
        r.raise_for_status()
        return r.json()


def chat_stream(
    message: str,
    session_id: Optional[str] = None,
    map_context: Optional[Dict] = None,
) -> Generator[Dict[str, Any], None, None]:
    """
    流式聊天：逐行解析后端 /chat/stream 推送的 newline-delimited JSON 事件。

    每次 yield 一个事件字典，type 可能为：
      "routing" | "planning" | "step_start" | "step_done" | "summarizing" | "done" | "error"
    """
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    if map_context:
        payload["map_context"] = map_context
    with httpx.Client(timeout=TIMEOUT) as client:
        with client.stream("POST", _url("/chat/stream"), json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass


def get_basemap_config() -> Dict[str, Any]:
    """获取底图配置：优先调用 GET /chat/basemap。"""
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(_url("/chat/basemap"))
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {"center_lat": 22.3193, "center_lon": 114.1694, "zoom": 10}
