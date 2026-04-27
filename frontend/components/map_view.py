"""地图组件：folium 渲染，支持 GEE tile URL 图层叠加。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from branca.element import MacroElement, Template
import folium
import streamlit as st
from streamlit_folium import st_folium


class _BottomLeftZoom(MacroElement):
    """Move Leaflet zoom controls away from Streamlit's top chrome."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        L.control.zoom({ position: "bottomleft" }).addTo({{ this._parent.get_name() }});
        {% endmacro %}
        """
    )


class _FitBounds(MacroElement):
    """Fit the map to an AOI bbox after Leaflet finishes sizing the iframe."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        const aoiBounds = L.latLngBounds({{ this.bounds_json | safe }});
        const fitOptions = {
            paddingTopLeft: [48, 42],
            paddingBottomRight: [48, 92],
            maxZoom: {{ this.max_zoom }}
        };
        const map = {{ this._parent.get_name() }};
        map.fitBounds(aoiBounds, fitOptions);
        setTimeout(function() {
            map.invalidateSize();
            map.fitBounds(aoiBounds, fitOptions);
        }, 80);
        {% endmacro %}
        """
    )

    def __init__(self, bbox: List[float], max_zoom: int = 13) -> None:
        super().__init__()
        min_lon, min_lat, max_lon, max_lat = [float(v) for v in bbox]
        self.bounds_json = json.dumps([[min_lat, min_lon], [max_lat, max_lon]])
        self.max_zoom = max_zoom


class _CollapsibleLegend(MacroElement):
    """Leaflet control for compact, collapsible palette legends."""

    _template = Template(
        """
        {% macro html(this, kwargs) %}
        <style>
        .gee-legend-control {
            background: rgba(255, 255, 255, 0.94);
            border: 1px solid rgba(15, 23, 42, 0.12);
            border-radius: 14px;
            box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
            color: #1f2937;
            font-family: "Aptos", "Segoe UI", sans-serif;
            max-width: 280px;
            overflow: hidden;
            backdrop-filter: blur(10px);
        }
        .gee-legend-header {
            align-items: center;
            cursor: pointer;
            display: flex;
            gap: 10px;
            justify-content: space-between;
            line-height: 1;
            padding: 10px 12px;
            user-select: none;
        }
        .gee-legend-title {
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.02em;
        }
        .gee-legend-pill {
            background: #e6f4ef;
            border-radius: 999px;
            color: #0f766e;
            font-size: 11px;
            font-weight: 700;
            padding: 3px 7px;
        }
        .gee-legend-caret {
            color: #64748b;
            font-size: 12px;
            transition: transform 0.18s ease;
        }
        .gee-legend-control.is-collapsed .gee-legend-caret {
            transform: rotate(-90deg);
        }
        .gee-legend-content {
            border-top: 1px solid rgba(15, 23, 42, 0.08);
            max-height: 280px;
            overflow-y: auto;
            padding: 10px 12px 12px;
        }
        .gee-legend-control.is-collapsed .gee-legend-content {
            display: none;
        }
        .gee-legend-card + .gee-legend-card {
            margin-top: 12px;
        }
        .gee-legend-layer {
            color: #334155;
            font-size: 12px;
            font-weight: 650;
            margin-bottom: 6px;
            max-width: 238px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .gee-legend-gradient {
            border: 1px solid rgba(15, 23, 42, 0.15);
            border-radius: 999px;
            height: 12px;
            overflow: hidden;
        }
        .gee-legend-scale {
            color: #64748b;
            display: flex;
            font-size: 11px;
            justify-content: space-between;
            margin-top: 4px;
        }
        .leaflet-bottom.leaflet-left {
            margin-bottom: 18px;
        }
        .leaflet-top.leaflet-right {
            margin-top: 12px;
            margin-right: 12px;
        }
        .leaflet-bottom.leaflet-right {
            margin-bottom: 18px;
            margin-right: 12px;
        }
        </style>
        {% endmacro %}

        {% macro script(this, kwargs) %}
        const legendItems = {{ this.items_json | safe }};
        const legend = L.control({ position: "bottomright" });
        legend.onAdd = function(map) {
            const div = L.DomUtil.create("div", "gee-legend-control is-collapsed");
            const header = L.DomUtil.create("div", "gee-legend-header", div);
            const title = L.DomUtil.create("div", "gee-legend-title", header);
            title.textContent = "图例";
            const pill = L.DomUtil.create("span", "gee-legend-pill", header);
            pill.textContent = String(legendItems.length);
            const caret = L.DomUtil.create("span", "gee-legend-caret", header);
            caret.textContent = "▾";
            const content = L.DomUtil.create("div", "gee-legend-content", div);

            legendItems.forEach(function(item) {
                const card = L.DomUtil.create("div", "gee-legend-card", content);
                const layer = L.DomUtil.create("div", "gee-legend-layer", card);
                layer.title = item.name;
                layer.textContent = item.name;
                const gradient = L.DomUtil.create("div", "gee-legend-gradient", card);
                gradient.style.background = "linear-gradient(to right, " + item.colors.join(", ") + ")";
                const scale = L.DomUtil.create("div", "gee-legend-scale", card);
                const minLabel = L.DomUtil.create("span", "", scale);
                minLabel.textContent = item.min_label;
                const maxLabel = L.DomUtil.create("span", "", scale);
                maxLabel.textContent = item.max_label;
            });

            header.addEventListener("click", function(evt) {
                L.DomEvent.stop(evt);
                div.classList.toggle("is-collapsed");
            });
            L.DomEvent.disableClickPropagation(div);
            L.DomEvent.disableScrollPropagation(div);
            return div;
        };
        legend.addTo({{ this._parent.get_name() }});
        {% endmacro %}
        """
    )

    def __init__(self, items: List[Dict[str, Any]]) -> None:
        super().__init__()
        self.items_json = json.dumps(items, ensure_ascii=False)


def _normalize_color(color: Any) -> Optional[str]:
    if color is None:
        return None
    text = str(color).strip()
    if not text:
        return None
    if text.startswith("#") or text.startswith("rgb") or text.startswith("hsl"):
        return text
    if re.fullmatch(r"[0-9a-fA-F]{3,8}", text):
        return f"#{text}"
    return text


def _palette_list(palette: Any) -> List[str]:
    if isinstance(palette, str):
        raw_colors = [c.strip() for c in palette.split(",")]
    elif isinstance(palette, list):
        raw_colors = palette
    else:
        return []
    colors = [_normalize_color(c) for c in raw_colors]
    return [c for c in colors if c]


def _first_scalar(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _format_label(value: Any) -> str:
    value = _first_scalar(value)
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(num) >= 100:
        return f"{num:.0f}"
    return f"{num:.2f}".rstrip("0").rstrip(".")


def _fallback_vis_from_name(name: str) -> Dict[str, Any]:
    upper = (name or "").upper()
    if "LAI" in upper or "叶面积" in name:
        return {"min": 0, "max": 6, "palette": ["ffffe5", "c7e9b4", "41ab5d", "006837"]}
    if "NDBI" in upper or "建筑" in name or "建成" in name:
        return {"min": -0.5, "max": 0.5, "palette": ["2166ac", "f7f7f7", "b2182b"]}
    if "MNDWI" in upper:
        return {"min": -0.5, "max": 0.5, "palette": ["a6611a", "f5f5f5", "4393c3", "053061"]}
    if "NDWI" in upper:
        return {"min": -0.5, "max": 0.5, "palette": ["8c510a", "f6e8c3", "67a9cf", "016c9c"]}
    if "NDMI" in upper:
        return {"min": -0.6, "max": 0.6, "palette": ["8c510a", "f6e8c3", "80cdc1", "01665e"]}
    if "NBR" in upper:
        return {"min": -0.5, "max": 0.8, "palette": ["7f0000", "d7301f", "fdae6b", "ffffcc", "1a9850"]}
    if "BSI" in upper or "裸土" in name:
        return {"min": -0.5, "max": 0.5, "palette": ["2166ac", "f7f7f7", "b35806"]}
    if any(token in upper for token in ("EVI", "SAVI", "MSAVI", "GNDVI", "NDRE", "NDVI")) or "植被" in name:
        return {"min": -0.2, "max": 0.8, "palette": ["8c510a", "d8b365", "f6e8c3", "c7eae5", "5ab4ac", "01665e"]}
    return {}


def _legend_items(layers: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for i, layer in enumerate(layers or []):
        name = str(layer.get("name") or f"GEE 图层 {i + 1}")
        vis = layer.get("vis_params") or _fallback_vis_from_name(name)
        colors = _palette_list(vis.get("palette"))
        if len(colors) < 2:
            continue
        items.append(
            {
                "name": name,
                "colors": colors,
                "min_label": _format_label(vis.get("min")),
                "max_label": _format_label(vis.get("max")),
            }
        )
    return items


def render_map(
    center_lat: float = 22.3193,
    center_lon: float = 114.1694,
    zoom: int = 10,
    layers: Optional[List[Dict[str, Any]]] = None,
    bbox: Optional[List[float]] = None,
    height: int = 700,
) -> None:
    """渲染 folium 地图，tiles 中若有 tile_url 则叠加为 GEE 图层。"""
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="CartoDB positron",
        zoom_control=False,
        control_scale=True,
    )
    _BottomLeftZoom().add_to(m)

    if layers:
        for i, layer in enumerate(layers):
            tile_url = layer.get("tile_url")
            if tile_url:
                folium.TileLayer(
                    tiles=tile_url,
                    attr="Google Earth Engine",
                    name=layer.get("name", f"GEE 图层 {i + 1}"),
                    overlay=True,
                    control=True,
                    tms=False,
                    no_wrap=True,
                    opacity=float(layer.get("opacity", 1.0)),
                ).add_to(m)
        folium.LayerControl(collapsed=True, position="topright").add_to(m)

    legend_items = _legend_items(layers)
    if legend_items:
        _CollapsibleLegend(legend_items).add_to(m)

    if isinstance(bbox, list) and len(bbox) == 4:
        try:
            _FitBounds(bbox).add_to(m)
        except (TypeError, ValueError):
            pass

    st_folium(m, width=None, height=height, returned_objects=[])


def render_map_with_bbox(
    center_lat: float,
    center_lon: float,
    bbox: Optional[List[float]] = None,
    zoom: int = 10,
) -> None:
    """带 bbox 时仍以中心点渲染。"""
    render_map(center_lat=center_lat, center_lon=center_lon, zoom=zoom, bbox=bbox)
