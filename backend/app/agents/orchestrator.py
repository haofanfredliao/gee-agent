"""Orchestrator：核心工作流状态机。

实现观察-思考-执行（Observe-Think-Act）循环：
  1. routing    — 通过 router 识别用户意图
  2. planning   — LLM 将 query 拆解为有序子步骤
  3. executing  — 逐步执行：inspect（观察）→ 更新 context → execute（行动）
  4. summarizing — LLM 汇总所有步骤输出，生成最终回答
  5. terminated  — 返回 ChatResponse

对外接口：
  run_workflow(query, session_id)   -> ChatResponse        （一次性返回）
  stream_workflow(query, session_id) -> AsyncGenerator[str] （SSE 流式事件）

流式事件格式（每行一个 JSON，以 \\n 结尾）：
  {"type": "routing",    "data": {"intent": "execution"}}
  {"type": "planning",   "data": {"plan": [{"description":..., "type":...}, ...]}}
  {"type": "step_start", "data": {"index": 0, "description": "...", "tool": "..."}}
  {"type": "step_done",  "data": {"index": 0, "description": "...", "tool": "...",
                                  "success": true, "output_preview": "..."}}
  {"type": "summarizing","data": {}}
  {"type": "done",       "data": {"reply": "...", "map_update": {...}}}
  {"type": "error",      "data": {"message": "..."}}
"""
import json
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.app.agents.state import WorkflowState, StepResult, make_initial_state, format_status
from backend.app.agents.router import classify_intent
from backend.app.tools.explanation.asset_inspector import inspect_asset
from backend.app.tools.explanation.kb_lookup import knowledge_base_lookup
from backend.app.tools.execution.gee_executor import execute_gee_snippet
from backend.app.models.chat import ChatResponse, MapUpdate, WorkflowStatus
from backend.app.services import llm_client
from backend.app.rag.prompts import PLANNER_PROMPT, CODE_GEN_PROMPT, CODE_REPAIR_PROMPT, SUMMARIZE_PROMPT
from backend.app.core.config import DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON, DEFAULT_ZOOM

# ─── 辅助函数 ────────────────────────────────────────────────────────────────

_ASSET_PATH_RE = re.compile(r"projects/[\w\-]+/assets/[\w\-/]+")


def _extract_asset_ids(text: str) -> List[str]:
    """从文本中提取所有 GEE asset 路径（projects/…/assets/…）。"""
    return _ASSET_PATH_RE.findall(text)


def _build_context_section(context: Dict[str, Any]) -> str:
    """将 state.context 格式化为 CODE_GEN_PROMPT 中的上下文描述段落。"""
    parts: List[str] = []
    if context.get("asset_id"):
        parts.append(f"Asset ID：{context['asset_id']}")
    if context.get("property_names"):
        parts.append(f"属性字段（实际字段名，必须使用这些）：{context['property_names']}")
    if context.get("feature_count") is not None:
        parts.append(f"要素总数：{context['feature_count']}")
    if context.get("geometry_type"):
        parts.append(f"几何类型：{context['geometry_type']}")
    if context.get("bands"):
        parts.append(f"波段列表：{context['bands']}")
    if not parts:
        return ""
    return "已知数据上下文（由前序检查步骤获得）：\n" + "\n".join(f"  - {p}" for p in parts)


# ─── 规划阶段 ────────────────────────────────────────────────────────────────

async def _plan(state: WorkflowState) -> WorkflowState:
    """
    Planning 阶段：调用 LLM 将用户 query 拆解为结构化子步骤列表。

    LLM 返回 JSON 数组，每项含 description / type / asset_id。
    若解析失败，则根据 query 中是否含有 asset 路径生成默认计划。
    """
    state["status"] = "planning"
    raw = await llm_client.chat_with_llm(PLANNER_PROMPT.format(query=state["query"]))

    plan: List[Dict[str, Any]] = []
    try:
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError):
        plan = []

    # 回退：按 query 中的 asset 路径自动生成默认两步计划
    if not plan:
        asset_ids = _extract_asset_ids(state["query"])
        if asset_ids:
            plan = [
                {
                    "description": "检查数据集属性字段与元数据",
                    "type": "inspect",
                    "asset_id": asset_ids[0],
                },
                {
                    "description": "加载数据、执行分析并可视化",
                    "type": "execute",
                    "asset_id": asset_ids[0],
                },
            ]
        else:
            plan = [
                {
                    "description": "执行 GEE 分析任务",
                    "type": "execute",
                    "asset_id": None,
                }
            ]

    state["plan"] = plan
    return state


# ─── 单步执行 ────────────────────────────────────────────────────────────────

async def _execute_step(
    state: WorkflowState,
    step: Dict[str, Any],
    step_index: int,
) -> WorkflowState:
    """
    执行单个规划步骤。

    - type="inspect"  → 调用 asset_inspector（Observe），结果写入 state.context
    - type="execute"  → 调用 LLM 生成代码（Think），再调用 gee_executor（Act）
    """
    state["current_step"] = step_index
    step_type = step.get("type", "execute")
    description = step.get("description", f"步骤 {step_index + 1}")
    asset_id: Optional[str] = step.get("asset_id") or state["context"].get("asset_id")

    result: StepResult = {
        "step_index": step_index,
        "description": description,
        "tool": "",
        "output": "",
        "tile_url": None,
        "success": False,
    }

    # ── Observe：inspect 步骤 ──────────────────────────────────────────────
    if step_type == "inspect":
        result["tool"] = "asset_inspector"
        if asset_id:
            info = inspect_asset(asset_id)
            result["output"] = json.dumps(info, ensure_ascii=False, indent=2)
            result["success"] = info["status"] == "ok"
            if info["status"] == "ok":
                # 写入跨步骤共享 context
                state["context"]["asset_id"] = asset_id
                state["context"]["property_names"] = info.get("property_names", [])
                state["context"]["feature_count"] = info.get("feature_count")
                state["context"]["geometry_type"] = info.get("geometry_type")
                state["context"]["bands"] = info.get("bands", [])
        else:
            result["output"] = "未提供 asset_id，跳过检查。"
            result["success"] = False

    # ── Think + Act：execute 步骤 ─────────────────────────────────────────
    elif step_type == "execute":
        result["tool"] = "gee_executor"

        # Think：LLM 生成代码，注入从 inspect 步骤获得的 context
        context_section = _build_context_section(state["context"])
        code_prompt = CODE_GEN_PROMPT.format(
            query=state["query"],
            step_description=description,
            context_section=context_section,
        )
        llm_response = await llm_client.chat_with_llm(code_prompt)

        # 提取代码块
        code_blocks = re.findall(r"```python(.*?)```", llm_response, re.DOTALL)
        if not code_blocks:
            code_blocks = re.findall(r"```(.*?)```", llm_response, re.DOTALL)

        if not code_blocks:
            result["output"] = f"[代码生成失败] LLM 原始响应：\n{llm_response[:400]}"
            result["success"] = False
        else:
            code = code_blocks[-1].strip()

            # Act：执行代码（含 repair 子循环，最多重试 3 次）
            MAX_REPAIR_ATTEMPTS = 3
            exec_result = execute_gee_snippet(code)
            for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
                if exec_result["status"] == "ok":
                    break
                error_log = exec_result.get("log", "")
                repair_prompt = CODE_REPAIR_PROMPT.format(
                    query=state["query"],
                    step_description=description,
                    context_section=context_section,
                    original_code=code,
                    error_log=error_log,
                    attempt=attempt,
                )
                repair_response = await llm_client.chat_with_llm(repair_prompt)
                repaired_blocks = re.findall(r"```python(.*?)```", repair_response, re.DOTALL)
                if not repaired_blocks:
                    repaired_blocks = re.findall(r"```(.*?)```", repair_response, re.DOTALL)
                if not repaired_blocks:
                    break
                code = repaired_blocks[-1].strip()
                exec_result = execute_gee_snippet(code)

            result["output"] = exec_result.get("log", "")
            result["tile_url"] = exec_result.get("tile_url")
            result["success"] = exec_result["status"] == "ok"

            # 更新地图 state
            if exec_result.get("tile_url"):
                q = state["query"].lower()
                if "hong" in q or "香港" in q or "hk" in q:
                    center_lat, center_lon, zoom = 22.312, 114.174, 10
                else:
                    center_lat, center_lon, zoom = DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON, DEFAULT_ZOOM
                state["map_update"] = {
                    "center_lat": center_lat,
                    "center_lon": center_lon,
                    "zoom": zoom,
                    "layer_info": {"tile_url": exec_result["tile_url"]},
                }

    state["steps"].append(result)
    return state


# ─── 汇总阶段 ────────────────────────────────────────────────────────────────

async def _summarize(state: WorkflowState) -> WorkflowState:
    """
    Summarizing 阶段：LLM 将所有步骤的输出汇总为最终自然语言回答。
    """
    state["status"] = "summarizing"

    steps_summary = "\n\n".join(
        f"**步骤 {s['step_index'] + 1}（{s['description']}）** [工具: {s['tool']}]：\n{s['output']}"
        for s in state["steps"]
    )

    prompt = SUMMARIZE_PROMPT.format(
        query=state["query"],
        steps_summary=steps_summary,
    )
    state["final_reply"] = await llm_client.chat_with_llm(prompt)
    state["status"] = "terminated"
    return state


# ─── 主入口 ──────────────────────────────────────────────────────────────────

async def run_workflow(query: str, session_id: str = "") -> ChatResponse:
    """
    工作流主入口：路由 → 规划 → 执行 → 汇总 → 返回 ChatResponse。

    ChatResponse.workflow_status 包含完整的中间状态摘要，
    可在前端聊天界面通过 status() 展示各步骤进度。
    """
    state = make_initial_state(query, session_id)

    # ── 1. routing ────────────────────────────────────────────────────────
    state["status"] = "routing"
    state["intent"] = await classify_intent(query)

    # 知识问答直接走 RAG，不走多步工作流
    if state["intent"] == "knowledge":
        from backend.app.rag.chains import run_rag
        reply = await run_rag(query)
        return ChatResponse(
            reply=reply,
            workflow_status=WorkflowStatus(
                intent="knowledge",
                status="terminated",
                plan=["知识库检索与直接问答"],
                steps_completed=1,
                steps_total=1,
                steps=[],
            ),
        )

    # ── 2. planning ───────────────────────────────────────────────────────
    state = await _plan(state)

    # ── 3. executing（逐步 Observe-Think-Act 循环） ───────────────────────
    state["status"] = "executing"
    for i, step in enumerate(state["plan"]):
        state = await _execute_step(state, step, i)

    # ── 4. summarizing ────────────────────────────────────────────────────
    state = await _summarize(state)

    # ── 5. 构建返回对象 ───────────────────────────────────────────────────
    map_update: Optional[MapUpdate] = None
    if state.get("map_update"):
        mu = state["map_update"]
        map_update = MapUpdate(
            center_lat=mu["center_lat"],
            center_lon=mu["center_lon"],
            zoom=mu["zoom"],
            layer_info=mu.get("layer_info"),
        )

    workflow_status = WorkflowStatus(
        intent=state["intent"],
        status=state["status"],
        plan=[s.get("description", "") for s in state["plan"]],
        steps_completed=len(state["steps"]),
        steps_total=len(state["plan"]),
        steps=[
            {
                "index": s["step_index"],
                "description": s["description"],
                "tool": s["tool"],
                "success": s["success"],
                "output_preview": (s["output"] or "")[:300],
            }
            for s in state["steps"]
        ],
    )

    return ChatResponse(
        reply=state["final_reply"] or "工作流执行完成，但未生成汇总。",
        map_update=map_update,
        workflow_status=workflow_status,
    )


# ─── 流式主入口 ───────────────────────────────────────────────────────────────

def _evt(event_type: str, data: Any) -> str:
    """序列化为单行 JSON 事件（带换行符）。"""
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=False) + "\n"


async def stream_workflow(query: str, session_id: str = "") -> AsyncGenerator[str, None]:
    """
    工作流流式入口：与 run_workflow 逻辑相同，但每个关键节点都立即 yield 一个事件，
    而不是等到全部完成后一次性返回。

    前端通过 httpx 流式接收，实时更新 st.status。
    """
    state = make_initial_state(query, session_id)
    try:
        # ── 1. routing ────────────────────────────────────────────────────
        state["status"] = "routing"
        state["intent"] = await classify_intent(query)
        yield _evt("routing", {"intent": state["intent"]})

        # 知识问答直接走 RAG
        if state["intent"] == "knowledge":
            yield _evt("summarizing", {})
            from backend.app.rag.chains import run_rag
            reply = await run_rag(query)
            yield _evt("done", {"reply": reply, "map_update": None})
            return

        # ── 2. planning ───────────────────────────────────────────────────
        state = await _plan(state)
        yield _evt("planning", {
            "plan": [
                {"description": s.get("description", ""), "type": s.get("type", "execute")}
                for s in state["plan"]
            ]
        })

        # ── 3. executing ──────────────────────────────────────────────────
        state["status"] = "executing"
        for i, step in enumerate(state["plan"]):
            step_type = step.get("type", "execute")
            tool_name = "asset_inspector" if step_type == "inspect" else "gee_executor"
            yield _evt("step_start", {
                "index": i,
                "description": step.get("description", f"步骤 {i+1}"),
                "tool": tool_name,
            })
            state = await _execute_step(state, step, i)
            done_result = state["steps"][-1]
            yield _evt("step_done", {
                "index": i,
                "description": done_result["description"],
                "tool": done_result["tool"],
                "success": done_result["success"],
                "output_preview": (done_result["output"] or "")[:300],
            })

        # ── 4. summarizing ────────────────────────────────────────────────
        yield _evt("summarizing", {})
        state = await _summarize(state)

        # ── 5. 构建 done 事件 ─────────────────────────────────────────────
        map_update_dict = state.get("map_update")
        yield _evt("done", {
            "reply": state["final_reply"] or "工作流执行完成，但未生成汇总。",
            "map_update": map_update_dict,
            "workflow_status": {
                "intent": state["intent"],
                "status": state["status"],
                "plan": [s.get("description", "") for s in state["plan"]],
                "steps_completed": len(state["steps"]),
                "steps_total": len(state["plan"]),
                "steps": [
                    {
                        "index": s["step_index"],
                        "description": s["description"],
                        "tool": s["tool"],
                        "success": s["success"],
                        "output_preview": (s["output"] or "")[:300],
                    }
                    for s in state["steps"]
                ],
            },
        })
    except Exception as exc:
        yield _evt("error", {"message": str(exc)})
