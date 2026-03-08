"""页面 2：Map Explorer — 地名定位 + 地图。"""
import streamlit as st

from frontend.components.map_view import render_map_with_bbox
from frontend.components.sidebar import render_sidebar
from frontend.services.api_client import geo_resolve, run_gee_task

render_sidebar()

# 默认香港
if "explorer_center_lat" not in st.session_state:
    st.session_state["explorer_center_lat"] = 22.3193
if "explorer_center_lon" not in st.session_state:
    st.session_state["explorer_center_lon"] = 114.1694
if "explorer_zoom" not in st.session_state:
    st.session_state["explorer_zoom"] = 10

st.title("地图浏览")

place = st.text_input("输入地名", placeholder="例如：香港、九龙、北京")
if st.button("定位"):
    if place:
        try:
            resp = geo_resolve(place)
            st.session_state["explorer_center_lat"] = resp["center_lat"]
            st.session_state["explorer_center_lon"] = resp["center_lon"]
            st.session_state["explorer_zoom"] = 11
            st.success(f"已定位到 {place}")
        except Exception as e:
            st.error(f"解析失败: {e}")
    else:
        st.warning("请输入地名")

render_map_with_bbox(
    center_lat=st.session_state["explorer_center_lat"],
    center_lon=st.session_state["explorer_center_lon"],
    zoom=st.session_state["explorer_zoom"],
)

# 可选：触发默认 GEE 任务
if st.checkbox("加载默认高程图层 (SRTM)"):
    try:
        result = run_gee_task("load_asset", {"asset_id": "USGS/SRTMGL1_003"})
        if result.get("status") == "ok":
            st.success("任务已执行，图层 URL 见后端返回（地图叠加 TODO）")
        else:
            st.info(result.get("result", {}).get("message", str(result)))
    except Exception as e:
        st.error(str(e))
