"""Generate dataset cards for RAG from the Earth Engine STAC catalog.

This script pulls metadata for a curated list of dataset IDs and writes
`gee_rag_data/gee_datasets_catalog.txt` in a stable card format.

Usage:
  PYTHONPATH=. python scripts/generate_dataset_cards_from_stac.py
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IDS_FILE = ROOT / "gee_rag_data" / "dataset_ids_common.txt"
DEFAULT_OUTPUT_FILE = ROOT / "gee_rag_data" / "gee_datasets_catalog.txt"
DEFAULT_STAC_BASE = "https://storage.googleapis.com/earthengine-stac/catalog"

HTTP_HEADERS = {"User-Agent": "gee-agent-stac-card-generator/1.0"}

CARD_ENRICHMENTS: dict[str, dict[str, list[str]]] = {
    "COPERNICUS/S2_SR_HARMONIZED": {
        "selection_constraints": [
            "Use this as the default Sentinel-2 SR collection.",
            "Map common aliases (Sentinel-2/S2/S2 SR/哨兵2) to this asset id.",
        ],
        "recommended_preprocessing": [
            "Mask clouds with COPERNICUS/S2_CLOUD_PROBABILITY before compositing.",
            "Scale SR bands using scale factor 0.0001 when computing indices.",
        ],
        "warnings": [
            "Critical: do NOT use deprecated COPERNICUS/S2_SR.",
            "Always use COPERNICUS/S2_SR_HARMONIZED for Sentinel-2 SR workflows.",
        ],
    }
}


def fetch_json(url: str, timeout: int = 30) -> dict[str, Any]:
    req = Request(url=url, headers=HTTP_HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
    obj = json.loads(payload)
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return obj


def read_dataset_ids(path: Path) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            ids.append(line)
    return ids


def dataset_id_to_primary_url(dataset_id: str, stac_base: str) -> str:
    dataset_name = dataset_id.replace("/", "_")
    provider = dataset_id.split("/", 1)[0]
    return f"{stac_base.rstrip('/')}/{provider}/{dataset_name}.json"


def resolve_item_url(dataset_id: str, stac_base: str) -> tuple[str, dict[str, Any]]:
    primary_url = dataset_id_to_primary_url(dataset_id, stac_base)
    try:
        return primary_url, fetch_json(primary_url)
    except HTTPError as exc:
        if exc.code != 404:
            raise

    # Fallback: locate the child item from provider catalog links.
    provider = dataset_id.split("/", 1)[0]
    provider_catalog_url = f"{stac_base.rstrip('/')}/{provider}/catalog.json"
    provider_catalog = fetch_json(provider_catalog_url)
    target_title = dataset_id.replace("/", "_")
    for link in provider_catalog.get("links", []):
        if link.get("rel") != "child":
            continue
        if link.get("title") != target_title:
            continue
        href = str(link.get("href") or "").strip()
        if not href:
            break
        return href, fetch_json(href)
    raise FileNotFoundError(f"Could not resolve STAC item for dataset id: {dataset_id}")


def fmt_temporal(item: dict[str, Any]) -> str:
    interval = (
        item.get("extent", {})
        .get("temporal", {})
        .get("interval", [[None, None]])
    )
    if not interval or not isinstance(interval, list):
        return "unknown"
    start, end = (interval[0] + [None, None])[:2] if isinstance(interval[0], list) else (None, None)
    start_s = str(start) if start else "unknown"
    end_s = str(end) if end else "open"
    return f"{start_s} -> {end_s}"


def fmt_spatial(item: dict[str, Any]) -> str:
    bbox = item.get("extent", {}).get("spatial", {}).get("bbox", [])
    if not bbox or not isinstance(bbox, list):
        return "unknown"
    first = bbox[0]
    if not isinstance(first, list) or len(first) < 4:
        return "unknown"
    return "[" + ", ".join(str(v) for v in first[:4]) + "]"


def fmt_interval(item: dict[str, Any]) -> str:
    gee_interval = item.get("gee:interval", {})
    if not isinstance(gee_interval, dict) or not gee_interval:
        return "unknown"
    value = gee_interval.get("interval")
    unit = gee_interval.get("unit")
    typ = gee_interval.get("type")
    parts = [str(x) for x in (value, unit, typ) if x is not None and str(x).strip()]
    return " ".join(parts) if parts else "unknown"


def normalize_gsd(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, list):
        return "/".join(str(x) for x in raw)
    return str(raw)


def trim_text(text: str, max_len: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


def get_ee_type(item: dict[str, Any]) -> str:
    raw = str(item.get("gee:type") or "").strip().lower()
    mapping = {
        "image": "ee.Image",
        "image_collection": "ee.ImageCollection",
        "table": "ee.FeatureCollection",
        "table_collection": "ee.FeatureCollection",
    }
    return mapping.get(raw, raw or "unknown")


def pick_link(item: dict[str, Any], rel: str) -> str:
    for link in item.get("links", []):
        if link.get("rel") == rel:
            href = str(link.get("href") or "").strip()
            if href:
                return href
    return ""


def pick_related_js_link(item: dict[str, Any]) -> str:
    for link in item.get("links", []):
        if link.get("rel") != "related":
            continue
        if str(link.get("code") or "").lower() != "javascript":
            continue
        href = str(link.get("href") or "").strip()
        if href:
            return href
    return ""


def format_band_lines(item: dict[str, Any], max_items: int = 12) -> list[str]:
    bands = item.get("summaries", {}).get("eo:bands", [])
    if not isinstance(bands, list) or not bands:
        return ["  - (no eo:bands in STAC summaries)"]
    lines: list[str] = []
    for band in bands[:max_items]:
        if not isinstance(band, dict):
            continue
        name = str(band.get("name") or "unknown")
        desc = trim_text(str(band.get("description") or ""))
        gsd = normalize_gsd(band.get("gsd"))
        units = str(band.get("gee:units") or "").strip()
        scale = str(band.get("gee:scale") or "").strip()
        meta_parts = []
        if gsd:
            meta_parts.append(f"gsd={gsd}")
        if units:
            meta_parts.append(f"unit={units}")
        if scale:
            meta_parts.append(f"scale={scale}")
        head = f"  - {name}"
        if meta_parts:
            head += " (" + ", ".join(meta_parts) + ")"
        if desc:
            head += f": {desc}"
        lines.append(head)
    return lines or ["  - (no eo:bands in STAC summaries)"]


def format_property_lines(item: dict[str, Any], max_items: int = 12) -> list[str]:
    schema = item.get("summaries", {}).get("gee:schema", [])
    if not isinstance(schema, list) or not schema:
        return ["  - (no gee:schema in STAC summaries)"]
    lines: list[str] = []
    for prop in schema[:max_items]:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "unknown")
        typ = str(prop.get("type") or "").strip()
        desc = trim_text(str(prop.get("description") or ""))
        head = f"  - {name}"
        if typ:
            head += f" ({typ})"
        if desc:
            head += f": {desc}"
        lines.append(head)
    return lines or ["  - (no gee:schema in STAC summaries)"]


def render_card(item: dict[str, Any], stac_source_url: str) -> str:
    dataset_id = str(item.get("id") or "").strip() or "unknown"
    title = trim_text(str(item.get("title") or ""))
    categories = item.get("gee:categories", [])
    keywords = item.get("keywords", [])
    providers = item.get("providers", [])
    providers_text = ", ".join(
        str(p.get("name")).strip()
        for p in providers
        if isinstance(p, dict) and str(p.get("name") or "").strip()
    ) or "unknown"

    category_text = ", ".join(str(x) for x in categories) if isinstance(categories, list) and categories else "unknown"
    keyword_text = ", ".join(str(x) for x in keywords[:12]) if isinstance(keywords, list) and keywords else "unknown"
    status_text = str(item.get("gee:status") or "unknown")
    license_text = str(item.get("license") or "unknown")
    terms = trim_text(str(item.get("gee:terms_of_use") or ""), max_len=220)

    enrich = CARD_ENRICHMENTS.get(dataset_id, {})
    selection_constraints = enrich.get("selection_constraints") or ["TODO"]
    recommended_preprocessing = enrich.get("recommended_preprocessing") or ["TODO"]
    warnings = enrich.get("warnings") or ["TODO"]

    lines: list[str] = [
        f"## {dataset_id}",
        f"Name: {title or 'unknown'}",
        f"Asset ID: {dataset_id}",
        f"Type: {get_ee_type(item)}",
        f"Status: {status_text}",
        f"Categories: {category_text}",
        f"Temporal coverage: {fmt_temporal(item)}",
        f"Update frequency: {fmt_interval(item)}",
        f"Spatial bbox: {fmt_spatial(item)}",
        f"License: {license_text}",
        f"Providers: {providers_text}",
        f"Keywords: {keyword_text}",
        f"Terms (short): {terms or 'unknown'}",
        f"STAC item: {stac_source_url}",
        f"Dataset page: {pick_link(item, 'license') or 'unknown'}",
        f"Code Editor example: {pick_related_js_link(item) or 'unknown'}",
        f"Preview image: {pick_link(item, 'preview') or 'unknown'}",
        "",
        "Selection constraints:",
        *[f"  - {x}" for x in selection_constraints],
        "Recommended preprocessing:",
        *[f"  - {x}" for x in recommended_preprocessing],
        "Warnings:",
        *[f"  - {x}" for x in warnings],
        "",
        "Key bands:",
    ]
    lines.extend(format_band_lines(item))
    lines.append("Key properties:")
    lines.extend(format_property_lines(item))
    lines.append("===")
    return "\n".join(lines)


def build_document(cards: list[str], ids_file: Path) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = [
        "## GEE Dataset Catalog",
        "Generated from Earth Engine STAC metadata.",
        f"Generated at: {now}",
        f"Dataset ID source: {ids_file.as_posix()}",
        "Notes:",
        "- Keep one dataset per card.",
        "- Use `===` as the card separator (required by current chunking setup).",
        "- `Selection constraints`, `Recommended preprocessing`, `Warnings` are placeholders for manual enrichment.",
        "===",
    ]
    return "\n".join(header + cards).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GEE dataset cards from STAC")
    parser.add_argument(
        "--ids-file",
        type=Path,
        default=DEFAULT_IDS_FILE,
        help=f"Path to curated dataset ID list (default: {DEFAULT_IDS_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Output catalog txt path (default: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--stac-base",
        default=DEFAULT_STAC_BASE,
        help=f"Earth Engine STAC base URL (default: {DEFAULT_STAC_BASE})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ids_file = args.ids_file if args.ids_file.is_absolute() else (ROOT / args.ids_file)
    output_file = args.output if args.output.is_absolute() else (ROOT / args.output)
    stac_base = str(args.stac_base).rstrip("/")

    if not ids_file.exists():
        raise FileNotFoundError(f"Dataset ID file not found: {ids_file}")

    dataset_ids = read_dataset_ids(ids_file)
    if not dataset_ids:
        raise ValueError(f"No dataset ids found in: {ids_file}")

    cards: list[str] = []
    failed: list[tuple[str, str]] = []

    for dataset_id in dataset_ids:
        print(f"[fetch] {dataset_id}")
        try:
            source_url, item = resolve_item_url(dataset_id, stac_base=stac_base)
            cards.append(render_card(item, stac_source_url=source_url))
        except (HTTPError, URLError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            failed.append((dataset_id, str(exc)))
            print(f"  ! failed: {dataset_id} -> {exc}")

    if not cards:
        raise RuntimeError("No cards fetched from STAC; output file not updated.")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(build_document(cards, ids_file=ids_file), encoding="utf-8")

    print(f"[done] cards={len(cards)} output={output_file}")
    if failed:
        print(f"[warn] failed={len(failed)}")
        for dataset_id, reason in failed:
            print(f"  - {dataset_id}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
