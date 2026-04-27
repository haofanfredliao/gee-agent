"""Resolve, cache, and load OpenStreetMap boundaries for GEE snippets."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from backend.app.services.geocoding import nominatim_search


class AmbiguousBoundaryError(RuntimeError):
    """Raised when a place name maps to multiple plausible OSM boundaries."""

    def __init__(self, place_name: str, options: List[Dict[str, Any]]) -> None:
        self.place_name = place_name
        self.options = options
        labels = [
            f"{i + 1}. {o.get('display_name')} ({o.get('osm_type')} {o.get('osm_id')})"
            for i, o in enumerate(options[:5])
        ]
        super().__init__(
            "Ambiguous OSM boundary for "
            f"{place_name!r}. Please choose one:\n" + "\n".join(labels)
        )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _boundaries_root() -> Path:
    return _project_root() / "gee_rag_data" / "boundaries"


def _osm_cache_root() -> Path:
    return _boundaries_root() / "osm"


def _legacy_hk_path() -> Path:
    return _boundaries_root() / "hong_kong_osm_boundary.geojson"


def _normalize_place(place_name: str) -> str:
    return unicodedata.normalize("NFKC", place_name or "").strip()


_PLACE_ALIASES = {
    "香港": "Hong Kong",
    "香港特别行政区": "Hong Kong",
    "深圳": "Shenzhen",
    "深圳市": "Shenzhen",
    "广州": "Guangzhou",
    "广州市": "Guangzhou",
    "北京": "Beijing",
    "北京市": "Beijing",
    "上海": "Shanghai",
    "上海市": "Shanghai",
}


def _canonical_place_name(place_name: str) -> str:
    normalized = _normalize_place(place_name)
    return _PLACE_ALIASES.get(normalized, normalized)


def _slugify(place_name: str) -> str:
    normalized = _canonical_place_name(place_name).lower()
    slug = re.sub(r"[^\w]+", "_", normalized, flags=re.UNICODE).strip("_")
    if not slug:
        slug = "place"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:60]}__{digest}"


def _is_hong_kong(place_name: str) -> bool:
    normalized = _canonical_place_name(place_name).lower().replace(" ", "")
    return normalized in {"香港", "hongkong", "hongkongsar", "hongkongchina"}


def osm_boundary_cache_path(place_name: str) -> Path:
    """Return the canonical local cache path for a place query."""
    if _is_hong_kong(place_name) and _legacy_hk_path().exists():
        return _legacy_hk_path()
    return _osm_cache_root() / f"{_slugify(place_name)}.geojson"


def hong_kong_osm_boundary_path() -> Path:
    """Backward-compatible path used by the previous HK-specific helper."""
    return _legacy_hk_path()


def _candidate_geometry(item: Dict[str, Any]) -> Dict[str, Any] | None:
    geom = item.get("geojson") or {}
    if geom.get("type") in {"Polygon", "MultiPolygon"}:
        return geom
    return None


def _score_candidate(item: Dict[str, Any], place_name: str) -> tuple[int, float]:
    geom = _candidate_geometry(item)
    category = item.get("category") or item.get("class") or ""
    typ = item.get("type") or ""
    osm_type = item.get("osm_type") or ""
    display = (item.get("display_name") or "").lower()
    name = (item.get("name") or "").lower()
    query = _normalize_place(place_name).lower()
    extratags = item.get("extratags") or {}
    importance = float(item.get("importance") or 0)

    score = 0
    if geom:
        score += 100
    if category == "boundary":
        score += 45
    if typ in {"administrative", "city", "municipality", "county", "state", "province"}:
        score += 25
    if osm_type == "relation":
        score += 15
    if extratags.get("admin_level"):
        score += 10
    if query and (query in display or query in name):
        score += 20
    if "boundary" in display:
        score += 5
    return score, importance


def _bbox_from_item(item: Dict[str, Any]) -> List[float] | None:
    raw = item.get("boundingbox") or []
    if len(raw) != 4:
        return None
    try:
        south, north, west, east = [float(v) for v in raw]
        return [west, south, east, north]
    except (TypeError, ValueError):
        return None


def _geometry_bbox(geometry: Dict[str, Any] | None) -> List[float] | None:
    if not geometry:
        return None
    coords = geometry.get("coordinates")
    values: List[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, list)
            and len(node) >= 2
            and all(isinstance(v, (int, float)) for v in node[:2])
        ):
            values.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, list):
            for child in node:
                walk(child)

    walk(coords)
    if not values:
        return None
    lons = [lon for lon, _ in values]
    lats = [lat for _, lat in values]
    return [min(lons), min(lats), max(lons), max(lats)]


def _summarize_candidate(item: Dict[str, Any], place_name: str) -> Dict[str, Any]:
    score, importance = _score_candidate(item, place_name)
    return {
        "name": item.get("name"),
        "display_name": item.get("display_name"),
        "osm_type": item.get("osm_type"),
        "osm_id": item.get("osm_id"),
        "category": item.get("category") or item.get("class"),
        "type": item.get("type"),
        "importance": importance,
        "score": score,
        "bbox": _bbox_from_item(item),
    }


def _is_ambiguous(ranked: List[Dict[str, Any]], place_name: str) -> bool:
    if len(ranked) < 2:
        return False
    first = ranked[0]
    second = ranked[1]
    first_score, first_imp = _score_candidate(first, place_name)
    second_score, second_imp = _score_candidate(second, place_name)
    if first.get("osm_type") == second.get("osm_type") and first.get("osm_id") == second.get("osm_id"):
        return False
    # A clear administrative boundary usually wins by a large margin. If two
    # polygon boundaries are close in score and importance, ask the user.
    return (first_score - second_score) < 15 and abs(first_imp - second_imp) < 0.08


def _read_cache_metadata(path: Path, place_name: str) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    feature = (data.get("features") or [{}])[0] if data.get("type") == "FeatureCollection" else data
    props = feature.get("properties") or {}
    bbox = props.get("bbox") or _geometry_bbox(feature.get("geometry"))
    return {
        "status": "ok",
        "source": props.get("source", "OpenStreetMap cache"),
        "place_name": place_name,
        "cache_path": str(path),
        "display_name": props.get("display_name") or props.get("name") or place_name,
        "osm_type": props.get("osm_type"),
        "osm_id": props.get("osm_id"),
        "bbox": bbox,
        "cache_hit": True,
    }


def _write_feature_collection(
    *,
    path: Path,
    place_name: str,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    geometry = _candidate_geometry(item)
    if not geometry:
        raise RuntimeError(f"Nominatim result for {place_name!r} has no polygon boundary.")

    props = {
        "name": item.get("name") or place_name,
        "place_query": place_name,
        "display_name": item.get("display_name"),
        "osm_type": item.get("osm_type"),
        "osm_id": item.get("osm_id"),
        "category": item.get("category") or item.get("class"),
        "type": item.get("type"),
        "importance": item.get("importance"),
        "bbox": _bbox_from_item(item) or _geometry_bbox(geometry),
        "source": "OpenStreetMap Nominatim",
        "attribution": "Data (c) OpenStreetMap contributors, ODbL 1.0",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    feature_collection = {
        "type": "FeatureCollection",
        "name": _slugify(place_name),
        "features": [
            {
                "type": "Feature",
                "properties": props,
                "geometry": geometry,
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(feature_collection, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "source": "OpenStreetMap Nominatim",
        "place_name": place_name,
        "cache_path": str(path),
        "display_name": props["display_name"] or props["name"],
        "osm_type": props["osm_type"],
        "osm_id": props["osm_id"],
        "bbox": props["bbox"],
        "cache_hit": False,
    }


def resolve_osm_boundary(
    place_name: str,
    *,
    force_refresh: bool = False,
    allow_ambiguous: bool = False,
) -> Dict[str, Any]:
    """Resolve a place to a cached OSM Polygon/MultiPolygon boundary.

    Returns metadata including ``cache_path``. If the name is ambiguous and
    ``allow_ambiguous`` is false, raises ``AmbiguousBoundaryError`` with top
    candidate options for the UI/user.
    """
    place_name = _normalize_place(place_name)
    if not place_name:
        return {"status": "error", "message": "Place name is empty."}

    search_name = _canonical_place_name(place_name)
    path = osm_boundary_cache_path(search_name)
    if path.exists() and not force_refresh:
        return _read_cache_metadata(path, place_name)

    results = nominatim_search(search_name, polygon_geojson=True, limit=10)
    polygon_results = [item for item in results if _candidate_geometry(item)]
    if not polygon_results:
        return {
            "status": "error",
            "place_name": place_name,
            "message": f"Nominatim returned no Polygon/MultiPolygon boundary for {place_name}.",
        }

    ranked = sorted(
        polygon_results,
        key=lambda item: _score_candidate(item, search_name),
        reverse=True,
    )
    if _is_ambiguous(ranked, search_name) and not allow_ambiguous:
        raise AmbiguousBoundaryError(
            place_name,
            [_summarize_candidate(item, search_name) for item in ranked[:5]],
        )

    return _write_feature_collection(path=path, place_name=place_name, item=ranked[0])


def _iter_geojson_features(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if data.get("type") == "FeatureCollection":
        yield from data.get("features") or []
    elif data.get("type") == "Feature":
        yield data
    elif data.get("type") in {"Polygon", "MultiPolygon"}:
        yield {"type": "Feature", "properties": {}, "geometry": data}


def load_cached_boundary_fc(ee_module: Any, cache_path: str | Path) -> Any:
    """Return a cached GeoJSON boundary as an ee.FeatureCollection."""
    path = Path(cache_path)
    if not path.exists():
        raise FileNotFoundError(
            f"OSM boundary cache not found: {path}. "
            "Run: python scripts/fetch_osm_boundary.py --place <place name>"
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    ee_features = []
    for feature in _iter_geojson_features(data):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        ee_features.append(
            ee_module.Feature(
                ee_module.Geometry(geometry),
                feature.get("properties") or {},
            )
        )
    if not ee_features:
        raise ValueError(f"OSM boundary cache has no feature geometries: {path}")
    return ee_module.FeatureCollection(ee_features)


def load_hong_kong_osm_boundary_fc(ee_module: Any) -> Any:
    """Backward-compatible HK helper exposed to older generated snippets."""
    path = hong_kong_osm_boundary_path()
    if not path.exists():
        path = osm_boundary_cache_path("Hong Kong")
    return load_cached_boundary_fc(ee_module, path)
