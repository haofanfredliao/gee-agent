"""前端调用后端 API 的客户端。"""
import os
from typing import Any, Dict, Optional

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


def geo_resolve(place_name: str) -> Dict[str, Any]:
    """地名解析，返回 center_lat, center_lon, bbox。"""
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(_url("/geo/resolve"), json={"place_name": place_name})
        r.raise_for_status()
        return r.json()


def run_gee_task(task_type: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """执行 GEE 任务：load_asset 或 ndvi_example。"""
    payload = {"task_type": task_type, "params": params or {}}
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(_url("/gee/run"), json=payload)
        r.raise_for_status()
        return r.json()


def get_basemap_config() -> Dict[str, Any]:
    """获取底图配置：优先调用 GET /gee/basemap。"""
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(_url("/gee/basemap"))
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {"center_lat": 22.3193, "center_lon": 114.1694, "zoom": 10}
