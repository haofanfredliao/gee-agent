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
from frontend.services.api_client import chat_stream, get_basemap_config
import uuid


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
_init("session_id",     str(uuid.uuid4()))


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

            # ── 流式接收并实时渲染工作流进度 ──────────────────────────────
            collected_steps: list = []
            final_resp: dict = {}
            plan_total: int = 0  # 用于显示当前步骤进度
            with messages_container:
                with st.chat_message("assistant"):
                    status_placeholder = st.empty()
                    reply_placeholder  = st.empty()

                    try:
                        # st.status 在 running 状态时立即可见，expand=True 展示细节
                        with status_placeholder.status("🔄 工作流执行中…", expanded=True) as wf_status:
                            for evt in chat_stream(
                                prompt,
                                session_id=st.session_state["session_id"],
                                map_context={
                                    "center_lat": st.session_state["map_center_lat"],
                                    "center_lon": st.session_state["map_center_lon"],
                                    "zoom":       st.session_state["map_zoom"],
                                },
                            ):
                                etype = evt.get("type")
                                edata = evt.get("data", {})

                                if etype == "routing":
                                    intent = edata.get("intent", "")
                                    label = "📡 意图识别：代码执行" if intent == "execution" else "📡 意图识别：知识问答"
                                    st.write(label)

                                elif etype == "planning":
                                    plan = edata.get("plan", [])
                                    plan_total = len(plan)  # 缓存总步数供后续显示
                                    st.write(f"📋 任务规划（共 {plan_total} 步）：")
                                    for i, s in enumerate(plan):
                                        st.write(f"  ⬜ 步骤 {i+1}：{s.get('description','')}")

                                elif etype == "step_start":
                                    idx = edata.get("index", 0)
                                    desc = edata.get("description", "")
                                    tool = edata.get("tool", "")
                                    total_label = f"/{plan_total}" if plan_total else ""
                                    wf_status.update(label=f"⏳ 步骤 {idx+1}{total_label}：{desc}")
                                    st.write(f"⏳ **步骤 {idx+1}**：{desc}  `{tool}`")

                                elif etype == "step_done":
                                    idx = edata.get("index", 0)
                                    desc = edata.get("description", "")
                                    tool = edata.get("tool", "")
                                    ok   = edata.get("success", False)
                                    icon = "✅" if ok else "❌"
                                    preview = (edata.get("output_preview") or "").strip()
                                    st.write(f"{icon} **步骤 {idx+1}**：{desc}  `{tool}`")
                                    if preview:
                                        st.code(preview, language=None)
                                    collected_steps.append(edata)

                                elif etype == "summarizing":
                                    wf_status.update(label="✍️ 正在汇总结果…")
                                    st.write("✍️ 汇总中…")

                                elif etype == "done":
                                    final_resp = edata
                                    total = len(collected_steps)
                                    wf_status.update(
                                        label=f"✅ 工作流完成（{total} 步）",
                                        state="complete",
                                        expanded=False,
                                    )

                                elif etype == "error":
                                    wf_status.update(label="❌ 工作流出错", state="error", expanded=True)
                                    st.error(edata.get("message", "未知错误"))

                        # 在 status 下方渲染最终回复
                        if final_resp.get("reply"):
                            reply_placeholder.markdown(final_resp["reply"])

                    except Exception as e:
                        status_placeholder.empty()
                        st.error(f"请求失败：{e}")

            # ── 保存消息并刷新地图 ────────────────────────────────────────
            if final_resp:
                ws = final_resp.get("workflow_status")
                if not ws and collected_steps:
                    ws = {
                        "intent": "execution",
                        "status": "terminated",
                        "steps_completed": len(collected_steps),
                        "steps_total": len(collected_steps),
                        "steps": collected_steps,
                    }
                st.session_state["messages"].append({
                    "role": "assistant",
                    "content": final_resp.get("reply", ""),
                    "workflow_status": ws,
                })
                _apply_map_update(final_resp.get("map_update"))
                st.rerun()

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
