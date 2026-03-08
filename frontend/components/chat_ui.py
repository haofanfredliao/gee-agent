"""聊天 UI：历史消息列表 + 输入框 + 发送。"""
from typing import Any, Callable, Dict, List, Optional

import streamlit as st


def init_chat_state() -> None:
    """初始化 session state 中的对话历史。"""
    if "messages" not in st.session_state:
        st.session_state["messages"] = []


def render_chat(
    on_send: Callable[[str], Dict[str, Any]],
    placeholder_map_update: Optional[Callable[[Dict], None]] = None,
) -> None:
    """
    渲染聊天区域：展示历史，输入框发送后调用 on_send(msg)，
    若返回中有 map_update 则调用 placeholder_map_update(update)。
    """
    init_chat_state()
    for msg in st.session_state["messages"]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        with st.chat_message(role):
            st.markdown(content)

    if prompt := st.chat_input("输入消息，与 GEE 助手对话..."):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            try:
                resp = on_send(prompt)
                reply = resp.get("reply", "")
                st.markdown(reply)
                st.session_state["messages"].append({"role": "assistant", "content": reply})
                map_update = resp.get("map_update")
                if map_update and placeholder_map_update:
                    placeholder_map_update(map_update)
            except Exception as e:
                st.error(f"请求失败: {e}")
