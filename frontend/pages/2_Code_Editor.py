"""代码编辑器：sidebar 含 ACE 代码编辑器，主区域全屏地图，可直接执行 GEE 沙箱代码。"""
import streamlit as st

# ── 全屏地图 CSS（与 Chat Assistant 保持一致）────────────────────────────────
st.markdown(
    """
    <style>
    header[data-testid="stHeader"]   { display: none !important; }
    footer                           { display: none !important; }
    section[data-testid="stMain"] > div.block-container {
        padding: 0 !important;
        max-width: 100% !important;
    }
    iframe { height: calc(100vh - 4px) !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

from streamlit_ace import st_ace
from frontend.components.map_view import render_map
from frontend.services.api_client import run_sandbox_code, get_basemap_config

# ── Session state 初始化 ──────────────────────────────────────────────────────
config = get_basemap_config()


def _init(key, val):
    if key not in st.session_state:
        st.session_state[key] = val


_init("editor_map_center_lat", config.get("center_lat", 22.3193))
_init("editor_map_center_lon", config.get("center_lon", 114.1694))
_init("editor_map_zoom", config.get("zoom", 10))
_init("editor_map_layers", [])
_init("editor_run_log", "")
_init("editor_run_status", None)   # None | "ok" | "error"
_init(
    "editor_code",
    """\
# 沙箱环境内置变量：
#   ee   —— 已初始化的 earthengine-api
#   Map  —— 拦截 addLayer 并将图层渲染到右侧地图
#
# 示例：加载 Sentinel-2 影像
image = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED") \\
    .filterDate("2024-01-01", "2024-03-01") \\
    .sort("CLOUDY_PIXEL_PERCENTAGE") \\
    .first()

vis = {"bands": ["B4", "B3", "B2"], "min": 0, "max": 3000}
Map.addLayer(image, vis, "Sentinel-2 真彩色")
""",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🖥️ GEE 代码编辑器")
    st.caption("代码在沙箱中执行，已内置 `ee`（已初始化）与 `Map`，无需手动 `ee.Initialize()`。")

    # ACE 编辑器
    new_code = st_ace(
        value=st.session_state["editor_code"],
        language="python",
        theme="monokai",
        key="ace_editor",
        height=400,
        font_size=13,
        tab_size=4,
        wrap=False,
        show_gutter=True,
        show_print_margin=False,
        auto_update=True,
        placeholder="在此输入 GEE Python 代码…",
    )
    # 仅当编辑器返回非空内容时更新（ACE 首次渲染返回 None）
    if new_code is not None:
        st.session_state["editor_code"] = new_code

    col_run, col_clear = st.columns([2, 1])
    run_clicked = col_run.button("▶ 运行", type="primary", use_container_width=True)
    clear_clicked = col_clear.button("清空图层", use_container_width=True)

    if clear_clicked:
        st.session_state["editor_map_layers"] = []
        st.session_state["editor_run_log"] = ""
        st.session_state["editor_run_status"] = None
        st.rerun()

    if run_clicked:
        code_to_run = st.session_state["editor_code"]
        if not code_to_run.strip():
            st.warning("请先输入代码。")
        else:
            with st.spinner("执行中…"):
                try:
                    result = run_sandbox_code(code_to_run)
                    st.session_state["editor_run_status"] = result.get("status")
                    st.session_state["editor_run_log"] = result.get("log", "")
                    layers = result.get("layers", [])
                    if layers:
                        st.session_state["editor_map_layers"] = layers
                except Exception as exc:
                    st.session_state["editor_run_status"] = "error"
                    st.session_state["editor_run_log"] = str(exc)
            st.rerun()

    # 执行结果/日志
    status = st.session_state["editor_run_status"]
    log = st.session_state["editor_run_log"]
    if status == "ok":
        layers_count = len(st.session_state["editor_map_layers"])
        st.success(f"执行成功，共 {layers_count} 个图层已加载到地图。")
    elif status == "error":
        st.error("执行出错")

    if log:
        with st.expander("📋 输出 / 错误日志", expanded=(status == "error")):
            st.code(log, language=None)

# ── 主区域：全屏地图 ──────────────────────────────────────────────────────────
render_map(
    center_lat=st.session_state["editor_map_center_lat"],
    center_lon=st.session_state["editor_map_center_lon"],
    zoom=st.session_state["editor_map_zoom"],
    layers=st.session_state["editor_map_layers"],
)
