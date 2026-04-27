"""Fetch and cache an OpenStreetMap boundary for use inside GEE snippets.

Examples:
  python scripts/fetch_osm_boundary.py --place "Hong Kong"
  python scripts/fetch_osm_boundary.py --place "Shenzhen"
  python scripts/fetch_osm_boundary.py --place "Cambridge, UK"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.tools.geo.osm_boundary import (  # noqa: E402
    AmbiguousBoundaryError,
    resolve_osm_boundary,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache an OSM boundary.")
    parser.add_argument(
        "--place",
        default="Hong Kong",
        help="Place name to query in Nominatim. Default: Hong Kong",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore an existing cache and fetch from Nominatim again.",
    )
    parser.add_argument(
        "--allow-ambiguous",
        action="store_true",
        help="Pick the best-scoring result even if candidates look ambiguous.",
    )
    args = parser.parse_args()

    try:
        result = resolve_osm_boundary(
            args.place,
            force_refresh=args.force_refresh,
            allow_ambiguous=args.allow_ambiguous,
        )
    except AmbiguousBoundaryError as exc:
        print(f"[ambiguous] {exc.place_name}")
        for i, option in enumerate(exc.options, start=1):
            print(
                f"{i}. {option.get('display_name')} | "
                f"{option.get('osm_type')} {option.get('osm_id')} | "
                f"score={option.get('score')} importance={option.get('importance')}"
            )
        print("Re-run with a more specific --place, e.g. \"Cambridge, UK\".")
        raise SystemExit(2) from exc

    if result.get("status") != "ok":
        print(f"[error] {result.get('message', result)}", file=sys.stderr)
        raise SystemExit(1)

    print(f"[ok] saved {result['cache_path']}")
    print(f"[place] {result['place_name']}")
    print(f"[osm] {result.get('osm_type')} {result.get('osm_id')} | {result.get('display_name')}")
    print(f"[cache] {'hit' if result.get('cache_hit') else 'created'}")
    print("[attribution] Data (c) OpenStreetMap contributors, ODbL 1.0")


if __name__ == "__main__":
    main()
