# 项目名称

使用 GEE + LangChain + FastAPI + Streamlit + Chroma 构建的「GEE Geo 助手 Demo」。

***

# 目标与核心体验

1. 用户打开 Web 应用后，默认看到一个 **GEE basemap**（基础底图）。  
2. 右侧或底部存在一个 **LLM 聊天框**，用户用自然语言与「GEE 助手」对话。  
3. 用户可以通过自然语言：
   - 询问 GEE 相关概念（如资产、数据集、NDVI 等）。  
   - 请求生成简单的 GEE 代码（例如加载某个官方数据集、按时间过滤）。  
   - 提供地名，系统自动定位地图到该区域，并加载相关图层。  

***

# 技术栈与总体架构

- 后端：
  - Python
  - FastAPI（REST API）
  - LangChain（RAG + Agent）
  - Chroma（向量知识库）
  - GEE（Google Earth Engine Python API）
  - Geocoding 服务（可用 Google Geocoding 或任意简单 HTTP API，占位实现即可）[5][6][7]
- 前端：
  - Streamlit（单页或多页应用）
  - 地图组件：`st.map` 或 pydeck-based map（先用简单版本即可）[8][9]

高层结构参考：「FastAPI + Streamlit + LangChain + 向量库」的常见实践。[2][3][10][11][1]

***

# 目录结构要求

请按以下结构生成代码骨架（可以只写出关键文件，内容允许是 TODO 占位）：

```
gee-assistant-demo/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── routes_chat.py
│   │   │   ├── routes_geo.py
│   │   │   ├── routes_gee.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   ├── models/
│   │   │   ├── chat.py
│   │   │   ├── geo.py
│   │   │   ├── gee.py
│   │   ├── services/
│   │   │   ├── llm_client.py
│   │   │   ├── embeddings.py
│   │   │   ├── chroma_store.py
│   │   │   ├── geocoding.py
│   │   │   ├── gee_client.py
│   │   ├── rag/
│   │   │   ├── retriever.py
│   │   │   ├── chains.py
│   │   │   ├── prompts.py
│   │   ├── agents/
│   │   │   ├── tools_geo.py
│   │   │   ├── tools_gee.py
│   │   │   ├── tools_kb.py
│   │   │   ├── agent_gee_assistant.py
│   │   ├── utils/
│   │   │   ├── geo_utils.py
│   │   │   ├── formatters.py
│   │   └── __init__.py
│   └── tests/
├── frontend/
│   ├── app.py
│   ├── pages/
│   │   ├── 1_Chat_Assistant.py
│   │   ├── 2_Map_Explorer.py
│   ├── components/
│   │   ├── chat_ui.py
│   │   ├── map_view.py
│   │   ├── sidebar.py
│   ├── services/
│   │   ├── api_client.py
│   └── __init__.py
├── configs/
│   ├── settings.example.yaml
│   ├── models.yaml
│   ├── gee_tasks.yaml
├── scripts/
│   ├── build_chroma_index.py
│   ├── test_gee_connection.py
│   ├── test_geocoding.py
├── .env.example
├── requirements.txt
└── README.md
```

***

# 环境与配置要求

1. `.env.example` 中至少包含（不写真实值，仅占位）：
   - `POE_API_KEY=...`  
   - `GEE_PROJECT_ID=...`  
   - `GEE_SERVICE_ACCOUNT=...`（如需）  
   - `GEE_PRIVATE_KEY_PATH=...`（如需）  
   - `GEOCODING_API_KEY=...`（如需）

2. `configs/settings.example.yaml`：
   - backend 监听端口
   - 前端调用的 backend URL
   - 默认地图中心、默认缩放级别（例如香港区域）
   - 默认模型名称（Poe 中的某个模型占位名）

3. `configs/models.yaml`：
   - 一条或多条 LLM 配置记录：`name`、`provider`（poe）、`model_id`。  
   - 一条 embedding 模型配置（可以假定使用 OpenAI 或其他，按占位配置即可）。[4]

4. `configs/gee_tasks.yaml`：
   - 定义一些简单任务模板（详见后文「基础功能案例」）。

***

# 后端详细需求（FastAPI）

## 1. FastAPI 启动与健康检查

文件：`backend/app/main.py`

需求：

- 创建 FastAPI app，对外暴露：
  - `GET /health`：返回 `{"status": "ok"}`。  
- 挂载路由模块：
  - `/chat` → `routes_chat.py`
  - `/geo` → `routes_geo.py`
  - `/gee` → `routes_gee.py`

***

## 2. 数据模型（Pydantic）

文件：`backend/app/models/chat.py`, `geo.py`, `gee.py`

请定义以下基础模型：

1. `ChatRequest`
   - 字段：
     - `message: str` 用户输入
     - `session_id: Optional[str]` 会话 ID（可选）
     - `map_context: Optional[MapContext]`（可选，用于联动地图）
2. `ChatResponse`
   - 字段：
     - `reply: str` LLM 回复
     - `map_update: Optional[MapUpdate]`（是否需要更新地图）
3. `MapContext`
   - `center_lat: Optional[float]`
   - `center_lon: Optional[float]`
   - `zoom: Optional[int]`
4. `MapUpdate`
   - `center_lat: float`
   - `center_lon: float`
   - `zoom: int`
   - `layer_info: Optional[Dict[str, Any]]`（例如 GEE 瓦片 URL）

Geo 模型：

1. `GeoQueryRequest`
   - `place_name: str`
2. `GeoQueryResponse`
   - `center_lat: float`
   - `center_lon: float`
   - `bbox: List[float]`（`[min_lon, min_lat, max_lon, max_lat]`）

GEE 模型：

1. `GeeTaskRequest`
   - `task_type: str`（例如 `"load_asset"`, `"ndvi_timeseries"`）
   - `params: Dict[str, Any]`
2. `GeeTaskResponse`
   - `status: str`
   - `result: Dict[str, Any]`（可包含 `tile_url`, `stats` 等）

***

## 3. 服务封装

### 3.1 LLM 客户端

文件：`services/llm_client.py`

- 封装对 Poe API 的调用。  
- 提供函数 `async def chat_with_llm(prompt: str, model_name: str) -> str:`  
- 内部读取 `.env` 或 `configs/models.yaml` 中的模型配置。  

（实际 Poe 接口细节可以先用伪实现或 TODO 占位，保证函数签名稳定。）

### 3.2 Embeddings + Chroma

文件：`services/embeddings.py`, `services/chroma_store.py`

- `embeddings.py`：
  - 提供函数 `get_embedding(text: str) -> List[float]`（可先用假实现或简单占位）。  

- `chroma_store.py`：
  - 初始化一个本地 Chroma 向量库（存储路径可配置）。  
  - 提供函数：
    - `add_documents(docs: List[str], metadatas: List[dict]) -> None`  
    - `similarity_search(query: str, k: int = 3) -> List[dict]` （返回文档和 metadata）

后续由 `scripts/build_chroma_index.py` 调用 `add_documents` 预先构建「GEE 文档/代码片段知识库」。[12][13][4]

### 3.3 Geocoding 服务

文件：`services/geocoding.py`

- 提供函数 `def geocode_place_name(place_name: str) -> Tuple[float, float, List[float]]:`  
  - 返回 `(center_lat, center_lon, bbox)`  
- 内部可以调用任意公开 geocoding API（可占位），保证函数签名和基本异常处理。[6][7][5]

### 3.4 GEE 客户端封装

文件：`services/gee_client.py`

- 提供以下示例函数（内部可以先写伪代码 + TODO）：  
  1. `def init_gee_client():` 初始化 GEE（使用服务账户或默认认证方式）。  
  2. `def get_basemap_config() -> Dict[str, Any]:`  
     - 返回前端初始化底图需要的配置信息（例如默认中心、缩放等级）。  
  3. `def load_simple_asset(asset_id: str) -> Dict[str, Any]:`  
     - 示例：加载一个 GEE 官方 asset（如 `USGS/SRTMGL1_003`），并返回用于前端显示的 `tile_url` 或 layer 配置。  
  4. `def run_ndvi_example(bbox, start_date, end_date) -> Dict[str, Any]:`  
     - 使用一个简单数据集（如 `MODIS/006/MOD13Q1` 或 `LANDSAT` 之一）计算 NDVI 的示例实现，返回图层或统计结果。[14][15]

***

## 4. RAG 与 Agent

### 4.1 Prompts

文件：`rag/prompts.py`

定义至少两个 prompt 模板（字符串）：

1. `SYSTEM_PROMPT_GEE_ASSISTANT`：
   - 角色：你是 Google Earth Engine 的助手。  
   - 能力：回答 GEE 概念问题、解释代码、根据用户需求生成简单 GEE 代码片段。  
   - 要求：尽量使用简洁、可直接复制的代码片段。

2. `SYSTEM_PROMPT_CODE_EXPLAINER`：
   - 用于解释 GEE 代码的作用。

### 4.2 Retriever

文件：`rag/retriever.py`

- 使用 Chroma 作为向量库。  
- 提供 `get_relevant_docs(query: str, k: int = 3)` → 返回字符串列表（供 LangChain 使用）。[4]

### 4.3 Chains

文件：`rag/chains.py`

- 定义一个基础 RAG chain 函数 `async def run_rag(query: str) -> str:`：
  - 调用 `retriever.get_relevant_docs` 获取文档。  
  - 将用户问题 + 检索到的文档拼到 prompt 里。  
  - 调用 `llm_client.chat_with_llm` 得到回答。  

### 4.4 Agent & Tools

文件：`agents/tools_geo.py`, `tools_gee.py`, `tools_kb.py`, `agent_gee_assistant.py`

简化版本需求：

1. `tools_geo.py`
   - Tool：`geo_lookup(place_name: str) -> Dict`  
   - 内部调用 `geocoding.geocode_place_name`，返回 center + bbox。  

2. `tools_gee.py`
   - Tool：`gee_load_simple_asset(asset_id: str) -> Dict`  
   - Tool：`gee_run_ndvi_example(bbox, start_date, end_date) -> Dict`  
   - 内部调用 `gee_client.load_simple_asset` 和 `gee_client.run_ndvi_example`。  

3. `tools_kb.py`
   - Tool：`kb_search(query: str) -> str`  
   - 内部调用 `chroma_store.similarity_search` 并返回拼接后的文本。  

4. `agent_gee_assistant.py`
   - 定义一个函数 `async def run_gee_agent(query: str) -> ChatResponse:`  
   - 逻辑（可简化）：
     - 使用规则：如果 query 中包含「香港」「九龙」「某某地名」等，可以调用 `geo_lookup`。  
     - 如果 query 提到「加载某个 asset」或「NDVI」，调用对应 GEE tool。  
     - 同时通过 `kb_search` 获取 GEE 文档片段，帮助回答解释性问题。  
     - 最终组合一个 `ChatResponse`，必要时填充 `map_update`（比如需要定位到某区域）。  

这里不要求完整 LangChain Agent 代码，可以先写手工路由（`if/else`）的「简易 agent」，重点是留好接口和结构。[16][17]

***

## 5. API 路由

### 5.1 `/chat`（routes_chat.py）

- `POST /chat`  
- 请求：`ChatRequest`  
- 响应：`ChatResponse`  

逻辑：

1. 从请求中取出 `message`（用户问题）。  
2. 调用 `agent_gee_assistant.run_gee_agent(message)`。  
3. 返回 `ChatResponse`。  

### 5.2 `/geo`（routes_geo.py）

- `POST /geo/resolve`  
- 请求：`GeoQueryRequest`  
- 响应：`GeoQueryResponse`  

逻辑：

1. 调用 `geocoding.geocode_place_name`。  
2. 返回 center + bbox。  

### 5.3 `/gee`（routes_gee.py）

- `POST /gee/run`  
- 请求：`GeeTaskRequest`  
- 响应：`GeeTaskResponse`  

简化实现：

- 匹配 `task_type`：
  - `"load_asset"` → 调 `gee_client.load_simple_asset`。  
  - `"ndvi_example"` → 调 `gee_client.run_ndvi_example`。  

***

# 前端详细需求（Streamlit）

## 1. 总体

入口：`frontend/app.py`，采用多页模式：

- 页面 1：`1_Chat_Assistant.py`  
- 页面 2：`2_Map_Explorer.py`  

引用 `components/` 和 `services/api_client.py`。[9][18][8]

***

## 2. API Client

文件：`frontend/services/api_client.py`

- 提供函数：
  - `chat(message: str, session_state) -> ChatResponse`  
  - `geo_resolve(place_name: str) -> dict`  
  - `run_gee_task(task_type: str, params: dict) -> dict`  

使用 `requests` 或 `httpx` 调用后端。

***

## 3. 组件

### 3.1 聊天 UI

文件：`components/chat_ui.py`

- 封装：
  - 显示历史对话（简单列表：用户消息、助手消息）。  
  - 输入框 + 发送按钮。  
- 使用 Session State 存储对话历史。

### 3.2 地图组件

文件：`components/map_view.py`

需求：

1. 函数 `render_map(center_lat, center_lon, zoom, layers=None)`：  
   - 使用 `st.map` 或 pydeck 展示一个地图，默认以配置中的中心点为初始视图。  
   - 后续如果 `layers` 中传有 `tile_url` 等，可叠加展示（可以先留 TODO 注释）。  

2. 应在 `1_Chat_Assistant.py` 页面引入，用于在助手响应里触发地图更新时展示。  
3. 应在 `2_Map_Explorer.py` 页面作为主视图，支持用户输入地名后 zoom。

### 3.3 Sidebar

文件：`components/sidebar.py`

- 简单实现：
  - 显示模型名称（只读或下拉）。  
  - 地图相关参数（起始中心、起始日期范围等，初期也可以只显示信息）。

***

## 4. 页面：Chat Assistant

文件：`pages/1_Chat_Assistant.py`

需求：

1. 页面加载时：
   - 调用 `gee_client.get_basemap_config` 或在前端配置中读取默认 center/zoom。  
   - 渲染基础地图（GEE basemap 概念层面，实际可以先用普通底图）。  

2. 主布局：
   - 左侧或上方：地图区域（使用 `map_view.render_map`）。  
   - 右侧或下方：聊天区域（使用 `chat_ui`）。  

3. 交互逻辑：
   - 用户输入自然语言，点击发送 → 调 `api_client.chat` → 得到 `ChatResponse`。  
   - 将回复显示在聊天框中。  
   - 如果 `ChatResponse.map_update` 非空，则调用 `map_view.render_map` 更新地图中心和缩放。  
   - 如返回内容中包含 `layer_info.tile_url`，预留对瓦片加载的 TODO。

4. 基础功能案例（对话示例，作为注释或 README 中的引导）：
   - 「什么是 GEE 的 asset？」→ 系统回答 GEE asset 的基本概念。  
   - 「给我一个在 GEE 中加载 SRTM 高程数据的最简单代码例子。」  
     - 期望：LLM 返回一段简单的 GEE 代码（JS 或 Python），例如使用 `USGS/SRTMGL1_003` 做展示。[15][14]
   - 「帮我在香港九龙区域加载 NDVI 示例。」  
     - 期望：Agent 调用 `geo_lookup` 获取坐标，然后调用 `gee_run_ndvi_example` 返回图层信息，并更新地图中心到九龙。

***

## 5. 页面：Map Explorer

文件：`pages/2_Map_Explorer.py`

需求：

1. 页面布局：
   - 顶部：地名输入框，按钮「定位」。  
   - 中部：地图（`map_view.render_map`）。  

2. 交互逻辑：
   - 用户输入地名 → 调用 `api_client.geo_resolve` → 拿到 `center` + `bbox` → 更新地图视图到该位置。  
   - 可选：再触发一个默认的 GEE 任务（例如加载一个基础数据集），并在地图上展示。

***

# Scripts 与知识库

## `scripts/build_chroma_index.py`

要求：

1. 读取一些基础的 GEE 文档内容文本（可以是硬编码的字符串列表，初期无需真实爬取）：  
   - 如「什么是 GEE asset」、「如何加载一个官方数据集」、「简单 NDVI 示例」等。  
2. 调用 `embeddings.get_embedding` 计算向量，并用 `chroma_store.add_documents` 写入。  

## `scripts/test_gee_connection.py`

- 在终端运行时，验证 GEE 是否能正常初始化，打印一个简单的结果（比如打印某个 asset 的信息）。  

## `scripts/test_geocoding.py`

- 传入一个地名（如「Hong Kong」），验证 geocoding 能否返回经纬度和 bbox。

***

# 基础功能案例（必须支持）

1. **介绍 GEE asset 概念（纯文本）**
   - 用户问：「GEE 的 asset 是什么？」  
   - 系统：通过 Chroma + LLM 返回简洁介绍。

2. **提供最简单的加载数据代码（来自知识库或直接生成）**
   - 用户问：「给一个最简单的在 GEE 中加载 SRTM 高程数据的示例。」  
   - 系统：返回一段可直接在 GEE Code Editor 使用的示例代码（语言 JS 或 Python，只需简单加载和显示）。  
   - 示例数据集可以选：`USGS/SRTMGL1_003` 或其他官方基础 DEM/影像。[14][15]

3. **地名 → 区域 → 地图 zoom**
   - 用户在 `Map Explorer` 或聊天里提到「香港九龙」，系统通过 geocoding 获取 bbox + center，并让地图 zoom 过去。  

4. **一个简单的 NDVI 示例任务**
   - 用户说：「在这个区域做一个 2020-2022 年 NDVI 示例。」  
   - 系统：  
     - 调用 `geo_lookup` 获取区域坐标。  
     - 调用 `gee_run_ndvi_example` 获取图层/统计结果。  
     - 返回解释性文字，并让地图更新到该区域（图层展示可以先留 TODO，占位 tile_url 字段）。  

***

# 非功能性要求

1. 所有核心模块先写出最小可用骨架（函数签名 + 调用链），即使内部逻辑暂时是 TODO。  
2. 代码风格统一使用 Python 3.10+，黑格式（black）兼容。  
3. 支持在本地通过如下方式运行：
   - 后端：`uvicorn backend.app.main:app --reload`  
   - 前端：`streamlit run frontend/app.py`  

***

**请根据以上 PRD / 需求，为项目生成初始代码骨架与关键模块的实现（可以带 TODO 注释），确保整体结构可运行并可逐步迭代。**
