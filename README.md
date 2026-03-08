# GEE Geo 助手 Demo（MVP）

基于 PRD 搭建的 GEE + LangChain + FastAPI + Streamlit + Chroma 的 Geo 助手最小可用版本。

## 功能概览

- **Chat Assistant**：自然语言与 GEE 助手对话，可问概念、要代码、说地名定位并更新地图。
- **Map Explorer**：输入地名定位地图，可选加载 GEE 图层。
- **后端 API**：`/chat`、`/geo/resolve`、`/gee/run`、`/gee/basemap`、`/health`。

## 目录结构

```
gee-agent/
├── backend/app/          # FastAPI 应用
│   ├── main.py
│   ├── api/              # routes_chat, routes_geo, routes_gee
│   ├── core/             # config
│   ├── models/           # chat, geo, gee
│   ├── services/         # llm_client, embeddings, chroma_store, geocoding, gee_client
│   ├── rag/              # prompts, retriever, chains
│   └── agents/           # tools_geo, tools_gee, tools_kb, agent_gee_assistant
├── frontend/
│   ├── app.py            # Streamlit 入口
│   ├── pages/            # 1_Chat_Assistant.py, 2_Map_Explorer.py
│   └── components/       # chat_ui, map_view, sidebar
├── configs/              # settings.example.yaml, models.yaml, gee_tasks.yaml
├── scripts/              # build_chroma_index, test_gee_connection, test_geocoding
└── .env.example
```

## 环境与运行

### 1. 安装依赖

```bash
cd /Users/fred/Code/gee-agent
pip install -e .
# 或 pip install -r requirements.txt（见下）
```

若使用 `pyproject.toml` 已包含依赖，可：

```bash
pip install -e .
```

可选：复制 `configs/settings.example.yaml` 为 `configs/settings.yaml`，复制 `.env.example` 为 `.env` 并填写（如 `POE_API_KEY`、`GEOCODING_API_KEY`、GEE 相关）。

### 2. 构建知识库（首次建议执行）

```bash
PYTHONPATH=. python scripts/build_chroma_index.py
```

### 3. 启动后端

```bash
PYTHONPATH=. uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. 启动前端

新开终端：

```bash
PYTHONPATH=. streamlit run frontend/app.py
```

浏览器打开提示的地址（通常 http://localhost:8501），左侧进入 **Chat Assistant** 或 **Map Explorer**。

## 验证脚本

- **GEE 连接**：`PYTHONPATH=. python scripts/test_gee_connection.py`
- **地理编码**：`PYTHONPATH=. python scripts/test_geocoding.py` 或 `python scripts/test_geocoding.py "Hong Kong"`

## 基础功能案例（PRD）

1. **问概念**：输入「GEE 的 asset 是什么？」→ 结合 Chroma 检索 + LLM 回答。
2. **要代码**：输入「给一个最简单的在 GEE 中加载 SRTM 高程数据的示例。」→ 返回示例代码（或占位说明）。
3. **地名定位**：在 Map Explorer 或聊天里输入「香港九龙」→ 调用地理编码并更新地图中心。
4. **NDVI 示例**：输入「在这个区域做一个 2020-2022 年 NDVI 示例。」→ 调用 geo + GEE NDVI 任务并返回说明，地图可更新到该区域（瓦片叠加可后续做）。

## 依赖说明

- **Poe API**：未配置 `POE_API_KEY` 时，聊天为占位回复。
- **GEE**：未安装或未认证时，`load_asset` / `ndvi_example` 返回占位或错误信息。
- **Chroma**：需 `chromadb`，首次运行前执行 `build_chroma_index.py`。

可选 `requirements.txt`（与 pyproject.toml 对齐）：

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.0
pyyaml>=6.0
httpx>=0.26.0
streamlit>=1.28.0
pandas>=2.0.0
chromadb>=0.4.0
earthengine-api>=0.1.0
```
