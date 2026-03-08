"""Streamlit 入口：多页应用，页面 1 聊天助手，页面 2 地图浏览。"""
import streamlit as st

st.set_page_config(page_title="GEE Geo 助手", page_icon="🌍", layout="wide")

st.markdown("# GEE Geo 助手 Demo")
st.markdown("左侧选择 **Chat Assistant** 或 **Map Explorer** 开始使用。")
st.sidebar.markdown("## 页面")
st.sidebar.page_link("app.py", label="首页", icon="🏠")
st.sidebar.page_link("pages/1_Chat_Assistant.py", label="Chat Assistant", icon="💬")
st.sidebar.page_link("pages/2_Map_Explorer.py", label="Map Explorer", icon="🗺️")
