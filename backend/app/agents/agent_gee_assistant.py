"""GEE 助手简易 Agent：路由到地理/GEE/知识库并组合 ChatResponse。"""
import re
from typing import Optional

from backend.app.models.chat import ChatResponse, MapUpdate
from backend.app.agents.tools_geo import geo_lookup
from backend.app.agents.tools_gee import gee_load_simple_asset, gee_run_ndvi_example
from backend.app.agents.tools_kb import kb_search
from backend.app.rag.chains import run_rag
from backend.app.rag.prompts import SYSTEM_PROMPT_GEE_ASSISTANT
from backend.app.services import llm_client
from backend.app.services.gee_client import execute_gee_code_simple
from backend.app.core.config import DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON, DEFAULT_ZOOM

# 简单地名关键词（可扩展）
GEO_KEYWORDS = re.compile(
    r"香港|九龙|北京|上海|hong\s*kong|kowloon|beijing|shanghai|地名|定位|哪里|区域"
)
NDVI_KEYWORDS = re.compile(r"ndvi|植被|归一化.*指数")
ASSET_KEYWORDS = re.compile(r"asset|srtm|高程|dem|加载.*数据|load.*asset")


async def run_gee_agent(query: str) -> ChatResponse:
    """
    简易 agent：根据 query 决定是否调用 geo_lookup / GEE tools / kb_search，
    再调用 LLM 生成回复，必要时填充 map_update。
    """
    query = (query or "").strip()
    map_update: Optional[MapUpdate] = None
    extra_context = ""

    geo_result = None
    # 1) 地名 -> 定位
    if GEO_KEYWORDS.search(query.lower()):
        place = _extract_place(query)
        if place:
            geo_result = geo_lookup(place)
            extra_context += f"\n[地理] 地点「{place}」：中心 ({geo_result['center_lat']}, {geo_result['center_lon']})，bbox={geo_result['bbox']}"
            map_update = MapUpdate(
                center_lat=geo_result["center_lat"],
                center_lon=geo_result["center_lon"],
                zoom=11,
                layer_info=None,
            )

    # 2) NDVI 示例
    if NDVI_KEYWORDS.search(query.lower()):
        bbox = (geo_result or {}).get("bbox") or [114.15, 22.28, 114.25, 22.35]
        start_date, end_date = "2020-01-01", "2022-12-31"
        ndvi_result = gee_run_ndvi_example(bbox, start_date, end_date)
        extra_context += f"\n[NDVI 结果] {ndvi_result}"
        if ndvi_result.get("tile_url") and map_update:
            map_update.layer_info = {"tile_url": ndvi_result["tile_url"]}
        elif not map_update and ndvi_result.get("status") == "ok":
            map_update = MapUpdate(
                center_lat=22.312,
                center_lon=114.174,
                zoom=11,
                layer_info={"tile_url": ndvi_result.get("tile_url")} if ndvi_result.get("tile_url") else None,
            )

    # 3) 加载 Asset（如 SRTM）
    if ASSET_KEYWORDS.search(query.lower()):
        asset_id = "USGS/SRTMGL1_003"
        load_result = gee_load_simple_asset(asset_id)
        extra_context += f"\n[加载 Asset] {load_result}"
        if load_result.get("tile_url") and map_update:
            map_update.layer_info = map_update.layer_info or {}
            map_update.layer_info["tile_url"] = load_result["tile_url"]
        elif not map_update and load_result.get("status") == "ok":
            map_update = MapUpdate(
                center_lat=DEFAULT_CENTER_LAT,
                center_lon=DEFAULT_CENTER_LON,
                zoom=DEFAULT_ZOOM,
                layer_info={"tile_url": load_result.get("tile_url")},
            )

    # 4) 知识库检索
    kb_text = kb_search(query, k=3)
    if kb_text and "（未找到相关文档）" not in kb_text:
        extra_context += f"\n[知识库]\n{kb_text}"

    # 5) 尝试判断是否需要代码执行
    needs_exec = bool(re.search(r"执行|可视化|explore|分析", query.lower()))
    if needs_exec:
        extra_context += "\n[附加要求] 用户要求执行代码或可视化分析。请务必提供可执行的 Python 代码段，并使用 `ee.FeatureCollection`, `ee.Image` 等，调用其 `.getInfo()` 来提取分析结果，并用 `print(...)` 打印。最后如果是空间数据，请使用类似 `Map.addLayer(obj, vis_params)` 进行展示。"

    # 6) 调用 RAG/LLM 生成回复
    if extra_context:
        prompt = f"""{SYSTEM_PROMPT_GEE_ASSISTANT}

以下是与当前请求相关的上下文（地理/任务结果/知识库）：
{extra_context}

用户问题：{query}

请结合上述上下文给出简洁、有用的回复。若涉及代码请给出可直接使用的片段。"""
        reply = await llm_client.chat_with_llm(prompt)
    else:
        reply = await run_rag(query)

    # 7) 自动提取并执行生成的代码
    if needs_exec:
        code_blocks = re.findall(r"```python(.*?)```", reply, re.DOTALL)
        if code_blocks:
            code = code_blocks[-1].strip()
            exec_res = execute_gee_code_simple(code)
            
            # Append exec results to reply
            reply += f"\n\n**自动执行结果**:\n```\n{exec_res['log']}\n```"
            if exec_res.get("tile_url"):
                if not map_update:
                    # Default coordinates roughly over Hong Kong if no specifics previously matched
                    map_update = MapUpdate(
                        center_lat=22.312 if "hong" in query.lower() or "香港" in query else DEFAULT_CENTER_LAT,
                        center_lon=114.174 if "hong" in query.lower() or "香港" in query else DEFAULT_CENTER_LON,
                        zoom=10 if "hong" in query.lower() or "香港" in query else DEFAULT_ZOOM,
                        layer_info=None
                    )
                map_update.layer_info = {"tile_url": exec_res["tile_url"]}

    return ChatResponse(reply=reply, map_update=map_update)


def _extract_place(query: str) -> Optional[str]:
    """从 query 中简单抽取地名。"""
    q = query.lower()
    for name in ["香港", "九龙", "北京", "上海", "hong kong", "kowloon", "beijing", "shanghai"]:
        if name in q:
            return name
    return None
