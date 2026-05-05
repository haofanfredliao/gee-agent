"""知识库检索工具（explanation 层）。

封装 Chroma 向量检索，供 orchestrator 在规划或执行步骤中
查询与当前任务相关的 GEE API 文档和知识。

Additionally provides a lightweight file-based hotfix path for specific
high-frequency workflows (e.g., Sentinel-2 low-cloud mosaic) so prompt context
can be updated immediately even if vector index rebuild is delayed.
"""
from pathlib import Path
from typing import List

from backend.app.services import chroma_store

MAX_HOTFIX_DOC_CHARS = 24_000
MAX_RAG_CHUNK_CHARS = 8_000
MAX_RAG_TOTAL_CHARS = 48_000


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _clip_text(text: str, limit: int, label: str = "text") -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[... {label} truncated by gee-agent: removed {len(text) - limit:,} characters ...]"
    )


def _clip_chunks(chunks: List[str], total_limit: int = MAX_RAG_TOTAL_CHARS) -> List[str]:
    clipped: List[str] = []
    used = 0
    for idx, chunk in enumerate(chunks, start=1):
        if not chunk:
            continue
        remaining = total_limit - used
        if remaining <= 0:
            clipped.append("[... additional RAG chunks omitted to keep prompt under model limit ...]")
            break
        label = f"RAG chunk {idx}"
        per_chunk_limit = MAX_HOTFIX_DOC_CHARS if "Hotfix:" in chunk[:120] else MAX_RAG_CHUNK_CHARS
        safe_chunk = _clip_text(chunk, min(per_chunk_limit, remaining), label)
        clipped.append(safe_chunk)
        used += len(safe_chunk)
    return clipped


def _asks_for_ndvi(query: str) -> bool:
    return "ndvi" in (query or "").lower()


def _asks_for_same_day_mosaic(query: str) -> bool:
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


def _looks_like_sentinel2_mosaic_query(query: str) -> bool:
    q = (query or "").lower()
    s2_any = ("sentinel-2", "sentinel 2", "s2")
    task_any = (
        "mosaic",
        "最少云",
        "少云",
        "云量",
        "真彩色",
        "true color",
        "clip",
        "裁剪",
        "hong kong",
        "香港",
    )
    return (not _asks_for_ndvi(q)) and any(k in q for k in s2_any) and any(k in q for k in task_any)


def _looks_like_aoi_ndvi_query(query: str) -> bool:
    q = (query or "").lower()
    index_terms = (
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
    has_index = any(term in q for term in index_terms)
    has_s2 = ("sentinel" in q) or ("s2" in q)
    has_landsat = ("landsat" in q) or ("landsat8" in q) or ("landsat9" in q) or (" l8 " in f" {q} ") or (" l9 " in f" {q} ")
    has_followup = any(term in q for term in ("刚刚", "刚才", "这个", "这张", "上一张", "上次", "previous image", "last image"))
    has_aoi_hint = any(term in q for term in ("广州", "广州市", "香港", "深圳", "北京市", "上海", "行政区", "aoi", "clip", "裁剪"))
    return has_index and (has_s2 or has_landsat or has_followup or has_aoi_hint)


def _looks_like_dynamic_world_query(query: str) -> bool:
    q = (query or "").lower()
    dataset_terms = (
        "dynamic world",
        "dynamicworld",
        "google/dynamicworld/v1",
    )
    task_terms = (
        "land cover",
        "land-use",
        "land use",
        "class proportion",
        "class proportions",
        "percentage",
        "proportion",
        "占比",
        "地类",
        "土地覆盖",
    )
    return any(term in q for term in dataset_terms) or (
        "dynamic" in q and "world" in q and any(term in q for term in task_terms)
    )


def _looks_like_landsat_full_coverage_query(query: str) -> bool:
    q = (query or "").lower()
    has_landsat = any(term in q for term in ("landsat", "landsat8", "landsat 8", "landsat9", "landsat 9"))
    has_imagery = any(
        term in q
        for term in (
            "image",
            "imagery",
            "remote sensing",
            "mosaic",
            "least cloud",
            "cloud cover",
            "真彩色",
            "影像",
            "最少云",
        )
    )
    has_aoi = any(term in q for term in ("hong kong", "香港", "china", "中国", "district", "行政区", "aoi"))
    return has_landsat and has_imagery and has_aoi


def _looks_like_landsat_uhi_query(query: str) -> bool:
    q = (query or "").lower()
    has_landsat = "landsat" in q
    has_uhi = any(
        term in q
        for term in (
            "urban heat island",
            "surface urban heat island",
            "heat island",
            "heat island intensity",
            "uhi",
            "城市热岛",
            "热岛强度",
            "热岛",
        )
    )
    return has_landsat and has_uhi


def _looks_like_validation_error_matrix_query(query: str) -> bool:
    q = (query or "").lower()
    terms = (
        "error matrix",
        "confusion matrix",
        "overall accuracy",
        "users accuracy",
        "user's accuracy",
        "users’ accuracy",
        "producers accuracy",
        "producer's accuracy",
        "producers’ accuracy",
        "kappa",
        "精度",
        "误差矩阵",
        "混淆矩阵",
    )
    return any(term in q for term in terms)


def _load_hotfix_docs(query: str) -> List[str]:
    docs: List[str] = []
    if _looks_like_aoi_ndvi_query(query):
        p = _project_root() / "gee_rag_data" / "few_shot_spectral_indices.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append("【Hotfix: AOI Spectral Index Product】\n" + text)
        except Exception:
            pass
    elif _looks_like_sentinel2_mosaic_query(query):
        p = _project_root() / "gee_rag_data" / "few_shot_sentinel2_cloudless_mosaic.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                mode = (
                    "same-day lowest-cloud mosaic"
                    if _asks_for_same_day_mosaic(query)
                    else "cloud-masked multi-scene composite"
                )
                docs.append(f"【Hotfix: Sentinel-2 True Color Product | Required mode: {mode}】\n" + text)
        except Exception:
            # Hotfix docs are best-effort; do not block normal retrieval.
            pass
    elif _looks_like_dynamic_world_query(query):
        p = _project_root() / "gee_rag_data" / "06_dynamic_world_landcover_proportions.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append(
                    "【Hotfix: Dynamic World Land Cover Proportions】\n"
                    "For Earth Engine Python, do not use ee.List.get(index, default). "
                    "When mapping grouped class results, use ee.List(...).get(index) only, "
                    "or convert class-id/name mapping to ee.Dictionary before lookup.\n"
                    + text
                )
        except Exception:
            pass
    elif _looks_like_landsat_uhi_query(query):
        p = _project_root() / "gee_rag_data" / "18_landsat_uhi_intensity_summary.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append(
                    "【Hotfix: Landsat UHI intensity】\n"
                    "Do not answer a UHI request with raw LST only. Use Landsat Collection 2 Level 2 ST_B10, convert "
                    "to Celsius with `ST_B10 * 0.00341802 + 149.0 - 273.15`, build explicit urban and rural masks, "
                    "and compute `uhi_intensity_c = urban_mean_temp_c - rural_mean_temp_c`. If a map layer is requested, "
                    "visualize a UHI intensity ee.Image such as `temp_c.subtract(rural_mean)` rather than raw LST. "
                    "Print Dataset, Selected period/date, Candidate count, Boundary source, Boundary names, selected_band, "
                    "temp_unit, urban_pixel_count, rural_pixel_count, urban_mean_temp_c, rural_mean_temp_c, and "
                    "uhi_intensity_c.\n"
                    + text
                )
        except Exception:
            pass
    elif _looks_like_validation_error_matrix_query(query):
        p = _project_root() / "gee_rag_data" / "few_shot_validation_error_matrix.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append(
                    "【Hotfix: Validation metrics must be concrete】\n"
                    "The final code must print exact metric values using labels `Error Matrix:`, "
                    "`Overall Accuracy:`, `Producers Accuracy:`, `Users Accuracy:`, and `Kappa Index:`. "
                    "The final answer must copy these values directly; do not say only 'computed' or 'as shown in logs'.\n"
                    + text
                )
        except Exception:
            pass
    elif _looks_like_landsat_full_coverage_query(query):
        docs.append(
            "【Hotfix: Landsat Full-AOI True Color Product】\n"
            "For Landsat 8/9 AOI imagery, do not display a single `.sort('CLOUD_COVER').first()` scene. "
            "A single Landsat path/row footprint can cover only part of Hong Kong or other large AOIs. "
            "Build a bounded ImageCollection product instead: filterBounds(aoi), filterDate(start, end), "
            "sort('CLOUD_COVER').limit(30), apply SR scale factors, then use median() or same-date mosaic() "
            "and clipToCollection(aoi_fc). Use RGB bands SR_B4/SR_B3/SR_B2 with min=0.0 max=0.3. "
            "Print Dataset, Selected period, Candidate count, Image IDs if available, Boundary source, and Map.addLayer the final composite."
        )
    return docs


def knowledge_base_lookup(query: str, k: int = 3) -> str:
    """
    从 Chroma 知识库检索与 query 相关的文档片段。

    Parameters
    ----------
    query : str
        检索问题或关键词。
    k : int
        返回的文档片段数量。

    Returns
    -------
    拼接后的文本字符串，或"（未找到相关文档）"。
    """
    chunks: List[str] = []
    chunks.extend(_load_hotfix_docs(query))

    try:
        hits = chroma_store.similarity_search(query, k=k)
    except Exception:
        hits = []

    if hits:
        chunks.extend(h["content"] for h in hits if h.get("content"))

    if not chunks:
        return "（未找到相关文档）"
    return "\n\n".join(_clip_chunks(chunks))
