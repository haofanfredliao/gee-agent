"""地图组件：folium 渲染，支持 GEE tile URL 图层叠加。"""
from typing import Any, Dict, List, Optional

import folium
import streamlit as st
from streamlit_folium import st_folium


def render_map(
    center_lat: float = 22.3193,
    center_lon: float = 114.1694,
    zoom: int = 10,
    layers: Optional[List[Dict[str, Any]]] = None,
    height: int = 700,
) -> None:
    """渲染 folium 地图，tiles 中若有 tile_url 则叠加为 GEE 图层。"""
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="CartoDB positron",
    )
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
                ).add_to(m)
        folium.LayerControl(collapsed=False).add_to(m)

    st_folium(m, width=None, height=height, returned_objects=[])


def render_map_with_bbox(
    center_lat: float,
    center_lon: float,
    bbox: Optional[List[float]] = None,
    zoom: int = 10,
) -> None:
    """带 bbox 时仍以中心点渲染。"""
    render_map(center_lat=center_lat, center_lon=center_lon, zoom=zoom)
