## 代码重构 Todolist

### 一、清除 v1 历史遗留（死代码/冲突架构）

**1.1 删除 `agents/agent_gee_assistant.py`（旧简易 Agent）**
- 这是 v1 单线 LLM agent，routes_chat.py 已完全切换到 orchestrator，该文件已无任何入口调用
- 它内部还直接调用 `run_rag()`（`rag/chains.py`），而 orchestrator 不走这条链路

**1.2 删除 `agents/tools_gee.py`、`agents/tools_geo.py`、`agents/tools_kb.py`（三个薄包装层）**
- 这三个文件是 v1 时放在 `agents/` 下的 tool shim，对 `services/` 做一层无意义转发
- 目前 orchestrator 直接引用 `tools/execution/` 和 `tools/explanation/`，这三个文件只被旧 agent_gee_assistant.py 引用
- 删除后，`tools/explanation/kb_lookup.py` 中当前 `from backend.app.agents.tools_kb import kb_search` 的 2 级代理链也须直接改为调用 `services/chroma_store`

**1.3 废除 `/geo/resolve` 和 `/gee/run` 路由**
- routes_geo.py（`/geo/resolve`）和 routes_gee.py（`/gee/run`、`/gee/basemap`）是 v1 暴露原始 GEE 功能的独立路由，已被 orchestrator 的 tool 调用机制替代
- `routes_gee.py` 中的 `/gee/basemap` 仍被前端 api_client.py 调用，这个端点可以合并进 `/chat` 的初始化响应或迁移到 routes_chat.py
- 对应删除 `models/geo.py`（`GeoQueryRequest/Response` 只服务于 routes_geo.py）和 `models/gee.py`（`GeeTaskRequest/Response` 只服务于 routes_gee.py）

**1.4 废弃 `rag/chains.py`（`run_rag()`）**
- `run_rag()` 只被旧 agent_gee_assistant.py 调用，orchestrator 直接组合 prompt + llm_client，不走这条链
- `rag/retriever.py` 中的 `get_relevant_docs()` 是对 `chroma_store.similarity_search` 的单行封装，也可内联

**1.5 识别并清理 frontend 中的 v1 残留**
- sidebar.py 中的 `render_sidebar()` 已被 1_Chat_Assistant.py 自己内联实现的 sidebar 逻辑完全替代，是死代码
- api_client.py 中的 `geo_resolve()` 和 `run_gee_task()` 两个函数依赖被废除的路由，需要同步清除

---

### 二、`services/` 瘦身与职责厘清

**2.1 从 gee_client.py 中剥离执行逻辑**
- gee_client.py 目前混合了两类职责：GEE 初始化/连接管理（`init_gee_client()`），以及 v1 业务逻辑（`load_simple_asset()`、`run_ndvi_example()`、`execute_gee_code_simple()`）
- `execute_gee_code_simple()` 是 gee_executor.py 的前身，功能已被后者完全覆盖，直接删除
- `load_simple_asset()` 和 `run_ndvi_example()` 属于"GEE 任务"而非"GEE 服务"，应迁移为 `tools/` 下的具体 tool，gee_client.py 只保留 `init_gee_client()` 和 `get_basemap_config()`

**2.2 修复 config.py 中缺失的 `GEE_PROJECT_ID`**
- gee_client.py 从 config 导入 `GEE_PROJECT_ID`，但 config.py 中从未定义该变量，这是一个运行时 `ImportError` 风险，需要补充定义

**2.3 `services/` 最终只保留三类**
- llm_client.py：封装 LLM 调用
- chroma_store.py：向量库读写
- gee_client.py：GEE 连接管理（精简后）
- embeddings.py：当前是 hash 占位，待接入真实 embedding 服务（见第四节）
- geocoding.py：可暂留，但调用路径要从 `agents/tools_geo.py` 的代理改为 orchestrator 通过 tool 直接调用

---

### 三、Orchestrator 与 Session 能力强化

**3.1 建立跨请求的 Session 状态管理器**
- 当前 `WorkflowState` 是单次请求内的状态机，每次 `make_initial_state()` 都是全新的空状态
- 需要引入 `SessionStore`（可先用内存字典，key 为 `session_id`），存储：当前地图上下文（`map_center`、`map_zoom`、`active_layers`）、当前对话历史摘要、上一步执行结果的关键输出（如最近的 asset_id、bbox、分析结论）
- orchestrator 在 `make_initial_state` 时从 `SessionStore` 加载 session 上下文注入 `state["context"]`，执行完毕后将关键结果写回 `SessionStore`

**3.2 扩展 `WorkflowState` 的 session 感知字段**
- 新增 `session_context: Dict[str, Any]`，区别于 `context`（步骤间共享），专门存储从 `SessionStore` 注入的跨请求持久变量
- planner 和 code_gen_prompt 需要感知 `session_context`（如"上一次分析的区域是…"）

**3.3 Router 增加更多 intent 类型**
- 当前 `classify_intent` 只返回 `"execution"` 或 `"knowledge"` 两类
- 迭代目标中 geocoding 和图表插入将成为独立 tool 调用，建议增加 `"geo_query"`、`"clarification"` 等意图，或将 intent 改为 tag 列表（支持多意图）

**3.4 工作流中的 RAG 步骤显式化**
- 当前 knowledge 路径直接走 `_summarize` → llm_client，没有真正的检索增强
- 应在 knowledge 分支中增加显式的 `_retrieve` 步骤（调用 `chroma_store`），并将检索结果注入 summarize 阶段的 prompt

---

### 四、`tools/` 目录扩充与分类整理

**4.1 将 geocoding 功能收归 `tools/`**
- 新建 `tools/geo/geocoder.py`，将 `services/geocoding.py` 的逻辑迁入
- orchestrator 在需要地名解析时通过 tool 调用，而非 services 直接暴露

**4.2 将 `load_simple_asset` 和 `run_ndvi_example` 迁入 `tools/`**
- 新建 `tools/execution/gee_tasks.py`，承接从 gee_client.py 剥离的这两个函数
- 对应更新 orchestrator 中 step_type 的分发逻辑

**4.3 建立 Sandbox 子目录 `app/sandbox/`**
- 将 `tools/execution/gee_executor.py` 中的 `_MockMap`、`exec()` 沙箱逻辑迁入 `sandbox/executor.py`
- 新建 `sandbox/env_rules.py`，集中存放注入沙箱的执行约束（禁止 geemap、Map 对象行为规范等），对应从 `SYSTEM_PROMPT_GEE_ASSISTANT` 中移除这些细节规则
- `sandbox/` 对外只暴露 `run(code: str) -> ExecutionResult`，屏蔽内部 `exec()` 细节

**4.4 规范 `tools/` 下的 tool 接口**
- 目前 tool 函数返回值格式不统一（有些 dict 有 `status` 字段，有些没有）
- 考虑引入统一的 `ToolResult` TypedDict（`status`, `output`, data, `error`），便于 orchestrator 统一处理步骤结果
- 未来便于将工具改造成可注册的 function-calling schema

---

### 五、Prompt 分层重构

**5.1 重写 `SYSTEM_PROMPT_GEE_ASSISTANT`**
- 当前 prompt 大量篇幅是沙箱执行规则（禁止 geemap、Map 对象用法等），这些规则迁入 `sandbox/env_rules.py` 后，`SYSTEM_PROMPT_GEE_ASSISTANT` 应改写为宏观稳定的助手定位描述（身份、能力边界、回答风格）

**5.2 消除 prompt 中的执行规则重复**
- `SYSTEM_PROMPT_GEE_ASSISTANT`、`CODE_GEN_PROMPT`、`CODE_REPAIR_PROMPT` 都有重复的"禁止 geemap"类规则
- 统一抽取为一个 `SANDBOX_CONSTRAINTS_BLOCK` 常量，三处 prompt 引用同一来源

**5.3 agent_gee_assistant.py 中的内联 `extra_context` 拼接逻辑**
- 旧 agent 中有大段 if-else 在 prompt 里动态拼接执行规则，这种方式随 agent 文件删除可一并消除

---

### 六、前端聊天与 Session 同步

**6.1 `session_id` 全链路打通**
- 前端 1_Chat_Assistant.py 调用 `chat_stream()` 时传入的 `session_id` 为 `None`（api_client.py 中如果 `session_id` 为空则不附带该字段）
- 前端需要在 `st.session_state` 中生成并维护 `session_id`（如 uuid4），每次对话请求都携带，用于后端 `SessionStore` 读写

**6.2 历史记录的后端持久化对接**
- 当前历史记录仅存在 `st.session_state["history"]`，刷新即丢失
- 前端"保存对话"按钮对应的后端落盘接口尚未实现，需要补充 `POST /chat/history` 等接口

**6.3 1_Chat_Assistant.py 中流式渲染与 `step_start` 步骤数量逻辑**
- 渲染 `step_start` 事件时 `total` 用了 `len(collected_steps)+1`（此时 collected_steps 实际还未填充），应改为从 `planning` 事件中缓存 plan 长度

---

### 七、RAG 能力真正落地

**7.1 接入真实 Embedding 服务**
- `services/embeddings.py` 目前用 hash 生成假向量，chroma_store.py 在 `add_documents()` 和 `query()` 时依赖 Chroma 默认 embedding（chromadb 内置 sentence-transformers 或需显式配置）
- 实际上 embeddings.py 定义的 `get_embedding()` 根本没有被 chroma_store.py 调用，两者完全脱节
- 需要选择并接入一个真实 embedding provider（如 OpenAI `text-embedding-3-small`，或本地 `sentence-transformers`），让 `chroma_store.add_documents()` 使用对应 embedding

**7.2 RAG 真正服务于 orchestrator**
- knowledge 分支中检索结果应注入 planner 和 summarizer prompt，而不只是在 summarize 阶段凑数
- 对于 execution 分支，code_gen 阶段应能从知识库检索相关 API 示例作为 few-shot 注入 `CODE_GEN_PROMPT`

---

### 八、日志与数据落盘

**8.1 建立结构化对话日志**
- orchestrator 每次 workflow 执行应输出结构化日志（session_id、intent、plan、各步骤 input/output、final_reply、耗时），目前只有 llm_client.py 有 debug logging
- 考虑新建 `services/log_store.py`，以 JSONL 格式落盘，为后续连接数据库做准备

**8.2 数据库连接预留**
- `services/` 下预留 `db.py`，定义 SQLite（或 PostgreSQL）的连接和 session 表结构，方便 `SessionStore` 和 log_store 未来从内存/文件迁移到真正的持久层

---

### 九、工程质量

**9.1 CORS 配置收紧**
- main.py 中 `allow_origins=["*"]` 在生产中是安全风险，应改为从 config 读取允许的来源列表

**9.2 gee_executor.py 的 `exec()` 输入清洗**
- 目前 `exec(code, global_env, global_env)` 直接执行 LLM 生成的字符串，应在执行前做基本的危险 pattern 检测（如 `import os`、`import subprocess`、`__import__`、文件系统操作等），命中则拒绝执行并返回 error，不应仅依赖 `global_env` 的命名空间隔离

**9.3 清理 `models/` 只保留 chat 相关**
- 随 v1 路由废除，`models/geo.py` 和 `models/gee.py` 可删除
- `models/chat.py` 中 `WorkflowStatus` 可考虑合并进 `agents/state.py`，减少 models 层的概念碎片


---

**用户query：**

我在思考重构代码的问题，目前主要的问题是这样的：
readme显示了根据prd.md得到第一版模型：一个只通过system prompt单线llm简易agent，且除了chat_assistant之外还做了map_explorer，专用于输入地点名，返回坐标并在地图上展示（目前已经在frontend/pages中被删除），app中的部分功能的组织形式（如sevices/，models/）仍处于第一版模型的设计阶段。
在第一版模型后，代码迎来了几次较大的前后端功能迭代：
1. 增加了llm生成代码的执行能力和前端地图渲染能力，分别被放在了backend/app/services/gee_client.py和backend/app/tools/execution/gee_executor.py中
2. 将单向的简单agent流程重构成了react架构的多状态流转agent系统，核型是/Users/fred/Code/gee-agent/backend/app/agents/orchestrator.py，它不断管理和更新state，不断进行观察-思考-执行的循环
3. 前端同步的聊天框可以流式接受大模型的输出，sidebar中有新对话和保存对话，通过历史记录进入历史对话等功能（部分功能对应后端逻辑仍未实现）

之后的迭代方向是这样的：
1. 强化orchestrator的能力，让他除了流转一次用户请求所带来的状态以外，增加其管理session内变量值的能力，在用户行为被分为多步时，不至于在新的一步开始的时候没有任何上一步的信息
2. 在app/目录下设定sandbox/子目录，将llm的代码执行（目前在gee_executor）隔离开，并且将目前在SYSTEM_PROMPT_GEE_ASSISTANT中的诸多代码规则注入到sandbox环境，SYSTEM_PROMPT_GEE_ASSISTANT作为初始的prompt则移除这些细枝末节的规则，重写成长期稳
定静态和宏观的规则
3. 继续壮大tools/下的各个工具，将目前散落在各处的geocoding功能和ndvi示例相关功能全部收归tools，且将它做更好的分门别类，如向sandbox提交任务，在前端某处插入图表等等
4. 为整个系统带来真正的rag能力（目前embedding功能使用hash占位），通过目前已经有的搭建更加完善的知识库和向量检索增强，让rag能够真正服务于orchestrator的输出
5. 建立聊天记录，模型输出的日志落盘能力，日志罗盘后连接数据库

基于这些迭代目标，目前我觉得存在的问题如下：
1. 一些第一版遗留的架构中存在和目前迭代方向的架构设计相冲突，例如agents下有一些tool_xx.py的脚本，models/下的类定义也需要核对
2. 舍弃不必要的api/下的各个路由，从前端设计上只需要着重迭代chat_assistant下的各个功能即可
3. services杂乱，当前部分功能可能和tool设想的功能重合，services只保留简洁的llm，向量库，数据落盘的功能即可
你看一下代码，看看除了我上述提到的，照着这个强调react架构的agent系统的设计思路还有哪些重构代码的要点，并整合成一个完整的代码重构todolist
