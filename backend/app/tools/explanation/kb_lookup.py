"""知识库检索工具（explanation 层）。

封装 Chroma 向量检索，供 orchestrator 在规划或执行步骤中
查询与当前任务相关的 GEE API 文档和知识。

Additionally provides a lightweight file-based hotfix path for specific
high-frequency workflows (e.g., Sentinel-2 low-cloud mosaic) so prompt context
can be updated immediately even if vector index rebuild is delayed.
"""
import re
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


def _looks_like_dnbr_fire_severity_query(query: str) -> bool:
    """林火 / 过火面积 / dNBR 火烈度评估（与纯光谱 NBR 单点计算区分，优先注入专项 few-shot）。"""
    raw = query or ""
    q = raw.lower()
    if "dnbr" in q or "d-nbr" in q or "delta nbr" in q:
        return True
    if "nbr差" in raw or "nbr 差" in raw or "nbr差值" in raw or "差值nbr" in raw:
        return True
    fire_zh = any(
        t in raw
        for t in (
            "过火",
            "林火",
            "森林火",
            "森林火灾",
            "火烧迹",
            "火烧迹地",
            "火烈度",
            "野火",
            "火灾",
            "焚毁",
            "烧毁",
            "灾前",
            "灾后",
            "严重程度",
            "过火面积",
            "火烧强度",
        )
    )
    fire_en = any(
        t in q
        for t in (
            "wildfire",
            "forest fire",
            "burn severity",
            "burned area",
            "burn scar",
            "fire scar",
            "post-fire",
            "post fire",
            "pre-fire",
            "pre fire",
        )
    )
    nbr_like = (
        "nbr" in q
        or "归一化燃烧" in raw
        or "归一化过火" in raw
        or "燃烧指数" in raw
    )
    if nbr_like and (fire_zh or fire_en):
        return True
    if (fire_zh or fire_en) and ("swir" in q or "短波红外" in raw):
        return True
    return False


def _looks_like_soil_moisture_drought_query(query: str) -> bool:
    """SMAP / GLDAS 土壤湿度异常与农业干旱（百分位法）；优先于通用光谱指数 hotfix。"""
    raw = query or ""
    q = raw.lower()
    sm_en = any(
        t in q
        for t in (
            "smap",
            "spl3smp",
            "spl4sm",
            "gldas",
            "noah/g025/t3h",
            "soil moisture",
            "soil-moisture",
            "soil_moist",
            "soilmoist",
            "volumetric soil",
        )
    )
    sm_zh = any(
        t in raw
        for t in (
            "土壤湿度",
            "土壤水分",
            "地表土壤水",
            "地表土壤湿度",
            "土壤墒情",
            "墒情",
        )
    )
    ag_drought_ctx = ("农业干旱" in raw) or ("agricultural drought" in q)
    pct_ctx = ("百分位" in raw) or ("percentile" in q)
    anomaly_zh = ("湿度异常" in raw) or ("土壤湿度异常" in raw) or ("土壤水分异常" in raw)
    anomaly_en = "soil moisture anomaly" in q
    drought_moisture = ("drought" in q) and ("moisture" in q or "sm " in f" {q} " or " soil " in f" {q} ")
    return (
        sm_en
        or sm_zh
        or anomaly_zh
        or anomaly_en
        or drought_moisture
        or (ag_drought_ctx and pct_ctx and (sm_en or sm_zh or "干旱指数" in raw))
    )


def _looks_like_crop_phenology_sos_eos_query(query: str) -> bool:
    """作物 / 农田 NDVI 物候：SOS、EOS、生长季长度、双逻辑斯谛；优先于通用 AOI 光谱指数 hotfix。"""
    raw = query or ""
    q = raw.lower()
    # 避免将 EOS 卫星产品名误判为物候 EOS
    if re.search(r"\beos\b", q) and re.search(r"\beos\b\s*[-]?(sat|sar|01|spacecraft|mission)", q):
        return False

    zh_ctx = (
        "物候" in raw
        or "生长季" in raw
        or "双逻辑" in raw
        or "逻辑斯谛" in raw
        or "返青" in raw
        or "枯黄" in raw
        or "农作物" in raw
    )
    en_ctx = (
        "phenology" in q
        or "double logistic" in q
        or "start of season" in q
        or "end of season" in q
        or "growing season length" in q
        or "season onset" in q
        or "season offset" in q
    )
    acronym_sos = bool(re.search(r"\bsos\b", q))
    acronym_eos = bool(re.search(r"\beos\b", q))

    farm_crop = (
        "农田" in raw
        or "田块" in raw
        or "作物" in raw
        or "parcel" in q
        or "farmland" in q
        or ("crop" in q and ("aoi" in q or "field" in q or "parcel" in q))
        or "agricultur" in q
    )

    ts_veg = (
        "ndvi" in q
        or "evi" in q
        or "sentinel" in q
        or bool(re.search(r"\bs2\b", q))
        or "时序" in raw
        or "曲线" in raw
        or "time series" in q
    )

    pheno_signal = (
        zh_ctx
        or en_ctx
        or (acronym_sos and acronym_eos)
        or ((acronym_sos or acronym_eos) and (zh_ctx or en_ctx or farm_crop or "ndvi" in q))
    )
    if not pheno_signal:
        return False
    return bool(ts_veg or farm_crop or zh_ctx)


def _looks_like_urban_cooling_effect_query(query: str) -> bool:
    """城市绿地降温效应：基于 LST 的缓冲区梯度、降温幅度与服务距离分析。"""
    raw = query or ""
    q = raw.lower()
    cooling_zh = any(
        t in raw
        for t in (
            "绿地降温",
            "城市绿地降温",
            "降温效应",
            "冷岛效应",
            "公园降温",
            "降温服务距离",
            "降温幅度",
            "缓冲区分析",
            "绿地内部",
            "绿地边缘",
            "服务距离",
        )
    )
    cooling_en = any(
        t in q
        for t in (
            "urban cooling",
            "cooling effect",
            "cooling service distance",
            "cooling distance",
            "cooling amplitude",
            "park cooling",
            "park cool island",
            "pci",
            "buffer analysis",
        )
    )
    lst_ctx = ("lst" in q) or ("land surface temperature" in q) or ("地表温度" in raw)
    green_ctx = any(
        t in q
        for t in (
            "green space",
            "greenspace",
            "park",
            "forest",
            "urban forest",
        )
    ) or any(t in raw for t in ("绿地", "公园", "森林"))
    return bool((cooling_zh or cooling_en) and (lst_ctx or green_ctx))


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
    if _looks_like_dnbr_fire_severity_query(query):
        p = _project_root() / "gee_rag_data" / "few_shot_dnbr_fire_severity_area.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append("【Hotfix: dNBR / Burn Severity & Burned Area】\n" + text)
        except Exception:
            pass
    elif _looks_like_soil_moisture_drought_query(query):
        p = _project_root() / "gee_rag_data" / "few_shot_soil_moisture_agricultural_drought.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append("【Hotfix: SMAP / GLDAS Soil Moisture & Agricultural Drought】\n" + text)
        except Exception:
            pass
    elif _looks_like_crop_phenology_sos_eos_query(query):
        p = _project_root() / "gee_rag_data" / "few_shot_crop_phenology_sos_eos_double_logistic.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append(
                    "【Hotfix: Crop Phenology (SOS / EOS / LOS) | Sentinel-2 NDVI + Double Logistic】\n"
                    + text
                )
        except Exception:
            pass
    elif _looks_like_urban_cooling_effect_query(query):
        p = _project_root() / "gee_rag_data" / "few_shot_urban_cooling_effect.txt"
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                docs.append(
                    "【Hotfix: Urban Cooling Effect | Green-space LST Buffer Analysis (Cooling Amplitude & Service Distance)】\n"
                    + text
                )
        except Exception:
            pass
    elif _looks_like_aoi_ndvi_query(query):
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
