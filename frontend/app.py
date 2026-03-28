"""入口：使用 st.navigation 管理页面，自身不出现在导航栏。"""
from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="GEE Geo 助手",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _ROOT / ".env"


def _poe_configured() -> bool:
    if not _ENV_PATH.exists():
        return False
    with open(_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("POE_API_KEY="):
                val = line.split("=", 1)[1].strip().strip("\"'")
                return bool(val and val not in ("...", ""))
    return False


setup_page = st.Page("pages/0_Setup.py", title="初始配置", icon="⚙️")
chat_page = st.Page("pages/1_Chat_Assistant.py", title="Chat Assistant", icon="🌍")

# 首次加载：按配置状态决定默认落地页（第一项为默认）
if _poe_configured():
    pg = st.navigation([chat_page, setup_page])
else:
    pg = st.navigation([setup_page, chat_page])

pg.run()
