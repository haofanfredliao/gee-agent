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
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.app.agents.state import WorkflowState, StepResult, make_initial_state, format_status
from backend.app.agents.session_store import load_session_context, save_session_state
from backend.app.agents.router import classify_intent
from backend.app.tools.explanation.asset_inspector import inspect_asset
from backend.app.tools.explanation.kb_lookup import knowledge_base_lookup
from backend.app.tools.execution.gee_executor import execute_gee_snippet
from backend.app.tools.geo.geocoder import resolve_place
from backend.app.agents.prompts import (
    PLANNER_PROMPT,
    CODE_GEN_PROMPT,
    CODE_REPAIR_PROMPT,
    SUMMARIZE_PROMPT,
    KNOWLEDGE_PROMPT,
    GEO_REPLY_PROMPT,
)
from backend.app.models.chat import ChatResponse, MapUpdate, WorkflowStatus
from backend.app.services import llm_client
from backend.app.services.log_store import write_log
from backend.app.core.config import DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON, DEFAULT_ZOOM

# ─── 辅助函数 ────────────────────────────────────────────────────────────────

_ASSET_PATH_RE = re.compile(r"projects/[\w\-]+/assets/[\w\-/]+")


def _extract_asset_ids(text: str) -> List[str]:
    """从文本中提取所有 GEE asset 路径（projects/…/assets/…）。"""
    return _ASSET_PATH_RE.findall(text)


def _build_prev_steps_section(steps: List[StepResult]) -> str:
    """将已完成的 execute 步骤输出格式化为前序结果摘要，注入后续步骤的 prompt。"""
    execute_steps = [s for s in steps if s["tool"] == "gee_executor" and s["output"]]
    if not execute_steps:
        return ""
    lines = ["【前序执行步骤输出 — 在本步骤代码中可直接使用这些计算结果】"]
    for s in execute_steps:
        preview = s["output"][:800]  # 截断过长输出
        lines.append(f"  步骤 {s['step_index'] + 1}（{s['description']}）的输出：\n{preview}")
    return "\n\n".join(lines)


def _build_context_section(context: Dict[str, Any]) -> str:
    """将 state.context 格式化为 CODE_GEN_PROMPT 中的上下文描述段落。

    支持多 asset：context["assets"] 是以 asset_id 为 key 的字典，
    每个 value 包含该 asset 的元数据（property_names/bands/feature_count/geometry_type）。
    """
    assets: Dict[str, Any] = context.get("assets") or {}
    if not assets:
        return ""
    lines: List[str] = ["已知数据上下文（由前序 inspect 步骤获得，必须使用这些实际字段名）："]
    for aid, meta in assets.items():
        lines.append(f"  Asset: {aid}")
        if meta.get("bands"):
            lines.append(f"    - 波段列表：{meta['bands']}")
        if meta.get("property_names"):
            lines.append(f"    - 属性字段（实际字段名）：{meta['property_names']}")
        if meta.get("feature_count") is not None:
            lines.append(f"    - 要素总数：{meta['feature_count']}")
        if meta.get("geometry_type"):
            lines.append(f"    - 几何类型：{meta['geometry_type']}")
    return "\n".join(lines)


def _build_session_section(state: WorkflowState) -> str:
    """将 session 级别上下文格式化为 prompt 中的对话历史/区域感知段落。"""
    parts: List[str] = []
    sc = state.get("session_context") or {}

    map_ctx = sc.get("map_context") or {}
    if map_ctx.get("center_lat") and map_ctx.get("center_lon"):
        parts.append(
            f"当前地图区域：中心 ({map_ctx['center_lat']:.5f}, {map_ctx['center_lon']:.5f})，"
            f"缩放级别 {map_ctx.get('zoom', '未知')}"
        )

    last_q = sc.get("last_query")
    last_r = sc.get("last_reply")
    if last_q:
        parts.append(f"上一轮用户请求：{last_q[:120]}")
    if last_r:
        parts.append(f"上一轮助手回复摘要：{last_r[:200]}")

    asset_id = sc.get("asset_id")
    if asset_id:
        parts.append(f"上一轮使用的 Asset：{asset_id}")

    if not parts:
        return ""
    return "\n".join(["【会话上下文 — 可复用以下信息】"] + [f"  - {p}" for p in parts])


# ─── 规划阶段 ────────────────────────────────────────────────────────────────

async def _plan(state: WorkflowState) -> WorkflowState:
    """
    Planning 阶段：调用 LLM 将用户 query 拆解为结构化子步骤列表。

    LLM 返回 JSON 数组，每项含 description / type / asset_id。
    若解析失败，则根据 query 中是否含有 asset 路径生成默认计划。
    """
    state["status"] = "planning"
    session_section = _build_session_section(state)
    raw = await llm_client.chat_with_llm(
        PLANNER_PROMPT.format(query=state["query"], session_section=session_section)
    )

    plan: List[Dict[str, Any]] = []
    try:
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError):
        plan = []

    # 回退：按 query 中的 asset 路径自动生成默认计划（每个 asset 各一个 inspect）
    if not plan:
        asset_ids = _extract_asset_ids(state["query"])
        # 去重同时保持顺序
        seen: Dict[str, bool] = {}
        unique_asset_ids = [a for a in asset_ids if not seen.setdefault(a, False) and not seen.update({a: True})]
        if unique_asset_ids:
            plan = [
                {"description": f"检查 {a.split('/')[-1]} 元数据", "type": "inspect", "asset_id": a}
                for a in unique_asset_ids
            ] + [
                {
                    "description": "执行分析并可视化",
                    "type": "execute",
                    "asset_id": unique_asset_ids[0],
                }
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
        "code": None,
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
                # 写入跨步骤共享 context（多 asset 结构）
                if "assets" not in state["context"]:
                    state["context"]["assets"] = {}
                state["context"]["assets"][asset_id] = {
                    "property_names": info.get("property_names", []),
                    "feature_count": info.get("feature_count"),
                    "geometry_type": info.get("geometry_type"),
                    "bands": info.get("bands", []),
                }
        else:
            result["output"] = "未提供 asset_id，跳过检查。"
            result["success"] = False

    # ── Think + Act：execute 步骤 ─────────────────────────────────────────
    elif step_type == "execute":
        result["tool"] = "gee_executor"

        # Think：LLM 生成代码，注入从 inspect 步骤获得的 context、前序步骤输出和 session context
        context_section = _build_context_section(state["context"])
        prev_steps_section = _build_prev_steps_section(state["steps"])
        session_section = _build_session_section(state)

        # RAG 检索：用步骤描述 + 用户总需求拼接查询，检索相关最佳实践
        rag_query = f"{description} {state['query']}"
        kb_hits = knowledge_base_lookup(rag_query, k=3)
        kb_section = (
            "【RAG 知识库参考 — 本任务相关最佳实践，优先遵循】\n" + kb_hits
            if kb_hits and kb_hits != "（未找到相关文档）"
            else ""
        )

        code_prompt = CODE_GEN_PROMPT.format(
            query=state["query"],
            step_description=description,
            context_section=context_section,
            kb_section=kb_section,
            prev_steps_section=prev_steps_section,
            session_section=session_section,
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
                        kb_section=kb_section,
                        prev_steps_section=prev_steps_section,
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
            result["code"] = code
            result["success"] = exec_result["status"] == "ok"

            # 更新地图 state：优先使用 session 中已知的地图中心
            all_layers = exec_result.get("layers") or []
            if all_layers:
                map_ctx = (state.get("session_context") or {}).get("map_context") or {}
                center_lat = map_ctx.get("center_lat") or DEFAULT_CENTER_LAT
                center_lon = map_ctx.get("center_lon") or DEFAULT_CENTER_LON
                zoom = map_ctx.get("zoom") or DEFAULT_ZOOM
                state["map_update"] = {
                    "center_lat": center_lat,
                    "center_lon": center_lon,
                    "zoom": zoom,
                    "layer_info": {"tile_url": exec_result.get("tile_url")},
                    "layers": all_layers,
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


async def _answer_knowledge(query: str) -> str:
    """知识问答：先检索知识库，再基于检索结果调用 LLM。"""
    kb_context = knowledge_base_lookup(query, k=4)
    prompt = KNOWLEDGE_PROMPT.format(
        query=query,
        kb_context=kb_context,
    )
    return await llm_client.chat_with_llm(prompt)


async def _handle_geo_query(
    query: str,
    session_id: str,
    existing_map_context: Optional[Dict[str, Any]],
) -> tuple[Optional[MapUpdate], str]:
    """地名定位分支：调用 geocoder tool，生成 map_update 和自然语言回复。

    Returns (map_update, reply_text)
    """
    # 从 query 中简单提取地名（取最后一个中/英名词段）
    place = query.strip()
    geo = resolve_place(place)
    if geo["status"] != "ok":
        reply = f"抱歉，无法定位「{place}」，请检查地名是否正确。"
        return None, reply

    map_update = MapUpdate(
        center_lat=geo["center_lat"],
        center_lon=geo["center_lon"],
        zoom=geo["zoom"],
        layer_info=None,
    )
    new_map_ctx = {
        "center_lat": geo["center_lat"],
        "center_lon": geo["center_lon"],
        "zoom": geo["zoom"],
    }
    save_session_state(
        session_id,
        map_context=new_map_ctx,
        last_query=query,
    )

    reply = await llm_client.chat_with_llm(
        GEO_REPLY_PROMPT.format(
            place_name=geo["place_name"],
            center_lat=geo["center_lat"],
            center_lon=geo["center_lon"],
            bbox=geo["bbox"],
        )
    )
    return map_update, reply


# ─── 主入口 ──────────────────────────────────────────────────────────────────

async def run_workflow(
    query: str,
    session_id: str = "",
    map_context: Optional[Dict[str, Any]] = None,
) -> ChatResponse:
    """
    工作流主入口：路由 → 规划 → 执行 → 汇总 → 返回 ChatResponse。

    ChatResponse.workflow_status 包含完整的中间状态摘要，
    可在前端聊天界面通过 status() 展示各步骤进度。
    """
    state = make_initial_state(query, session_id)
    state["session_context"] = load_session_context(session_id)
    state["context"].update(state["session_context"])
    _t0 = time.monotonic()

    # ── 1. routing ────────────────────────────────────────────────────────
    state["status"] = "routing"
    state["intent"] = await classify_intent(query)

    # 知识问答：走检索增强的单步回答，不进入多步执行工作流
    if state["intent"] == "knowledge":
        reply = await _answer_knowledge(query)
        save_session_state(
            session_id,
            context_updates=state["context"],
            map_context=map_context,
            last_query=query,
            last_reply=reply,
        )
        write_log(session_id, intent="knowledge", query=query, plan_steps=1,
                  reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
        return ChatResponse(
            reply=reply,
            workflow_status=WorkflowStatus(
                intent="knowledge",
                status="terminated",
                plan=["知识库检索与问答"],
                steps_completed=1,
                steps_total=1,
                steps=[],
            ),
        )

    # 地名定位：直接调用 geocoder tool
    if state["intent"] == "geo_query":
        map_update, reply = await _handle_geo_query(query, session_id, map_context)
        save_session_state(session_id, last_reply=reply)
        write_log(session_id, intent="geo_query", query=query, plan_steps=1,
                  reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
        return ChatResponse(
            reply=reply,
            map_update=map_update,
            workflow_status=WorkflowStatus(
                intent="geo_query",
                status="terminated",
                plan=["地名解析与地图定位"],
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
                "code": s.get("code") or "",
            }
            for s in state["steps"]
        ],
    )

    resolved_map_context = map_context or {}
    if map_update:
        resolved_map_context = {
            "center_lat": map_update.center_lat,
            "center_lon": map_update.center_lon,
            "zoom": map_update.zoom,
        }
    save_session_state(
        session_id,
        context_updates=state["context"],
        map_context=resolved_map_context,
        last_query=query,
        last_reply=state["final_reply"],
    )
    write_log(session_id, intent="execution", query=query,
              plan_steps=len(state["plan"]),
              reply_preview=state["final_reply"] or "",
              duration_ms=int((time.monotonic()-_t0)*1000))

    return ChatResponse(
        reply=state["final_reply"] or "工作流执行完成，但未生成汇总。",
        map_update=map_update,
        workflow_status=workflow_status,
    )


# ─── 流式主入口 ───────────────────────────────────────────────────────────────

def _evt(event_type: str, data: Any) -> str:
    """序列化为单行 JSON 事件（带换行符）。"""
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=False) + "\n"


async def stream_workflow(
    query: str,
    session_id: str = "",
    map_context: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    """
    工作流流式入口：与 run_workflow 逻辑相同，但每个关键节点都立即 yield 一个事件，
    而不是等到全部完成后一次性返回。

    前端通过 httpx 流式接收，实时更新 st.status。
    """
    state = make_initial_state(query, session_id)
    state["session_context"] = load_session_context(session_id)
    state["context"].update(state["session_context"])
    _t0 = time.monotonic()
    try:
        # ── 1. routing ────────────────────────────────────────────────────
        state["status"] = "routing"
        state["intent"] = await classify_intent(query)
        yield _evt("routing", {"intent": state["intent"]})

        # 知识问答：走检索增强的单步回答
        if state["intent"] == "knowledge":
            yield _evt("summarizing", {})
            reply = await _answer_knowledge(query)
            save_session_state(
                session_id,
                context_updates=state["context"],
                map_context=map_context,
                last_query=query,
                last_reply=reply,
            )
            write_log(session_id, intent="knowledge", query=query, plan_steps=1,
                      reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
            yield _evt("done", {"reply": reply, "map_update": None})
            return

        # 地名定位：直接走 geocoder，单步完成
        if state["intent"] == "geo_query":
            yield _evt("summarizing", {})
            map_update, reply = await _handle_geo_query(query, session_id, map_context)
            save_session_state(session_id, last_reply=reply)
            write_log(session_id, intent="geo_query", query=query, plan_steps=1,
                      reply_preview=reply, duration_ms=int((time.monotonic()-_t0)*1000))
            yield _evt("done", {
                "reply": reply,
                "map_update": map_update.model_dump() if map_update else None,
            })
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
                "code": done_result.get("code") or "",
            })

        # ── 4. summarizing ────────────────────────────────────────────────
        yield _evt("summarizing", {})
        state = await _summarize(state)

        # ── 5. 构建 done 事件 ─────────────────────────────────────────────
        map_update_dict = state.get("map_update")
        resolved_map_context = map_context or {}
        if map_update_dict:
            resolved_map_context = {
                "center_lat": map_update_dict.get("center_lat"),
                "center_lon": map_update_dict.get("center_lon"),
                "zoom": map_update_dict.get("zoom"),
            }
        save_session_state(
            session_id,
            context_updates=state["context"],
            map_context=resolved_map_context,
            last_query=query,
            last_reply=state["final_reply"],
        )
        write_log(session_id, intent="execution", query=query,
                  plan_steps=len(state["plan"]),
                  reply_preview=state["final_reply"] or "",
                  duration_ms=int((time.monotonic()-_t0)*1000))
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
                        "code": s.get("code") or "",
                    }
                    for s in state["steps"]
                ],
            },
        })
    except Exception as exc:
        yield _evt("error", {"message": str(exc)})
