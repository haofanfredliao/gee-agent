"""Chat Assistant：sidebar 聊天（含历史 tab），主区域全屏地图。"""
import streamlit as st

st.set_page_config(
    page_title="GEE Geo 助手",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 全屏地图 CSS ───────────────────────────────────────────────────────────────
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

from frontend.components.map_view import render_map
from frontend.services.api_client import chat, get_basemap_config


def _render_assistant_message(msg: dict) -> None:
    """渲染助手消息：若包含 workflow_status，先展示分步骤状态，再展示最终回复。"""
    ws = msg.get("workflow_status")
    if ws and ws.get("intent") == "execution" and ws.get("steps"):
        steps = ws["steps"]
        total = ws.get("steps_total", len(steps))
        completed = ws.get("steps_completed", len(steps))
        label = f"工作流执行完成（{completed}/{total} 步）"
        # st.status 在 expanded=False 时以折叠态显示已完成的过程
        with st.status(label, state="complete", expanded=False):
            for s in steps:
                icon = "✅" if s.get("success") else "❌"
                tool_tag = f"`{s.get('tool', '')}`" if s.get("tool") else ""
                st.markdown(f"{icon} **步骤 {s['index'] + 1}**：{s.get('description', '')}  {tool_tag}")
                preview = (s.get("output_preview") or "").strip()
                if preview:
                    st.code(preview, language=None)
    st.markdown(msg["content"])

# ── Session state 初始化 ───────────────────────────────────────────────────────
config = get_basemap_config()

def _init(key, val):
    if key not in st.session_state:
        st.session_state[key] = val

_init("map_center_lat", config.get("center_lat", 22.3193))
_init("map_center_lon", config.get("center_lon", 114.1694))
_init("map_zoom",       config.get("zoom", 10))
_init("map_layers",     [])
_init("messages",       [])
_init("history",        [])


def _apply_map_update(update: dict) -> None:
    if not update:
        return
    st.session_state["map_center_lat"] = update.get("center_lat", st.session_state["map_center_lat"])
    st.session_state["map_center_lon"] = update.get("center_lon", st.session_state["map_center_lon"])
    st.session_state["map_zoom"]        = update.get("zoom",       st.session_state["map_zoom"])
    if update.get("layer_info"):
        st.session_state["map_layers"] = [update["layer_info"]]


def _save_to_history():
    msgs = st.session_state["messages"]
    if not msgs:
        return
    title = next(
        (m["content"][:30] for m in msgs if m["role"] == "user"),
        "新对话",
    )
    st.session_state["history"].insert(0, {"title": title, "messages": msgs.copy()})
    st.session_state["messages"] = []
    st.session_state["map_layers"] = []


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    tab_chat, tab_history = st.tabs(["💬 当前对话", "📋 历史记录"])

    # ── Tab 1：当前对话 ──
    with tab_chat:
        messages_container = st.container(height=480)
        with messages_container:
            for msg in st.session_state["messages"]:
                with st.chat_message(msg["role"]):
                    if msg["role"] == "assistant":
                        _render_assistant_message(msg)
                    else:
                        st.markdown(msg["content"])

        prompt = st.chat_input("与 GEE 助手对话…")
        if prompt:
            st.session_state["messages"].append({"role": "user", "content": prompt})
            with messages_container:
                with st.chat_message("user"):
                    st.markdown(prompt)
            try:
                resp = chat(
                    prompt,
                    map_context={
                        "center_lat": st.session_state["map_center_lat"],
                        "center_lon": st.session_state["map_center_lon"],
                        "zoom":       st.session_state["map_zoom"],
                    },
                )
                reply = resp.get("reply", "")
                assistant_msg = {
                    "role": "assistant",
                    "content": reply,
                    "workflow_status": resp.get("workflow_status"),
                }
                st.session_state["messages"].append(assistant_msg)
                with messages_container:
                    with st.chat_message("assistant"):
                        _render_assistant_message(assistant_msg)
                _apply_map_update(resp.get("map_update"))
                st.rerun()
            except Exception as e:
                with messages_container:
                    st.error(f"请求失败：{e}")

        col_new, col_save = st.columns(2)
        with col_new:
            if st.button("🆕 新对话", use_container_width=True):
                _save_to_history()
                st.rerun()
        with col_save:
            if st.button("💾 保存历史", use_container_width=True):
                _save_to_history()
                st.rerun()

    # ── Tab 2：历史记录 ──
    with tab_history:
        history = st.session_state["history"]
        if not history:
            st.caption("暂无历史记录")
        else:
            for i, session in enumerate(history):
                if st.button(f"📝 {session['title']}", key=f"hist_{i}", use_container_width=True):
                    st.session_state["messages"] = session["messages"].copy()
                    st.rerun()

# ── 主区域：全屏地图 ───────────────────────────────────────────────────────────
render_map(
    center_lat=st.session_state["map_center_lat"],
    center_lon=st.session_state["map_center_lon"],
    zoom=st.session_state["map_zoom"],
    layers=st.session_state["map_layers"],
    height=900,
)
