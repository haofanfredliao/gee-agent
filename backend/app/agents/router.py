"""意图识别路由器。

判断用户请求属于以下类别之一：
  - "execution"  : 需要加载数据、执行分析、可视化或运行 GEE 代码
  - "knowledge"  : 仅需要概念解释、文档说明或知识性问答
  - "geo_query"  : 仅需要定位一个地名并更新地图中心（不涉及 GEE 分析）

路由结果决定 orchestrator 进入哪条处理分支。
"""
import re
from typing import Literal

from backend.app.services import llm_client

# ─── Prompts ────────────────────────────────────────────────────────────────

_ROUTER_PROMPT = """\
你是 Google Earth Engine (GEE) 助手的意图分类器。
请判断用户的请求属于哪一类：

- execution ：用户需要加载数据、运行分析、可视化结果或执行 GEE 代码
- knowledge ：用户只需要概念解释、API 文档说明或知识性问答
- geo_query ：用户只需要定位某个地名、查看某个地方在哪里，不涉及 GEE 数据分析

只输出一个词：execution、knowledge 或 geo_query。不要输出其他任何内容。

用户请求：{query}"""

# ─── 关键词回退（LLM 不可用时使用） ──────────────────────────────────────────

_GEO_PATTERN = re.compile(
    r"在哪|在哪里|定位|跳转|导航到|show.*on\s+map|where is|locate|go to"
    r"|地图.*显示|显示.*地图|地图.*跳|跳.*地图",
    re.IGNORECASE,
)

_EXEC_PATTERN = re.compile(
    r"load|加载|execute|执行|visualize|可视化|explore|分析|compute|计算|plot"
    r"|show\b|显示|map\b|地图|asset\b|dataset|数据集|run\b|运行"
    r"|count\b|计数|area\b|面积|boundary|边界|vector|raster|image\b"
    r"|ndvi|elevation|高程|classify|分类|export|导出",
    re.IGNORECASE,
)

IntentType = Literal["execution", "knowledge", "geo_query"]


async def classify_intent(query: str) -> IntentType:
    """通过 LLM 分类意图，以关键词匹配作为回退策略。

    Returns
    -------
    "execution" | "knowledge" | "geo_query"
    """
    prompt = _ROUTER_PROMPT.format(query=query)
    raw = (await llm_client.chat_with_llm(prompt)).strip().lower()

    if "geo_query" in raw:
        return "geo_query"
    if "execution" in raw:
        return "execution"
    if "knowledge" in raw:
        return "knowledge"

    # LLM 返回不确定时按关键词判断
    if _GEO_PATTERN.search(query):
        return "geo_query"
    return "execution" if _EXEC_PATTERN.search(query) else "knowledge"
