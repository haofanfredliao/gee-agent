"""配置页：初次使用时填入 API 密钥，验证可用性，并写入 .env 文件。"""
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _ROOT / ".env"


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _load_env() -> dict:
    cfg = {}
    if _ENV_PATH.exists():
        with open(_ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip()] = v.strip().strip("\"'")
    return cfg


def _write_env(poe_key: str, geocoding_key: str, gee_project: str) -> None:
    lines = [
        "# 由配置页自动生成——请勿提交至 git",
        "",
        "# Poe API（LLM）",
        f"POE_API_KEY={poe_key}",
        "",
        "# GEE（Earth Engine 项目 ID）",
        f"GEE_PROJECT_ID={gee_project}",
        "",
        "# 地理编码（Google Maps Geocoding API）",
        f"GEOCODING_API_KEY={geocoding_key}",
        "",
        "# 后端地址（前端调用时使用）",
        "BACKEND_URL=http://127.0.0.1:8000",
        "",
        "# 默认模型名（与 configs/models.yaml 中 name 一致）",
        "DEFAULT_MODEL=default",
    ]
    with open(_ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── 验证函数 ──────────────────────────────────────────────────────────────────

def _validate_poe(key: str) -> tuple[bool, str]:
    if not key.strip():
        return False, "未填写"
    try:
        import httpx
        resp = httpx.post(
            "https://api.poe.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "gemini-3.1-flash-lite",
                "messages": [{"role": "user", "content": "Reply with just the word: ok"}],
                "max_tokens": 10,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"]
            return True, f"可用（回复：{reply[:40]}）"
        return False, f"HTTP {resp.status_code}：{resp.text[:120]}"
    except Exception as e:
        return False, str(e)[:150]


def _validate_geocoding(key: str) -> tuple[bool, str]:
    if not key.strip():
        return False, "未填写（地名定位功能将不可用）"
    try:
        import httpx
        resp = httpx.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": "Tokyo", "key": key},
            timeout=10,
        )
        data = resp.json()
        status = data.get("status")
        if status == "OK":
            loc = data["results"][0]["geometry"]["location"]
            return True, f"可用（东京坐标：{loc['lat']:.2f}°N, {loc['lng']:.2f}°E）"
        return False, f"API 返回状态：{status}  {data.get('error_message', '')}"
    except Exception as e:
        return False, str(e)[:150]


def _validate_gee(project_id: str) -> tuple[bool, str]:
    if not project_id.strip():
        return False, "未填写（GEE 卫星图像功能将不可用）"
    try:
        import ee
        ee.Initialize(project=project_id)
        return True, "GEE 初始化成功"
    except Exception as e:
        err_lower = str(e).lower()
        if any(w in err_lower for w in ("credentials", "authentication", "login", "token", "oauth")):
            return (
                False,
                "缺少 Google 认证凭证——项目 ID 本身不作为密钥使用。\n"
                "请先在终端运行 `gcloud auth application-default login` 完成授权后再验证。",
            )
        if "not found" in err_lower or "permission" in err_lower:
            return False, f"项目未找到或无权限，请检查项目 ID 是否正确：{project_id}"
        return False, str(e)[:150]


# ── Session State 初始化 ──────────────────────────────────────────────────────

_existing = _load_env()


def _init(k, v):
    if k not in st.session_state:
        st.session_state[k] = v


_init("poe_key", _existing.get("POE_API_KEY", ""))
_init("geocoding_key", _existing.get("GEOCODING_API_KEY", ""))
_init("gee_project", _existing.get("GEE_PROJECT_ID", ""))
_init("poe_status", None)   # None | (ok: bool, msg: str)
_init("geo_status", None)
_init("gee_status", None)
_init("saved", False)


# ── 页面布局 ──────────────────────────────────────────────────────────────────

# 模拟 layout="centered"（宽布局下限制内容宽度）
st.markdown(
    """
    <style>
    section[data-testid="stMain"] > div.block-container {
        max-width: 740px;
        padding-top: 2rem;
        margin: 0 auto;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⚙️ GEE 助手 · 初始配置")

if _ENV_PATH.exists() and _existing.get("POE_API_KEY"):
    st.info("已检测到现有配置，可在下方更新密钥并重新验证。")
else:
    st.warning("未检测到 `.env` 配置文件，请填写以下 API 密钥后保存以开始使用。")

st.divider()

# ── 1. Poe API Key ─────────────────────────────────────────────────────────────
st.subheader("🤖 Poe API Key  *(必填)*")
st.caption("用于 LLM 对话能力。前往 [poe.com/api_key](https://poe.com/api_key) 获取。")

c1, c2 = st.columns([4, 1])
with c1:
    st.text_input(
        "Poe API Key",
        key="poe_key",
        type="password",
        placeholder="TZ-...",
        label_visibility="collapsed",
    )
with c2:
    if st.button("验证", key="v_poe", use_container_width=True):
        with st.spinner("验证中…"):
            st.session_state["poe_status"] = _validate_poe(st.session_state["poe_key"])

if st.session_state["poe_status"] is not None:
    ok, msg = st.session_state["poe_status"]
    (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {msg}")

st.divider()

# ── 2. Google Geocoding API Key ────────────────────────────────────────────────
st.subheader("🗺️ Google Geocoding API Key  *(推荐)*")
st.caption("用于将地名解析为地图坐标。未填写时地图无法按地名定位，其他对话功能不受影响。")

c1, c2 = st.columns([4, 1])
with c1:
    st.text_input(
        "Geocoding API Key",
        key="geocoding_key",
        type="password",
        placeholder="AIzaSy...",
        label_visibility="collapsed",
    )
with c2:
    if st.button("验证", key="v_geo", use_container_width=True):
        with st.spinner("验证中…"):
            st.session_state["geo_status"] = _validate_geocoding(st.session_state["geocoding_key"])

if st.session_state["geo_status"] is not None:
    ok, msg = st.session_state["geo_status"]
    (st.success if ok else st.warning)(f"{'✅' if ok else '⚠️'} {msg}")

st.divider()

# ── 3. GEE Project ID ──────────────────────────────────────────────────────────
st.subheader("🌍 GEE Project ID  *(推荐)*")
st.caption("Google Earth Engine 项目 ID，用于卫星图像分析任务。未填写时 GEE 相关功能不可用。")

c1, c2 = st.columns([4, 1])
with c1:
    st.text_input(
        "GEE Project ID",
        key="gee_project",
        placeholder="your-gcp-project-id",
        label_visibility="collapsed",
    )
with c2:
    if st.button("验证", key="v_gee", use_container_width=True):
        with st.spinner("初始化 GEE（可能需要几秒）…"):
            st.session_state["gee_status"] = _validate_gee(st.session_state["gee_project"])

if st.session_state["gee_status"] is not None:
    ok, msg = st.session_state["gee_status"]
    (st.success if ok else st.warning)(f"{'✅' if ok else '⚠️'} {msg}")

with st.expander("ℹ️ 为什么 GEE 不像其他两项一样用 API Key？"):
    st.markdown(
        "Google Earth Engine 使用 **OAuth2 用户凭证**而非简单 API Key 进行认证。"
        " 项目 ID 用于计费和资源配额，但访问权限由登录账号决定。\n\n"
        "**本地开发（推荐）：**\n"
        "```bash\ngcloud auth application-default login\n```\n"
        "执行后浏览器会弹出 Google 登录授权，完成后凭证会保存到本机，"
        " earthengine-api 可自动读取。\n\n"
        "**服务器部署（高级）：**\n"
        "可使用 Service Account 密钥文件，需在代码层面额外配置（当前版本暂不支持）。"
    )

st.divider()

# ── 操作按钮区 ────────────────────────────────────────────────────────────────
c_all, c_save = st.columns(2)

with c_all:
    if st.button("🔍 验证全部", use_container_width=True):
        with st.spinner("逐项验证中，请稍候…"):
            st.session_state["poe_status"] = _validate_poe(st.session_state["poe_key"])
            st.session_state["geo_status"] = _validate_geocoding(st.session_state["geocoding_key"])
            st.session_state["gee_status"] = _validate_gee(st.session_state["gee_project"])
        st.rerun()

poe_filled = bool(st.session_state.get("poe_key", "").strip())

with c_save:
    if st.button(
        "💾 保存配置并开始",
        use_container_width=True,
        disabled=not poe_filled,
        type="primary",
    ):
        _write_env(
            st.session_state["poe_key"],
            st.session_state["geocoding_key"],
            st.session_state["gee_project"],
        )
        st.session_state["saved"] = True
        st.rerun()

if not poe_filled:
    st.caption("请先填写 Poe API Key 才能保存配置。")

# ── 保存成功提示 ──────────────────────────────────────────────────────────────
if st.session_state["saved"]:
    st.success("✅ `.env` 配置文件已保存到项目根目录！")
    st.info(
        "**下一步：** 如果后端服务尚未启动，请在终端中运行：\n"
        "```bash\nPYTHONPATH=. uvicorn backend.app.main:app --reload --port 8000\n```\n"
        "如果后端已在运行，需要**重启**以加载新配置。"
    )
    if st.button("🚀 前往 Chat Assistant", type="primary", use_container_width=True):
        st.session_state["saved"] = False
        st.switch_page("pages/1_Chat_Assistant.py")
