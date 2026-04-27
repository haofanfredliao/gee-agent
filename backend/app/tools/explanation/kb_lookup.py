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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


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
    return "\n\n".join(chunks)
