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
    got_done = False
    stream_error: Optional[Exception] = None

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            with client.stream("POST", _url("/chat/stream"), json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") == "done":
                        got_done = True
                    yield evt
    except Exception as e:
        stream_error = e

    # 流式中断或未完整结束时，自动回退到非流式请求，避免用户看到“incomplete chunked read”直接失败。
    if got_done:
        return

    try:
        fallback_resp = chat(message, session_id=session_id, map_context=map_context)
        if stream_error is not None:
            yield {
                "type": "error",
                "data": {
                    "message": f"流式连接中断，已自动回退非流式返回最终结果：{stream_error}",
                },
            }
        yield {"type": "done", "data": fallback_resp}
    except Exception as fallback_error:
        if stream_error is not None:
            msg = f"请求失败：{stream_error}；回退失败：{fallback_error}"
        else:
            msg = f"请求失败：{fallback_error}"
        yield {"type": "error", "data": {"message": msg}}


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


def run_sandbox_code(code: str) -> Dict[str, Any]:
    """在沙箱中执行 GEE Python 代码，返回 {status, log, tile_url, layers}。"""
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(_url("/sandbox/run"), json={"code": code})
        r.raise_for_status()
        return r.json()


def save_history(session_id: str, messages: list) -> bool:
    """将对话历史持久化到后端 POST /chat/history，失败时静默返回 False。"""
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                _url("/chat/history"),
                json={"session_id": session_id, "messages": messages},
            )
            return r.status_code == 200
    except Exception:
        return False
