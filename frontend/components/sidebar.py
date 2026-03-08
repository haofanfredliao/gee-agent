"""侧边栏：模型名称、地图参数等。"""
import streamlit as st
import os


def render_sidebar() -> None:
    """简单侧边栏：显示模型与地图默认信息。"""
    with st.sidebar:
        st.header("GEE Geo 助手")
        model_name = os.environ.get("DEFAULT_MODEL", "default")
        st.text(f"模型: {model_name}")
        st.caption("可在 configs/models.yaml 中配置")
        st.divider()
        st.subheader("地图")
        st.caption("默认中心: 香港 | 缩放级别可在页面中更新")
        # 可选：起始日期范围等
        # st.date_input("起始日期", value=...)
