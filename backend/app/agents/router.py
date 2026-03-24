"""意图识别路由器。

判断用户请求属于以下类别之一：
  - "execution" : 需要加载数据、执行分析、可视化或运行 GEE 代码
  - "knowledge"  : 仅需要概念解释、文档说明或知识性问答

路由结果决定 orchestrator 进入哪条处理分支。
"""
import re
from typing import Literal

from backend.app.services import llm_client

# ─── Prompts ────────────────────────────────────────────────────────────────

_ROUTER_PROMPT = """\
你是 Google Earth Engine (GEE) 助手的意图分类器。
请判断用户的请求属于哪一类：

- execution：用户需要加载数据、运行分析、可视化结果或执行 GEE 代码
- knowledge：用户只需要概念解释、API 文档说明或知识性问答

只输出一个词：execution 或 knowledge。不要输出其他任何内容。

用户请求：{query}"""

# ─── 关键词回退（LLM 不可用时使用） ──────────────────────────────────────────

_EXEC_PATTERN = re.compile(
    r"load|加载|execute|执行|visualize|可视化|explore|分析|compute|计算|plot"
    r"|show\b|显示|map\b|地图|asset\b|dataset|数据集|run\b|运行"
    r"|count\b|计数|area\b|面积|boundary|边界|vector|raster|image\b"
    r"|ndvi|elevation|高程|classify|分类|export|导出",
    re.IGNORECASE,
)

IntentType = Literal["execution", "knowledge"]


async def classify_intent(query: str) -> IntentType:
    """
    通过 LLM 分类意图，以关键词匹配作为回退策略。

    Returns
    -------
    "execution" 或 "knowledge"
    """
    prompt = _ROUTER_PROMPT.format(query=query)
    raw = (await llm_client.chat_with_llm(prompt)).strip().lower()

    if "execution" in raw:
        return "execution"
    if "knowledge" in raw:
        return "knowledge"

    # LLM 返回不确定时按关键词判断
    return "execution" if _EXEC_PATTERN.search(query) else "knowledge"
