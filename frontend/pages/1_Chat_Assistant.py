"""页面 1：Chat Assistant — 地图 + 聊天，支持地图联动。"""
import streamlit as st

from frontend.components.map_view import render_map
from frontend.components.chat_ui import render_chat, init_chat_state
from frontend.components.sidebar import render_sidebar
from frontend.services.api_client import chat, get_basemap_config

render_sidebar()

# 底图配置
config = get_basemap_config()
center_lat = config.get("center_lat", 22.3193)
center_lon = config.get("center_lon", 114.1694)
zoom = config.get("zoom", 10)

# 用 session_state 存当前地图状态，供 map_update 更新
if "map_center_lat" not in st.session_state:
    st.session_state["map_center_lat"] = center_lat
if "map_center_lon" not in st.session_state:
    st.session_state["map_center_lon"] = center_lon
if "map_zoom" not in st.session_state:
    st.session_state["map_zoom"] = zoom
if "map_layers" not in st.session_state:
    st.session_state["map_layers"] = []


def apply_map_update(update: dict) -> None:
    if not update:
        return
    st.session_state["map_center_lat"] = update.get("center_lat", st.session_state["map_center_lat"])
    st.session_state["map_center_lon"] = update.get("center_lon", st.session_state["map_center_lon"])
    st.session_state["map_zoom"] = update.get("zoom", st.session_state["map_zoom"])
    if update.get("layer_info"):
        st.session_state["map_layers"] = [update["layer_info"]]
    st.rerun()


def on_send(message: str):
    return chat(message, map_context={
        "center_lat": st.session_state["map_center_lat"],
        "center_lon": st.session_state["map_center_lon"],
        "zoom": st.session_state["map_zoom"],
    })


st.title("GEE Geo 助手")

# 布局：上方面积小一点给地图，下方聊天
col_map, col_chat = st.columns([1, 1])
with col_map:
    st.subheader("地图")
    render_map(
        center_lat=st.session_state["map_center_lat"],
        center_lon=st.session_state["map_center_lon"],
        zoom=st.session_state["map_zoom"],
        layers=st.session_state["map_layers"],
    )
with col_chat:
    st.subheader("对话")
    render_chat(on_send=on_send, placeholder_map_update=apply_map_update)
