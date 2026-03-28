"""入口：检查 .env 是否已配置 POE_API_KEY，自动路由到配置页或聊天页。"""
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="GEE Geo 助手", page_icon="🌍", layout="wide")

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


if _poe_configured():
    st.switch_page("pages/1_Chat_Assistant.py")
else:
    st.switch_page("pages/0_Setup.py")
