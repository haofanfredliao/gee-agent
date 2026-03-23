"""LLM 客户端：封装 Poe API（OpenAI 兼容接口）或占位实现。"""
import os
from pathlib import Path
from typing import Optional

import yaml

from backend.app.core.config import get_setting

try:
    import httpx
except ImportError:
    httpx = None

try:
    import openai
except ImportError:
    openai = None

POE_BASE_URL = "https://api.poe.com/v1"


def _get_model_config() -> dict:
    # backend/app/services/llm_client.py -> parents[4]=project root
    base = Path(__file__).resolve().parents[4]
    path = base / "configs" / "models.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("llm", {}) or data


def _resolve_model_id(model_name: Optional[str]) -> str:
    model_name = model_name or os.environ.get("DEFAULT_MODEL") or "default"
    config = _get_model_config()
    models = config.get("models", [config]) if isinstance(config.get("models"), list) else [config]
    for m in models:
        if m.get("name") == model_name:
            return m.get("model_id", "gpt-4")
        if m.get("name") == "default":
            default_id = m.get("model_id", "gpt-4")
    return models[0].get("model_id", "gpt-4") if models else "gpt-4"


async def chat_with_llm(prompt: str, model_name: Optional[str] = None) -> str:
    """
    调用 Poe API（OpenAI 兼容）生成回复。
    未配置 POE_API_KEY 或请求失败时返回占位说明。
    """
    model_id = _resolve_model_id(model_name)
    api_key = os.environ.get("POE_API_KEY")
    if not api_key:
        return (
            f"[占位] 已收到您的问题。配置 POE_API_KEY 与 configs/models.yaml 后可接入真实 LLM。\n\n"
            f"您的问题：{prompt[:200]}..."
        )

    if not openai:
        return f"[占位] 请安装 openai (pip install openai) 后使用 Poe API。\n\n您的问题：{prompt[:300]}"

    try:
        client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=POE_BASE_URL,
        )

        chat = await client.chat.completions.create(
            model=model_id,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        text = chat.choices[0].message.content.strip()
        return text or "[模型返回为空]"
    except Exception as e:
        return f"[Poe API 异常] {type(e).__name__}: {e}"
