"""地图组件：st.map 或 pydeck 展示，支持中心、缩放与图层。"""
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd


def render_map(
    center_lat: float = 22.3193,
    center_lon: float = 114.1694,
    zoom: int = 10,
    layers: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    渲染地图：默认以 (center_lat, center_lon) 为中心，zoom 为缩放级别。
    layers 中若有 tile_url 等可后续叠加（当前为 TODO）。
    """
    # 使用 st.map 时需要一个 DataFrame，列至少包含 lat, lon
    df = pd.DataFrame({"lat": [center_lat], "lon": [center_lon]})
    st.map(df, zoom=zoom, use_container_width=True)
    # TODO: 若 layers 中有 tile_url，用 pydeck 或 folium 叠加瓦片图层
    if layers:
        for layer in layers:
            if layer.get("tile_url"):
                st.caption(f"图层 URL: {layer['tile_url'][:80]}...")


def render_map_with_bbox(
    center_lat: float,
    center_lon: float,
    bbox: Optional[List[float]] = None,
    zoom: int = 10,
) -> None:
    """带 bbox 时仍以中心点渲染，bbox 可用来计算合适 zoom（简化实现）。"""
    render_map(center_lat=center_lat, center_lon=center_lon, zoom=zoom)
