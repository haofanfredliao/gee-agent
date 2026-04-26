"""Orchestrator：核心工作流状态机。

实现观察-思考-执行（Observe-Think-Act）循环：
  1. routing    — 通过 router 识别用户意图
  2. planning   — LLM 将 query 拆解为有序子步骤
  3. executing  — 逐步执行：inspect（观察）→ 更新 context → execute（行动）
  4. summarizing — LLM 汇总所有步骤输出，生成最终回答
  5. terminated  — 返回 ChatResponse

对外接口：
  run_workflow(query, session_id)   -> ChatResponse        （一次性返回）
  stream_workflow(query, session_id) -> AsyncGenerator[str] （SSE 流式事件）

流式事件格式（每行一个 JSON，以 \\n 结尾）：
  {"type": "routing",    "data": {"intent": "execution"}}
  {"type": "planning",   "data": {"plan": [{"description":..., "type":...}, ...]}}
  {"type": "step_start", "data": {"index": 0, "description": "...", "tool": "..."}}
  {"type": "step_done",  "data": {"index": 0, "description": "...", "tool": "...",
                                  "success": true, "output_preview": "..."}}
  {"type": "summarizing","data": {}}
  {"type": "done",       "data": {"reply": "...", "map_update": {...}}}
  {"type": "error",      "data": {"message": "..."}}
"""
import ast
import json
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.app.agents.state import WorkflowState, StepResult, make_initial_state, format_status
from backend.app.agents.session_store import load_session_context, save_session_state
from backend.app.agents.router import classify_intent
from backend.app.tools.explanation.asset_inspector import inspect_asset
from backend.app.tools.explanation.kb_lookup import knowledge_base_lookup
from backend.app.tools.execution.gee_executor import execute_gee_snippet
from backend.app.tools.geo.geocoder import resolve_place
from backend.app.tools.geo.osm_boundary import AmbiguousBoundaryError, resolve_osm_boundary
from backend.app.agents.prompts import (
    PLANNER_PROMPT,
    CODE_GEN_PROMPT,
    CODE_REPAIR_PROMPT,
    SUMMARIZE_PROMPT,
    KNOWLEDGE_PROMPT,
    GEO_REPLY_PROMPT,
)
from backend.app.models.chat import ChatResponse, MapUpdate, WorkflowStatus
from backend.app.services import llm_client
from backend.app.services.log_store import write_log
from backend.app.core.config import DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON, DEFAULT_ZOOM

# ─── 辅助函数 ────────────────────────────────────────────────────────────────

_ASSET_PATH_RE = re.compile(r"projects/[\w\-]+/assets/[\w\-/]+")
ASSET_ID_NORMALIZATION_MAP = {
    "COPERNICUS/S2_SR": "COPERNICUS/S2_SR_HARMONIZED",
    # Guard against over-replacement bug that produced duplicated suffix.
    "COPERNICUS/S2_SR_HARMONIZED_HARMONIZED": "COPERNICUS/S2_SR_HARMONIZED",
}
S2_SR_EXACT_TOKEN_RE = re.compile(r"(?<![A-Z0-9_])COPERNICUS/S2_SR(?![A-Z0-9_])")
FORCE_IMAGE_COLLECTION_IDS = (
    "COPERNICUS/S2_SR_HARMONIZED",
    "COPERNICUS/S2_CLOUD_PROBABILITY",
)


def _extract_asset_ids(text: str) -> List[str]:
    """从文本中提取所有 GEE asset 路径（projects/…/assets/…）。"""
    return _ASSET_PATH_RE.findall(text)


def _normalize_asset_id(asset_id: Optional[str]) -> Optional[str]:
    if not asset_id:
        return asset_id
    normalized = asset_id.strip()
    # Resolve chained aliases deterministically.
    for _ in range(3):
        nxt = ASSET_ID_NORMALIZATION_MAP.get(normalized)
        if not nxt or nxt == normalized:
            break
        normalized = nxt
    return normalized


def _normalize_plan_asset_ids(plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for step in plan:
        if not isinstance(step, dict):
            continue
        raw_asset_id = step.get("asset_id")
        if not isinstance(raw_asset_id, str):
            continue
        normalized = _normalize_asset_id(raw_asset_id.strip())
        if normalized and normalized != raw_asset_id:
            step["asset_id"] = normalized
            desc = step.get("description")
            if isinstance(desc, str) and raw_asset_id in desc:
                step["description"] = desc.replace(raw_asset_id, normalized)
    return plan


def _normalize_code_asset_ids(code: str) -> str:
    normalized = code
    # Fix already-corrupted IDs first.
    normalized = normalized.replace(
        "COPERNICUS/S2_SR_HARMONIZED_HARMONIZED",
        "COPERNICUS/S2_SR_HARMONIZED",
    )
    # Replace only exact deprecated token; avoid touching *_HARMONIZED.
    normalized = S2_SR_EXACT_TOKEN_RE.sub("COPERNICUS/S2_SR_HARMONIZED", normalized)
    # Guardrail: these known datasets are ImageCollection, never FeatureCollection.
    for dataset_id in FORCE_IMAGE_COLLECTION_IDS:
        pattern = re.compile(
            rf"ee\.FeatureCollection\(\s*([\"']){re.escape(dataset_id)}\1\s*\)"
        )
        normalized = pattern.sub(
            lambda m, did=dataset_id: f"ee.ImageCollection({m.group(1)}{did}{m.group(1)})",
            normalized,
        )
    return normalized


def _remove_projection_forcing(code: str) -> str:
    """Remove display-time projection forcing from generated GEE Python code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    class ProjectionCallRemover(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            self.generic_visit(node)
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"reproject", "setDefaultProjection"}:
                return func.value
            return node

    new_tree = ProjectionCallRemover().visit(tree)
    ast.fix_missing_locations(new_tree)
    try:
        return ast.unparse(new_tree)
    except Exception:
        return code


def _autofix_common_code_errors(code: str, error_log: str) -> str:
    """
    Try deterministic one-shot fixes before invoking another LLM repair round.
    This improves latency for recurrent, well-known type mistakes.
    """
    lowered = (error_log or "").lower()
    if "featurecollection" in lowered and "mosaic" in lowered:
        return _normalize_code_asset_ids(code)
    if "staticprojectionerror" in lowered:
        return _remove_projection_forcing(code)
    return code


def _query_asks_for_ndvi(query: str) -> bool:
    return "ndvi" in (query or "").lower()


def _query_asks_for_spectral_index(query: str) -> bool:
    q = (query or "").lower()
    terms = (
        "ndvi",
        "ndbi",
        "ndwi",
        "mndwi",
        "ndmi",
        "nbr",
        "nbr2",
        "evi",
        "savi",
        "msavi",
        "bsi",
        "ndsi",
        "gndvi",
        "ndre",
        "reci",
        "ci_re",
        "lai",
        "光谱指数",
        "植被指数",
        "水体指数",
        "建筑指数",
        "建成区指数",
        "裸土指数",
        "火烧指数",
        "叶面积指数",
    )
    return any(term in q for term in terms)


def _query_asks_for_same_day_mosaic(query: str) -> bool:
    q = (query or "").lower()
    same_day_terms = (
        "同一天",
        "当天",
        "单日",
        "同日",
        "某一天",
        "一天",
        "云量最低那一天",
        "same day",
        "single day",
        "lowest cloud day",
        "daily mosaic",
    )
    return any(term in q for term in same_day_terms)


def _query_asks_for_cloudless_composite(query: str) -> bool:
    q = (query or "").lower()
    cloudless_terms = ("最少云", "少云", "无云", "尽量无云", "cloudless", "least cloud", "minimum cloud")
    return (not _query_asks_for_same_day_mosaic(q)) and any(term in q for term in cloudless_terms)


def _query_expects_map_layer(query: str) -> bool:
    q = (query or "").lower()
    no_map_terms = (
        "不显示",
        "不用显示",
        "不要地图",
        "不需要地图",
        "只回答",
        "只统计",
        "仅统计",
        "平均值",
        "最大值",
        "最小值",
        "mean ndvi",
        "average ndvi",
    )
    if any(term in q for term in no_map_terms):
        return False
    map_terms = (
        "ndvi",
        "ndbi",
        "ndwi",
        "mndwi",
        "ndmi",
        "nbr",
        "nbr2",
        "evi",
        "savi",
        "msavi",
        "bsi",
        "ndsi",
        "gndvi",
        "ndre",
        "lai",
        "光谱指数",
        "植被指数",
        "水体指数",
        "建筑指数",
        "叶面积指数",
        "mosaic",
        "真彩色",
        "遥感影像",
        "遥感",
        "影像",
        "remote sensing",
        "remotesensing",
        "imagery",
        "图层",
        "可视化",
        "显示",
        "map",
        "visualize",
    )
    return any(term in q for term in map_terms)


def _query_asks_for_imagery_product(query: str) -> bool:
    q = (query or "").lower()
    terms = (
        "mosaic",
        "真彩色",
        "遥感影像",
        "遥感",
        "影像",
        "remote sensing",
        "remotesensing",
        "imagery",
        "image",
        "least cloud",
        "cloudless",
        "最少云",
        "少云",
        "无云",
    )
    return any(term in q for term in terms)


def _extract_query_years(query: str) -> List[str]:
    years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", query or "")
    return list(dict.fromkeys(years))


def _extract_dataset_hints(query: str) -> List[str]:
    q = (query or "").lower()
    hints: List[str] = []
    if re.search(r"sentinel\s*-?\s*2|\bs2\b|哨兵\s*2|哨兵二", q, flags=re.IGNORECASE):
        hints.append("Sentinel-2 / COPERNICUS/S2_SR_HARMONIZED")
    if re.search(r"landsat\s*8|\bl8\b", q, flags=re.IGNORECASE):
        hints.append("Landsat 8 Collection 2 Level 2")
    if re.search(r"landsat\s*9|\bl9\b", q, flags=re.IGNORECASE):
        hints.append("Landsat 9 Collection 2 Level 2")
    if "modis" in q:
        hints.append("MODIS")
    return hints


def _extract_index_hints(query: str) -> List[str]:
    q = (query or "").upper()
    candidates = ("MNDWI", "NDWI", "NDBI", "NDMI", "NBR2", "NBR", "NDVI", "EVI", "SAVI", "MSAVI", "BSI", "NDSI", "GNDVI", "NDRE", "RECI", "CI_RE", "LAI")
    found = [idx for idx in candidates if idx in q]
    return list(dict.fromkeys(found))


def _build_query_slots_section(query: str) -> str:
    """Create deterministic query slots so planning/codegen do not rely only on LLM guessing."""
    place = _extract_aoi_place_name(query)
    years = _extract_query_years(query)
    datasets = _extract_dataset_hints(query)
    indices = _extract_index_hints(query)
    wants_imagery = _query_asks_for_imagery_product(query)

    lines: List[str] = []
    if place:
        lines.append(f"AOI={place}")
    if years:
        lines.append(f"年份/时间={', '.join(years)}")
    if datasets:
        lines.append(f"数据源={', '.join(datasets)}")
    if indices:
        lines.append(f"光谱指数={', '.join(indices)}")
    if wants_imagery:
        lines.append("基础影像=需要生成/显示遥感影像或 mosaic")
    if wants_imagery and indices:
        lines.append("任务类型=复合任务，必须同时生成基础遥感影像和用户点名的指数图层")

    if not lines:
        return ""
    return "结构化请求槽位（后端确定性解析，优先于自由猜测）： " + "；".join(lines)


_AOI_PLACE_PATTERNS = (
    re.compile(
        r"(?:AOI|行政区名称|行政区|研究区|区域|范围)\s*(?:是|为|:|：)\s*"
        r"(?P<place>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s,.'’\-·]{0,30})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:计算|显示|可视化)\s*"
        r"(?P<place>[\u4e00-\u9fff]{2,12})(?:市|省|区|县|特别行政区)?"
        r"\s*的\s*(?:NDVI|ndvi|遥感影像|真彩色|mosaic)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:给|为|针对|对|在)\s*"
        r"(?P<place>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s,.'’\-·]{0,60}?)"
        r"\s*(?:做|制作|生成|提取|获取|计算|创建|显示|可视化|范围|区域|的)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:请你|请|麻烦|帮我|帮忙|给我)?\s*"
        r"(?:生成|制作|提取|获取|计算|显示|可视化|做)\s*"
        r"(?P<place>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s,.'’\-·]{1,60}?)"
        r"(?=\s*(?:\d{4}|年|的|Sentinel|S2|Landsat|MODIS|遥感|影像|remote|image|imagery|mosaic|NDVI|NDBI|NDWI|LAI))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:over|for|in|around)\s+"
        r"(?P<place>[A-Za-z][A-Za-z\s,.'’\-]{1,60}?)"
        r"(?:\s+(?:using|with|from|during|make|create|generate|show|sentinel|landsat|\d{4})|$)",
        re.IGNORECASE,
    ),
)


_KNOWN_AOI_NAMES = (
    "香港特别行政区",
    "香港",
    "Hong Kong",
    "广州市",
    "广州",
    "Guangzhou",
    "深圳市",
    "深圳",
    "Shenzhen",
    "北京市",
    "北京",
    "Beijing",
    "上海市",
    "上海",
    "Shanghai",
)


def _extract_known_aoi_name(query: str) -> Optional[str]:
    """Fast path for common class/demo AOIs and cached aliases."""
    text = query or ""
    lowered = text.lower()
    for name in sorted(_KNOWN_AOI_NAMES, key=len, reverse=True):
        if (name.lower() if name.isascii() else name) in (lowered if name.isascii() else text):
            return name
    return None


def _clean_aoi_place_candidate(candidate: str) -> Optional[str]:
    cleaned = (candidate or "").strip(" ，,。；;:：的范围区域")
    cleaned = re.sub(
        r"^(?:那|那么)?\s*(?:请你|请|麻烦|帮我|帮忙|给我)?\s*"
        r"(?:用|使用|基于|提取|获取|生成|制作|计算|显示|可视化|做)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.split(r"[，,。；;:：\n]", cleaned, maxsplit=1)[0]
    cleaned = re.sub(r"\b(Sentinel-?2|S2|Landsat\s*[89]?|MODIS|GEE)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(remote\s*sensing|remotesensing|imagery|image|mosaic|true\s*color)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{4}\b.*$", "", cleaned).strip(" ，,。；;:：的范围区域")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) < 2:
        return None
    if len(cleaned) > 60:
        return None
    remote_sensing_only = {
        "sentinel",
        "sentinel-2",
        "s2",
        "landsat",
        "mosaic",
        "ndvi",
        "true color",
        "remote sensing",
        "remotesensing",
        "imagery",
        "image",
        "真彩色",
        "遥感影像",
        "遥感",
        "影像",
    }
    if cleaned.lower() in remote_sensing_only:
        return None
    if any(term in cleaned for term in ("最少云", "少云", "无云", "遥感", "影像", "真彩色")):
        return None
    # Follow-up phrases are context references, not place names.
    if any(term in cleaned for term in ("刚刚", "刚才", "根据", "帮我", "这个", "这张", "上一", "上次", "之前")):
        return None
    return cleaned


def _extract_aoi_place_name(query: str) -> Optional[str]:
    """Best-effort AOI place extraction for execution tasks.

    This intentionally stays conservative. If no clear place is found, the
    normal LLM/code path proceeds without automatic OSM boundary preparation.
    """
    text = query or ""
    known = _extract_known_aoi_name(text)
    if known:
        return known

    for pattern in _AOI_PLACE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        place = _clean_aoi_place_candidate(match.group("place"))
        if place:
            return place

    # Common Chinese shorthand: "广州2022年最少云遥感影像".
    fallback_with_year = re.search(
        r"(?P<place>[\u4e00-\u9fff]{2,12})(?:市|省|区|县|特别行政区)?"
        r"\s*\d{4}\s*年?.{0,40}(?:遥感影像|遥感|影像|真彩色|最少云|少云|无云|remote\s*sensing|remotesensing|imagery|image|mosaic|NDVI|ndvi|NDBI|ndbi|LAI|lai)",
        text,
        flags=re.IGNORECASE,
    )
    if fallback_with_year:
        return _clean_aoi_place_candidate(fallback_with_year.group("place"))
    # Common Chinese shorthand: "深圳的遥感影像".
    fallback = re.search(
        r"(?P<place>[\u4e00-\u9fff]{2,12})(?:市|省|区|县|特别行政区)?"
        r"(?:的)?(?:遥感影像|遥感|影像|真彩色|最少云|少云|无云|remote\s*sensing|remotesensing|imagery|image|mosaic|NDVI|ndvi|NDBI|ndbi|LAI|lai)",
        text,
        flags=re.IGNORECASE,
    )
    if fallback:
        return _clean_aoi_place_candidate(fallback.group("place"))
    return None


def _query_refers_to_previous_product(query: str) -> bool:
    q = query or ""
    terms = (
        "刚刚",
        "刚才",
        "这个",
        "这张",
        "上一张",
        "上一次",
        "上次",
        "之前",
        "根据这个",
        "根据刚刚",
        "刚刚生成",
        "这张图",
        "this image",
        "previous image",
        "last image",
    )
    return any(term in q.lower() for term in terms)


def _format_ambiguous_boundary_reply(place_name: str, options: List[Dict[str, Any]]) -> str:
    lines = [
        f"我找到了多个可能的「{place_name}」边界，暂时不继续执行，避免把影像裁到错误城市。",
        "请你把地名说得更具体一点，例如加国家/省份/地区；也可以直接回复想用哪一个完整名称。",
    ]
    for i, option in enumerate(options[:5], start=1):
        display = option.get("display_name") or option.get("name") or "未知边界"
        osm_ref = f"{option.get('osm_type')} {option.get('osm_id')}"
        lines.append(f"{i}. {display} [{osm_ref}]")
    return "\n".join(lines)


def _map_context_from_aoi_boundary(context: Dict[str, Any]) -> Dict[str, Any]:
    aoi_boundary = context.get("aoi_boundary") or {}
    bbox = aoi_boundary.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return {}
    try:
        min_lon, min_lat, max_lon, max_lat = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return {}
    lon_span = abs(max_lon - min_lon)
    zoom = 12 if lon_span < 0.1 else (10 if lon_span < 0.5 else (8 if lon_span < 2 else 6))
    return {
        "center_lat": (min_lat + max_lat) / 2,
        "center_lon": (min_lon + max_lon) / 2,
        "zoom": zoom,
        "bbox": [min_lon, min_lat, max_lon, max_lat],
    }


def _extract_logged_value(output: str, label: str) -> Optional[str]:
    pattern = re.compile(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$")
    match = pattern.search(output or "")
    return match.group(1).strip() if match else None


def _capture_gee_product_context(
    *,
    query: str,
    exec_result: Dict[str, Any],
    code: str,
) -> Optional[Dict[str, Any]]:
    """Capture enough metadata for follow-up tasks like "compute NDVI from it"."""
    layers = exec_result.get("layers") or []
    if exec_result.get("status") != "ok" or not layers:
        return None

    output = exec_result.get("log", "") or ""
    product_type = "spectral_index" if _query_asks_for_spectral_index(query) else "imagery"
    metadata = {
        "product_type": product_type,
        "dataset": _extract_logged_value(output, "Dataset"),
        "selected_date": _extract_logged_value(output, "Selected date"),
        "image_ids": _extract_logged_value(output, "Image IDs") or _extract_logged_value(output, "Source image IDs"),
        "boundary_source": _extract_logged_value(output, "Boundary source"),
        "boundary_names": _extract_logged_value(output, "Boundary names"),
        "layer_names": [layer.get("name") for layer in layers if layer.get("name")],
        "output_preview": output[:2000],
        "code_preview": code[:4000],
    }
    return {k: v for k, v in metadata.items() if v not in (None, "", [])}


def _prepare_aoi_boundary_context(state: WorkflowState) -> Optional[str]:
    """Resolve a place AOI to cached OSM boundary metadata before codegen.

    Returns a user-facing blocking message when the boundary is ambiguous or
    unavailable. Returns None when execution can continue.
    """
    # If the user explicitly supplied a private GEE asset, keep that as the
    # preferred boundary/data source and do not second-guess it with OSM.
    if _extract_asset_ids(state["query"]):
        return None

    existing = state["context"].get("aoi_boundary") or {}
    place_name = _extract_aoi_place_name(state["query"])
    if not place_name:
        if existing:
            # Follow-up turns like "根据刚刚生成的影像计算 NDVI" should reuse
            # the previous AOI instead of treating the sentence prefix as a
            # new place name.
            return None
        if _query_refers_to_previous_product(state["query"]):
            return (
                "我理解你想基于上一张影像继续分析，但当前会话里没有可复用的 AOI 边界。"
                "请先生成一张影像，或在问题里明确行政区名称，例如「广州」。"
            )
        if _query_asks_for_spectral_index(state["query"]):
            return (
                "我可以计算光谱指数，但当前请求里没有明确 AOI，也没有可复用的上一轮影像边界。"
                "请补充行政区或范围，例如「广州 2022 年 Sentinel-2 NDVI」或「深圳 2022 年 NDBI」。"
            )
        return None

    existing_place = str(existing.get("place_name") or "").strip().lower()
    if existing and existing_place == place_name.strip().lower():
        return None

    try:
        resolved = resolve_osm_boundary(place_name)
    except AmbiguousBoundaryError as exc:
        state["context"]["aoi_boundary_candidates"] = {
            "place_name": exc.place_name,
            "options": exc.options,
        }
        return _format_ambiguous_boundary_reply(exc.place_name, exc.options)
    except Exception as exc:
        return (
            f"我识别到 AOI 是「{place_name}」，但暂时无法从 OpenStreetMap 获取可用边界：{exc}\n"
            "请稍后重试，或把地名写得更具体一些，例如「深圳市, 广东省, 中国」。"
        )

    if resolved.get("status") != "ok":
        return (
            f"我识别到 AOI 是「{place_name}」，但没有获取到 Polygon/MultiPolygon 边界："
            f"{resolved.get('message', '未知错误')}\n"
            "请换一个更明确的行政区名称，或提供 GEE FeatureCollection asset。"
        )

    state["context"]["aoi_boundary"] = resolved
    return None


def _static_code_sanity_error(code: str, query: str = "") -> Optional[str]:
    """
    Fast pre-exec sanity guard for common GEE type mistakes.
    Returns a synthetic error string when static pattern is clearly invalid.
    """
    image_only_methods = ("mosaic", "median", "qualityMosaic")
    method_group = "|".join(image_only_methods)

    # Direct call on a literal FeatureCollection(...)
    if re.search(
        rf"ee\.FeatureCollection\(\s*([\"']).+?\1\s*\)\s*\.\s*({method_group})\s*\(",
        code,
        flags=re.DOTALL,
    ):
        return (
            "StaticTypeError: ee.FeatureCollection(...) cannot call image-only methods "
            "(.mosaic/.median/.qualityMosaic)."
        )

    # Calls on variables previously assigned as FeatureCollection.
    feature_vars = set(
        re.findall(
            r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*ee\.FeatureCollection\(",
            code,
        )
    )
    for var_name in sorted(feature_vars):
        if re.search(rf"\b{re.escape(var_name)}\s*\.\s*({method_group})\s*\(", code):
            return (
                f"StaticTypeError: `{var_name}` is ee.FeatureCollection but used with "
                "an image-only method (.mosaic/.median/.qualityMosaic)."
            )

    # Sentinel-2 annual cloudless workflows can exceed memory if implemented with
    # full-year day-wise geometry traversal.
    if "COPERNICUS/S2_SR_HARMONIZED" in code:
        if re.search(r"\.(reproject|setDefaultProjection)\s*\(", code):
            return (
                "StaticProjectionError: do not force projection on Sentinel-2 mosaics for map display. "
                "Remove .reproject()/.setDefaultProjection() and let Earth Engine render map tiles."
            )

        uses_same_day_route = bool(
            "best_date" in code
            and re.search(r"\.filterDate\(\s*best_date\s*,\s*best_date\.advance\(", code)
        )
        uses_cloudless_route = bool(
            "mask_s2_sr_clouds" in code
            or re.search(r"\.median\(\)\s*\.clipToCollection", code)
        )
        if _query_asks_for_cloudless_composite(query) and uses_same_day_route and not uses_cloudless_route:
            return (
                "StaticProductModeError: the user asked for a least-cloud/cloudless annual product, "
                "not a same-day mosaic. Use SCL cloud masking and a multi-scene median composite."
            )
        if _query_asks_for_same_day_mosaic(query) and uses_cloudless_route and not uses_same_day_route:
            return (
                "StaticProductModeError: the user asked for a same-day lowest-cloud mosaic. "
                "Use the lowest-cloud date and mosaic scenes from that date only."
            )

        has_daily_map = bool(
            re.search(r"FeatureCollection\(\s*dates\s*\.map\s*\(", code)
            or re.search(r"aggregate_array\(\s*([\"'])date_str\1\s*\)\s*\.distinct\(\)", code)
        )
        has_geometry_coverage = bool(
            re.search(r"\.geometry\(\)\s*\.intersection\(\s*aoi\b", code)
            or re.search(r"coverage_pct", code)
        )
        # Escape hatch: when the code already bounds the candidate set via
        # `.sort(<cloud_property>).limit(...)`, the daily-map pattern is run on
        # a small subset (typically 30~40) which is memory-safe. This matches
        # the new few_shot_sentinel2_cloudless_mosaic.txt template — do not misfire.
        has_bounded_candidate_subset = bool(
            re.search(
                r"\.sort\(\s*(?:[\"'](?:CLOUDY_PIXEL_PERCENTAGE|CLOUD_COVER)[\"']|cloud_prop\w*)\s*\)\s*\.limit\(",
                code,
            )
        )
        if has_daily_map and has_geometry_coverage and not has_bounded_candidate_subset:
            return (
                "StaticPerfError: per-date map + geometry coverage traversal is applied on an unbounded "
                "collection (no `.sort(<cloud_property>).limit(...)` found before the daily map). "
                "Add `.sort('CLOUDY_PIXEL_PERCENTAGE').limit(30)` (or CLOUD_COVER for Landsat) to build "
                "a bounded candidate subset before the per-date aggregation."
            )

    # Intent guard: do not add NDVI unless the user asked for it.
    code_mentions_ndvi = bool(
        re.search(r"(?mi)^\s*ndvi\s*=", code)
        or re.search(r"normalizedDifference\(\s*\[\s*([\"'])B8\1\s*,\s*([\"'])B4\2\s*\]", code)
        or re.search(r"normalizedDifference\(\s*\[\s*([\"'])SR_B5\1\s*,\s*([\"'])SR_B4\2\s*\]", code)
        or re.search(r"Map\.addLayer\(\s*ndvi\b", code, flags=re.IGNORECASE)
    )
    if code_mentions_ndvi and not _query_asks_for_ndvi(query):
        return (
            "StaticIntentError: the user asked for a true-color mosaic, not NDVI. "
            "Remove all NDVI calculation and NDVI Map.addLayer calls."
        )

    # Hong Kong detailed-clip guard: avoid rectangle-only final AOI for HK tasks.
    mentions_hk = bool(re.search(r"hong\s*kong|香港", f"{query}\n{code}", flags=re.IGNORECASE))
    uses_bbox = ("Geometry.BBox(" in code) or ("Geometry.Rectangle(" in code)
    uses_osm_helper = ("load_aoi_boundary" in code) or ("osm_hk_boundary" in code)
    uses_lsib = "USDOS/LSIB/2017" in code
    uses_hk_boundary_fc = uses_osm_helper or uses_lsib or ("FAO/GAUL/2015/level0" in code)
    if mentions_hk and not (uses_osm_helper or uses_lsib):
        return (
            "StaticAOIError: Hong Kong task must use load_aoi_boundary() / osm_hk_boundary() first, "
            "or USDOS/LSIB/2017 as fallback with COUNTRY_NA matching."
        )
    if mentions_hk and uses_lsib and not uses_osm_helper:
        has_lsib_contains_fallback = (
            "stringContains" in code
            and "COUNTRY_NA" in code
            and "Hong" in code
        )
        has_nonempty_fallback = "ee.Algorithms.If" in code and uses_bbox
        direct_exact_geometry = bool(
            re.search(
                r"FeatureCollection\(\s*([\"'])USDOS/LSIB/2017\1\s*\)"
                r"[\s\S]{0,220}Filter\.eq\(\s*([\"'])COUNTRY_NA\2\s*,\s*([\"'])Hong Kong\3\s*\)"
                r"[\s\S]{0,120}\.geometry\(\)",
                code,
            )
        )
        if direct_exact_geometry or not (has_lsib_contains_fallback and has_nonempty_fallback):
            return (
                "StaticAOIError: LSIB Hong Kong boundary must be built defensively: "
                "first COUNTRY_NA == 'Hong Kong', then COUNTRY_NA contains 'Hong', then non-empty BBox fallback. "
                "Do not call .geometry() on a potentially empty exact-match collection."
            )
    if mentions_hk and uses_bbox and not uses_hk_boundary_fc:
        return (
            "StaticAOIError: Hong Kong task uses only BBox geometry. "
            "Use detailed HK boundary from USDOS/LSIB/2017 or FAO/GAUL/2015/level0 for final clip."
        )

    # NDVI rendering guard: NDVI layer must not use true-color visualization parameters.
    ndvi_layer_uses_rgb_bands = bool(
        re.search(r"Map\.addLayer\(\s*ndvi\b[\s\S]{0,220}bands[\s\S]{0,80}B4[\s\S]{0,40}B3[\s\S]{0,40}B2", code)
        or re.search(r"Map\.addLayer\(\s*ndvi\b[\s\S]{0,260}bands[\s\S]{0,100}SR_B4[\s\S]{0,60}SR_B3[\s\S]{0,60}SR_B2", code)
    )
    ndvi_layer_uses_reflectance_range = bool(
        re.search(r"Map\.addLayer\(\s*ndvi\b[\s\S]{0,220}min[\s\S]{0,20}[=:]\s*0[\s\S]{0,60}max[\s\S]{0,20}[=:]\s*(3000|10000)", code)
        or re.search(r"Map\.addLayer\(\s*ndvi\b[\s\S]{0,220}min[\s\S]{0,20}[=:]\s*0(?:\.0)?[\s\S]{0,60}max[\s\S]{0,20}[=:]\s*0\.3", code)
    )
    if ndvi_layer_uses_rgb_bands or ndvi_layer_uses_reflectance_range:
        return (
            "StaticVisError: NDVI layer is rendered with true-color/reflectance params. "
            "Use NDVI visualization (e.g., min=-0.2, max=0.8, NDVI palette)."
        )
    return None


def _execute_with_static_guard(
    code: str,
    query: str = "",
    *,
    aoi_boundary_path: Optional[str] = None,
) -> Dict[str, Any]:
    expects_map_layer = _query_expects_map_layer(query)
    if aoi_boundary_path and "load_aoi_boundary" not in code:
        return {
            "status": "error",
            "log": (
                "[前置本地静态检查拦截，未实际执行 GEE] "
                "StaticAOIError: the backend already prepared an AOI boundary cache for this task. "
                "Generated code must call load_aoi_boundary() and use image.clipToCollection(aoi_fc) "
                "instead of guessing a BBox or another boundary."
            ),
            "tile_url": None,
            "layers": [],
        }
    if expects_map_layer and "Map.addLayer" not in code:
        return {
            "status": "error",
            "log": (
                "[前置本地静态检查拦截，未实际执行 GEE] "
                "StaticVisualizationError: this imagery/NDVI request must add a map layer. "
                "Generated code must call Map.addLayer(image, vis_params, layer_name)."
            ),
            "tile_url": None,
            "layers": [],
        }
    static_error = _static_code_sanity_error(code, query=query)
    if static_error:
        # Prefix marker so downstream (repair LLM, summarize LLM) knows this is a
        # PRE-EXECUTION local guard hit, not a real Earth Engine runtime error.
        # Prevents the summarizer from hallucinating GEE-side advice like
        # "filter CLOUDY_PIXEL_PERCENTAGE < 10" when nothing reached GEE.
        prefixed = "[前置本地静态检查拦截，未实际执行 GEE] " + static_error
        return {"status": "error", "log": prefixed, "tile_url": None, "layers": []}
    result = execute_gee_snippet(code, aoi_boundary_path=aoi_boundary_path)
    if result.get("status") == "ok" and expects_map_layer and not (result.get("layers") or []):
        log = result.get("log", "")
        return {
            "status": "error",
            "log": (
                (log + "\n" if log else "")
                + "StaticVisualizationError: code executed but produced no map tile layer. "
                "Ensure Map.addLayer receives a valid ee.Image with correct visualization parameters."
            ),
            "tile_url": None,
            "layers": [],
        }
    return result


def _is_project_asset_id(asset_id: str) -> bool:
    return asset_id.startswith("projects/") and "/assets/" in asset_id


def _collect_allowed_project_assets(state: WorkflowState) -> set[str]:
    # Only assets explicitly provided by user/session are allowed for inspect.
    allowed: set[str] = set(_normalize_asset_id(a) or a for a in _extract_asset_ids(state["query"]))

    context_assets = (state.get("context") or {}).get("assets") or {}
    if isinstance(context_assets, dict):
        for aid in context_assets.keys():
            if isinstance(aid, str):
                norm = _normalize_asset_id(aid) or aid
                if _is_project_asset_id(norm):
                    allowed.add(norm)

    session_asset = (state.get("session_context") or {}).get("asset_id")
    if isinstance(session_asset, str):
        norm = _normalize_asset_id(session_asset) or session_asset
        if _is_project_asset_id(norm):
            allowed.add(norm)

    return allowed


def _sanitize_plan(state: WorkflowState, plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sanitize planner output to avoid hallucinated inspect steps:
    - inspect is only for explicit user/session project assets.
    - inspect on public datasets is skipped (low value, often slow).
    - execute with unknown private project asset has asset_id cleared.
    """
    allowed_project_assets = _collect_allowed_project_assets(state)
    sanitized: List[Dict[str, Any]] = []

    for step in plan:
        if not isinstance(step, dict):
            continue

        step_type = str(step.get("type", "execute")).strip().lower()
        if step_type not in {"inspect", "execute"}:
            step_type = "execute"
        step["type"] = step_type

        raw_asset_id = step.get("asset_id")
        asset_id: Optional[str] = None
        if isinstance(raw_asset_id, str) and raw_asset_id.strip():
            asset_id = _normalize_asset_id(raw_asset_id.strip())
        step["asset_id"] = asset_id

        if step_type == "inspect":
            # Inspect should only be used for explicit private assets.
            if not asset_id:
                continue
            if not _is_project_asset_id(asset_id):
                continue
            if asset_id not in allowed_project_assets:
                continue
            sanitized.append(step)
            continue

        # Execute step
        if asset_id and _is_project_asset_id(asset_id) and asset_id not in allowed_project_assets:
            # Prevent planner from dragging private assets from few-shot examples.
            step["asset_id"] = None
        sanitized.append(step)

    if not any(str(s.get("type", "execute")).lower() == "execute" for s in sanitized):
        sanitized.append(
            {
                "description": "执行 GEE 分析任务",
                "type": "execute",
                "asset_id": None,
            }
        )
    return sanitized


def _build_prev_steps_section(steps: List[StepResult]) -> str:
    """将已完成的 execute 步骤输出格式化为前序结果摘要，注入后续步骤的 prompt。"""
    execute_steps = [s for s in steps if s["tool"] == "gee_executor" and s["output"]]
    if not execute_steps:
        return ""
    lines = ["【前序执行步骤输出 — 在本步骤代码中可直接使用这些计算结果】"]
    for s in execute_steps:
        preview = s["output"][:800]  # 截断过长输出
        lines.append(f"  步骤 {s['step_index'] + 1}（{s['description']}）的输出：\n{preview}")
    return "\n\n".join(lines)


def _build_context_section(context: Dict[str, Any]) -> str:
    """将 state.context 格式化为 CODE_GEN_PROMPT 中的上下文描述段落。

    支持多 asset：context["assets"] 是以 asset_id 为 key 的字典，
    每个 value 包含该 asset 的元数据（property_names/bands/feature_count/geometry_type）。
    """
    assets: Dict[str, Any] = context.get("assets") or {}
    aoi_boundary: Dict[str, Any] = context.get("aoi_boundary") or {}
    last_product: Dict[str, Any] = context.get("last_gee_product") or {}
    if not assets and not aoi_boundary and not last_product:
        return ""

    lines: List[str] = ["已知数据上下文（必须优先使用）："]
    if aoi_boundary:
        lines.append("  AOI 边界：后端已解析并缓存 OpenStreetMap 边界")
        lines.append(f"    - 查询地名：{aoi_boundary.get('place_name')}")
        lines.append(f"    - OSM 名称：{aoi_boundary.get('display_name')}")
        lines.append(f"    - OSM ID：{aoi_boundary.get('osm_type')} {aoi_boundary.get('osm_id')}")
        lines.append(f"    - 本地缓存：{aoi_boundary.get('cache_path')}")
        lines.append("    - 代码中必须调用预注入 helper：aoi_fc = load_aoi_boundary()")
        lines.append("    - 用 aoi_fc.geometry() 做 filterBounds；最终显示裁剪用 image.clipToCollection(aoi_fc)")
        lines.append("    - 覆盖率/面积计算可用 aoi_fc.geometry().simplify(100) 后再 transform 到 EPSG:3857")

    if last_product:
        lines.append("  上一轮可复用遥感产品（用于“根据刚刚/这张/上一张影像”类追问）：")
        if last_product.get("product_type"):
            lines.append(f"    - 产品类型：{last_product.get('product_type')}")
        if last_product.get("dataset"):
            lines.append(f"    - Dataset：{last_product.get('dataset')}")
        if last_product.get("selected_date"):
            lines.append(f"    - Selected date：{last_product.get('selected_date')}")
        if last_product.get("image_ids"):
            lines.append(f"    - Image IDs：{last_product.get('image_ids')}")
        if last_product.get("layer_names"):
            lines.append(f"    - 图层名：{last_product.get('layer_names')}")
        if last_product.get("output_preview"):
            lines.append(f"    - 上一轮输出摘要：{last_product.get('output_preview')[:1200]}")
        lines.append("    - 若用户要求基于上一轮影像计算 NDVI，必须优先复用上述 Dataset/Image IDs/Selected date 重建影像。")

    if assets:
        lines.append("  已检查的 GEE asset 元数据：")
    for aid, meta in assets.items():
        lines.append(f"  Asset: {aid}")
        if meta.get("bands"):
            lines.append(f"    - 波段列表：{meta['bands']}")
        if meta.get("property_names"):
            lines.append(f"    - 属性字段（实际字段名）：{meta['property_names']}")
        if meta.get("feature_count") is not None:
            lines.append(f"    - 要素总数：{meta['feature_count']}")
        if meta.get("geometry_type"):
            lines.append(f"    - 几何类型：{meta['geometry_type']}")
    return "\n".join(lines)


def _build_session_section(state: WorkflowState) -> str:
    """将 session 级别上下文格式化为 prompt 中的对话历史/区域感知段落。"""
    parts: List[str] = []
    query_slots = _build_query_slots_section(state.get("query", ""))
    if query_slots:
        parts.append(query_slots)

    sc = state.get("session_context") or {}
    current_aoi = (state.get("context") or {}).get("aoi_boundary") or {}
    if current_aoi:
        parts.append(
            "当前任务 AOI 边界已解析："
            f"{current_aoi.get('place_name')} -> {current_aoi.get('display_name')} "
            f"({current_aoi.get('osm_type')} {current_aoi.get('osm_id')})"
        )

    map_ctx = sc.get("map_context") or {}
    if map_ctx.get("center_lat") and map_ctx.get("center_lon"):
        parts.append(
            f"当前地图区域：中心 ({map_ctx['center_lat']:.5f}, {map_ctx['center_lon']:.5f})，"
            f"缩放级别 {map_ctx.get('zoom', '未知')}"
        )

    last_q = sc.get("last_query")
    last_r = sc.get("last_reply")
    if last_q:
        parts.append(f"上一轮用户请求：{last_q[:120]}")
    if last_r:
        parts.append(f"上一轮助手回复摘要：{last_r[:200]}")

    asset_id = sc.get("asset_id")
    if asset_id:
        parts.append(f"上一轮使用的 Asset：{asset_id}")

    if not parts:
        return ""
    return "\n".join(["【会话上下文 — 可复用以下信息】"] + [f"  - {p}" for p in parts])


# ─── 规划阶段 ────────────────────────────────────────────────────────────────

async def _plan(state: WorkflowState) -> WorkflowState:
    """
    Planning 阶段：调用 LLM 将用户 query 拆解为结构化子步骤列表。

    LLM 返回 JSON 数组，每项含 description / type / asset_id。
    若解析失败，则根据 query 中是否含有 asset 路径生成默认计划。
    """
    state["status"] = "planning"
    session_section = _build_session_section(state)
    kb_hits = knowledge_base_lookup(state["query"], k=4)
    kb_section = (
        "【RAG 知识库参考 — 规划阶段请优先遵循这些数据集与实践】\n" + kb_hits
        if kb_hits and kb_hits != "（未找到相关文档）"
        else ""
    )
    raw = await llm_client.chat_with_llm(
        PLANNER_PROMPT.format(
            query=state["query"],
            session_section=session_section,
            kb_section=kb_section,
        )
    )

    plan: List[Dict[str, Any]] = []
    try:
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError):
        plan = []

    # 回退：按 query 中的 asset 路径自动生成默认计划（每个 asset 各一个 inspect）
    if not plan:
        asset_ids = _extract_asset_ids(state["query"])
        # 去重同时保持顺序
        seen: Dict[str, bool] = {}
        unique_asset_ids = [a for a in asset_ids if not seen.setdefault(a, False) and not seen.update({a: True})]
        if unique_asset_ids:
            plan = [
                {"description": f"检查 {a.split('/')[-1]} 元数据", "type": "inspect", "asset_id": a}
                for a in unique_asset_ids
            ] + [
                {
                    "description": "执行分析并可视化",
                    "type": "execute",
                    "asset_id": unique_asset_ids[0],
                }
            ]
        else:
            plan = [
                {
                    "description": "执行 GEE 分析任务",
                    "type": "execute",
                    "asset_id": None,
                }
            ]

    state["plan"] = _sanitize_plan(state, _normalize_plan_asset_ids(plan))
    return state


# ─── 单步执行 ────────────────────────────────────────────────────────────────

async def _execute_step(
    state: WorkflowState,
    step: Dict[str, Any],
    step_index: int,
) -> WorkflowState:
    """
    执行单个规划步骤。

    - type="inspect"  → 调用 asset_inspector（Observe），结果写入 state.context
    - type="execute"  → 调用 LLM 生成代码（Think），再调用 gee_executor（Act）
    """
    state["current_step"] = step_index
    step_type = step.get("type", "execute")
    description = step.get("description", f"步骤 {step_index + 1}")
    asset_id: Optional[str] = _normalize_asset_id(step.get("asset_id") or state["context"].get("asset_id"))

    result: StepResult = {
        "step_index": step_index,
        "description": description,
        "tool": "",
        "output": "",
        "tile_url": None,
        "code": None,
        "success": False,
    }

    # ── Observe：inspect 步骤 ──────────────────────────────────────────────
    if step_type == "inspect":
        result["tool"] = "asset_inspector"
        if asset_id:
            info = inspect_asset(asset_id)
            result["output"] = json.dumps(info, ensure_ascii=False, indent=2)
            result["success"] = info["status"] == "ok"
            if info["status"] == "ok":
                resolved_asset_id = str(info.get("asset_id") or asset_id)
                # 写入跨步骤共享 context（多 asset 结构）
                if "assets" not in state["context"]:
                    state["context"]["assets"] = {}
                state["context"]["assets"][resolved_asset_id] = {
                    "property_names": info.get("property_names", []),
                    "feature_count": info.get("feature_count"),
                    "geometry_type": info.get("geometry_type"),
                    "bands": info.get("bands", []),
                }
        else:
            result["output"] = "未提供 asset_id，跳过检查。"
            result["success"] = False

    # ── Think + Act：execute 步骤 ─────────────────────────────────────────
    elif step_type == "execute":
        result["tool"] = "gee_executor"

        # Think：LLM 生成代码，注入从 inspect 步骤获得的 context、前序步骤输出和 session context
        context_section = _build_context_section(state["context"])
        prev_steps_section = _build_prev_steps_section(state["steps"])
        session_section = _build_session_section(state)

        # RAG 检索：用步骤描述 + 用户总需求拼接查询，检索相关最佳实践
        rag_query = f"{description} {state['query']}"
        kb_hits = knowledge_base_lookup(rag_query, k=3)
        kb_section = (
            "【RAG 知识库参考 — 本任务相关最佳实践，优先遵循】\n" + kb_hits
            if kb_hits and kb_hits != "（未找到相关文档）"
            else ""
        )

        code_prompt = CODE_GEN_PROMPT.format(
            query=state["query"],
            step_description=description,
            context_section=context_section,
            kb_section=kb_section,
            prev_steps_section=prev_steps_section,
            session_section=session_section,
        )
        llm_response = await llm_client.chat_with_llm(code_prompt)

        # 提取代码块
        code_blocks = re.findall(r"```python(.*?)```", llm_response, re.DOTALL)
        if not code_blocks:
            code_blocks = re.findall(r"```(.*?)```", llm_response, re.DOTALL)

        if not code_blocks:
            result["output"] = f"[代码生成失败] LLM 原始响应：\n{llm_response[:400]}"
            result["success"] = False
        else:
            code = _normalize_code_asset_ids(code_blocks[-1].strip())
            aoi_boundary_path = (state["context"].get("aoi_boundary") or {}).get("cache_path")

            # Act：执行代码（含 repair 子循环，最多重试 3 次）
            MAX_REPAIR_ATTEMPTS = 3
            exec_result = _execute_with_static_guard(
                code,
                query=state["query"],
                aoi_boundary_path=aoi_boundary_path,
            )
            if exec_result["status"] != "ok":
                autofixed_code = _autofix_common_code_errors(code, exec_result.get("log", ""))
                if autofixed_code != code:
                    code = autofixed_code
                    exec_result = _execute_with_static_guard(
                        code,
                        query=state["query"],
                        aoi_boundary_path=aoi_boundary_path,
                    )
            for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
                if exec_result["status"] == "ok":
                    break
                error_log = exec_result.get("log", "")
                repair_prompt = CODE_REPAIR_PROMPT.format(
                        query=state["query"],
                        step_description=description,
                        context_section=context_section,
                        kb_section=kb_section,
                        prev_steps_section=prev_steps_section,
                        original_code=code,
                        error_log=error_log,
                        attempt=attempt,
                    )
                repair_response = await llm_client.chat_with_llm(repair_prompt)
                repaired_blocks = re.findall(r"```python(.*?)```", repair_response, re.DOTALL)
                if not repaired_blocks:
                    repaired_blocks = re.findall(r"```(.*?)```", repair_response, re.DOTALL)
                if not repaired_blocks:
                    break
                code = _normalize_code_asset_ids(repaired_blocks[-1].strip())
                exec_result = _execute_with_static_guard(
                    code,
                    query=state["query"],
                    aoi_boundary_path=aoi_boundary_path,
                )

            result["output"] = exec_result.get("log", "")
            result["tile_url"] = exec_result.get("tile_url")
            result["code"] = code
            result["success"] = exec_result["status"] == "ok"

            if result["success"]:
                last_product = _capture_gee_product_context(
                    query=state["query"],
                    exec_result=exec_result,
                    code=code,
                )
                if last_product:
                    state["context"]["last_gee_product"] = last_product

            # 更新地图 state：优先使用 session 中已知的地图中心
            all_layers = exec_result.get("layers") or []
            if all_layers:
                map_ctx = _map_context_from_aoi_boundary(state["context"])
                if not map_ctx:
                    map_ctx = (state.get("session_context") or {}).get("map_context") or {}
                center_lat = map_ctx.get("center_lat") or DEFAULT_CENTER_LAT
                center_lon = map_ctx.get("center_lon") or DEFAULT_CENTER_LON
                zoom = map_ctx.get("zoom") or DEFAULT_ZOOM
                state["map_update"] = {
                    "center_lat": center_lat,
                    "center_lon": center_lon,
                    "zoom": zoom,
                    "bbox": map_ctx.get("bbox"),
                    "layer_info": {"tile_url": exec_result.get("tile_url")},
                    "layers": all_layers,
                }

    state["steps"].append(result)
    return state


# ─── 汇总阶段 ────────────────────────────────────────────────────────────────

async def _summarize(state: WorkflowState) -> WorkflowState:
    """
    Summarizing 阶段：LLM 将所有步骤的输出汇总为最终自然语言回答。
    """
    state["status"] = "summarizing"

    steps_summary = "\n\n".join(
        f"**步骤 {s['step_index'] + 1}（{s['description']}）** [工具: {s['tool']}]：\n{s['output']}"
        for s in state["steps"]
    )

    prompt = SUMMARIZE_PROMPT.format(
        query=state["query"],
        steps_summary=steps_summary,
    )
    state["final_reply"] = await llm_client.chat_with_llm(prompt)
    state["status"] = "terminated"
    return state


async def _answer_knowledge(query: str) -> str:
    """知识问答：先检索知识库，再基于检索结果调用 LLM。"""
    kb_context = knowledge_base_lookup(query, k=4)
    prompt = KNOWLEDGE_PROMPT.format(
        query=query,
        kb_context=kb_context,
    )
    return await llm_client.chat_with_llm(prompt)


async def _handle_geo_query(
    query: str,
    session_id: str,
    existing_map_context: Optional[Dict[str, Any]],
) -> tuple[Optional[MapUpdate], str]:
    """地名定位分支：调用 geocoder tool，生成 map_update 和自然语言回复。

    Returns (map_update, reply_text)
    """
    # 从 query 中简单提取地名（取最后一个中/英名词段）
    place = query.strip()
    geo = resolve_place(place)
    if geo["status"] != "ok":
        reply = f"抱歉，无法定位「{place}」，请检查地名是否正确。"
        return None, reply

    map_update = MapUpdate(
        center_lat=geo["center_lat"],
        center_lon=geo["center_lon"],
        zoom=geo["zoom"],
        bbox=geo.get("bbox"),
        layer_info=None,
    )
    new_map_ctx = {
        "center_lat": geo["center_lat"],
        "center_lon": geo["center_lon"],
        "zoom": geo["zoom"],
        "bbox": geo.get("bbox"),
    }
    save_session_state(
        session_id,
        map_context=new_map_ctx,
        last_query=query,
    )

    reply = await llm_client.chat_with_llm(
        GEO_REPLY_PROMPT.format(
            place_name=geo["place_name"],
            center_lat=geo["center_lat"],
            center_lon=geo["center_lon"],
            bbox=geo["bbox"],
        )
    )
    return map_update, reply


# ─── 主入口 ──────────────────────────────────────────────────────────────────

async def run_workflow(
    query: str,
    session_id: str = "",
    map_context: Optional[Dict[str, Any]] = None,
) -> ChatResponse:
    """
    工作流主入口：路由 → 规划 → 执行 → 汇总 → 返回 ChatResponse。

    ChatResponse.workflow_status 包含完整的中间状态摘要，
    可在前端聊天界面通过 status() 展示各步骤进度。
    """
    state = make_initial_state(query, session_id)
    state["session_context"] = load_session_context(session_id)
    state["context"].update(state["session_context"])
    _t0 = time.monotonic()

    # ── 1. routing ────────────────────────────────────────────────────────
    state["status"] = "routing"
    state["intent"] = await classify_intent(query)

    # 知识问答：走检索增强的单步回答，不进入多步执行工作流
    if state["intent"] == "knowledge":
        reply = await _answer_knowledge(query)
        save_session_state(
            session_id,
            context_updates=state["context"],
            map_context=map_context,
            last_query=query,
            last_reply=reply,
        )
        write_log(session_id, intent="knowledge", query=query, plan_steps=1,
                  reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
        return ChatResponse(
            reply=reply,
            workflow_status=WorkflowStatus(
                intent="knowledge",
                status="terminated",
                plan=["知识库检索与问答"],
                steps_completed=1,
                steps_total=1,
                steps=[],
            ),
        )

    # 地名定位：直接调用 geocoder tool
    if state["intent"] == "geo_query":
        map_update, reply = await _handle_geo_query(query, session_id, map_context)
        save_session_state(session_id, last_reply=reply)
        write_log(session_id, intent="geo_query", query=query, plan_steps=1,
                  reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
        return ChatResponse(
            reply=reply,
            map_update=map_update,
            workflow_status=WorkflowStatus(
                intent="geo_query",
                status="terminated",
                plan=["地名解析与地图定位"],
                steps_completed=1,
                steps_total=1,
                steps=[],
            ),
        )

    aoi_blocking_reply = _prepare_aoi_boundary_context(state)
    if aoi_blocking_reply:
        save_session_state(
            session_id,
            context_updates=state["context"],
            map_context=map_context,
            last_query=query,
            last_reply=aoi_blocking_reply,
        )
        write_log(session_id, intent="execution", query=query, plan_steps=0,
                  reply_preview=aoi_blocking_reply, duration_ms=int((time.monotonic()-_t0)*1000))
        return ChatResponse(
            reply=aoi_blocking_reply,
            workflow_status=WorkflowStatus(
                intent=state["intent"],
                status="terminated",
                plan=["AOI 边界解析"],
                steps_completed=0,
                steps_total=0,
                steps=[],
            ),
        )

    # ── 2. planning ───────────────────────────────────────────────────────
    state = await _plan(state)

    # ── 3. executing（逐步 Observe-Think-Act 循环） ───────────────────────
    state["status"] = "executing"
    for i, step in enumerate(state["plan"]):
        state = await _execute_step(state, step, i)

    # ── 4. summarizing ────────────────────────────────────────────────────
    state = await _summarize(state)

    # ── 5. 构建返回对象 ───────────────────────────────────────────────────
    map_update: Optional[MapUpdate] = None
    if state.get("map_update"):
        mu = state["map_update"]
        map_update = MapUpdate(
            center_lat=mu["center_lat"],
            center_lon=mu["center_lon"],
            zoom=mu["zoom"],
            bbox=mu.get("bbox"),
            layer_info=mu.get("layer_info"),
            layers=mu.get("layers"),
        )

    workflow_status = WorkflowStatus(
        intent=state["intent"],
        status=state["status"],
        plan=[s.get("description", "") for s in state["plan"]],
        steps_completed=len(state["steps"]),
        steps_total=len(state["plan"]),
        steps=[
            {
                "index": s["step_index"],
                "description": s["description"],
                "tool": s["tool"],
                "success": s["success"],
                "output_preview": (s["output"] or "")[:300],
                "code": s.get("code") or "",
            }
            for s in state["steps"]
        ],
    )

    resolved_map_context = map_context or {}
    if map_update:
        resolved_map_context = {
            "center_lat": map_update.center_lat,
            "center_lon": map_update.center_lon,
            "zoom": map_update.zoom,
            "bbox": map_update.bbox,
        }
    save_session_state(
        session_id,
        context_updates=state["context"],
        map_context=resolved_map_context,
        last_query=query,
        last_reply=state["final_reply"],
    )
    write_log(session_id, intent="execution", query=query,
              plan_steps=len(state["plan"]),
              reply_preview=state["final_reply"] or "",
              duration_ms=int((time.monotonic()-_t0)*1000))

    return ChatResponse(
        reply=state["final_reply"] or "工作流执行完成，但未生成汇总。",
        map_update=map_update,
        workflow_status=workflow_status,
    )


# ─── 流式主入口 ───────────────────────────────────────────────────────────────

def _evt(event_type: str, data: Any) -> str:
    """序列化为单行 JSON 事件（带换行符）。"""
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=False) + "\n"


async def stream_workflow(
    query: str,
    session_id: str = "",
    map_context: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    """
    工作流流式入口：与 run_workflow 逻辑相同，但每个关键节点都立即 yield 一个事件，
    而不是等到全部完成后一次性返回。

    前端通过 httpx 流式接收，实时更新 st.status。
    """
    state = make_initial_state(query, session_id)
    state["session_context"] = load_session_context(session_id)
    state["context"].update(state["session_context"])
    _t0 = time.monotonic()
    try:
        # ── 1. routing ────────────────────────────────────────────────────
        state["status"] = "routing"
        state["intent"] = await classify_intent(query)
        yield _evt("routing", {"intent": state["intent"]})

        # 知识问答：走检索增强的单步回答
        if state["intent"] == "knowledge":
            yield _evt("summarizing", {})
            reply = await _answer_knowledge(query)
            save_session_state(
                session_id,
                context_updates=state["context"],
                map_context=map_context,
                last_query=query,
                last_reply=reply,
            )
            write_log(session_id, intent="knowledge", query=query, plan_steps=1,
                      reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
            yield _evt("done", {"reply": reply, "map_update": None})
            return

        # 地名定位：直接走 geocoder，单步完成
        if state["intent"] == "geo_query":
            yield _evt("summarizing", {})
            map_update, reply = await _handle_geo_query(query, session_id, map_context)
            save_session_state(session_id, last_reply=reply)
            write_log(session_id, intent="geo_query", query=query, plan_steps=1,
                      reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
            yield _evt("done", {
                "reply": reply,
                "map_update": map_update.model_dump() if map_update else None,
            })
            return

        aoi_blocking_reply = _prepare_aoi_boundary_context(state)
        if aoi_blocking_reply:
            yield _evt("summarizing", {})
            save_session_state(
                session_id,
                context_updates=state["context"],
                map_context=map_context,
                last_query=query,
                last_reply=aoi_blocking_reply,
            )
            write_log(session_id, intent="execution", query=query, plan_steps=0,
                      reply_preview=aoi_blocking_reply, duration_ms=int((time.monotonic()-_t0)*1000))
            yield _evt("done", {
                "reply": aoi_blocking_reply,
                "map_update": None,
                "workflow_status": {
                    "intent": state["intent"],
                    "status": "terminated",
                    "plan": ["AOI 边界解析"],
                    "steps_completed": 0,
                    "steps_total": 0,
                    "steps": [],
                },
            })
            return

        # ── 2. planning ───────────────────────────────────────────────────
        state = await _plan(state)
        yield _evt("planning", {
            "plan": [
                {"description": s.get("description", ""), "type": s.get("type", "execute")}
                for s in state["plan"]
            ]
        })

        # ── 3. executing ──────────────────────────────────────────────────
        state["status"] = "executing"
        for i, step in enumerate(state["plan"]):
            step_type = step.get("type", "execute")
            tool_name = "asset_inspector" if step_type == "inspect" else "gee_executor"
            yield _evt("step_start", {
                "index": i,
                "description": step.get("description", f"步骤 {i+1}"),
                "tool": tool_name,
            })
            state = await _execute_step(state, step, i)
            done_result = state["steps"][-1]
            yield _evt("step_done", {
                "index": i,
                "description": done_result["description"],
                "tool": done_result["tool"],
                "success": done_result["success"],
                "output_preview": (done_result["output"] or "")[:300],
                "code": done_result.get("code") or "",
            })

        # ── 4. summarizing ────────────────────────────────────────────────
        yield _evt("summarizing", {})
        state = await _summarize(state)

        # ── 5. 构建 done 事件 ─────────────────────────────────────────────
        map_update_dict = state.get("map_update")
        resolved_map_context = map_context or {}
        if map_update_dict:
            resolved_map_context = {
                "center_lat": map_update_dict.get("center_lat"),
                "center_lon": map_update_dict.get("center_lon"),
                "zoom": map_update_dict.get("zoom"),
                "bbox": map_update_dict.get("bbox"),
            }
        save_session_state(
            session_id,
            context_updates=state["context"],
            map_context=resolved_map_context,
            last_query=query,
            last_reply=state["final_reply"],
        )
        write_log(session_id, intent="execution", query=query,
                  plan_steps=len(state["plan"]),
                  reply_preview=state["final_reply"] or "",
                  duration_ms=int((time.monotonic()-_t0)*1000))
        yield _evt("done", {
            "reply": state["final_reply"] or "工作流执行完成，但未生成汇总。",
            "map_update": map_update_dict,
            "workflow_status": {
                "intent": state["intent"],
                "status": state["status"],
                "plan": [s.get("description", "") for s in state["plan"]],
                "steps_completed": len(state["steps"]),
                "steps_total": len(state["plan"]),
                "steps": [
                    {
                        "index": s["step_index"],
                        "description": s["description"],
                        "tool": s["tool"],
                        "success": s["success"],
                        "output_preview": (s["output"] or "")[:300],
                        "code": s.get("code") or "",
                    }
                    for s in state["steps"]
                ],
            },
        })
    except Exception as exc:
        yield _evt("error", {"message": str(exc)})
