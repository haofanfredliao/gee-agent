"""全局工作流状态定义。

WorkflowState 是贯穿整个 orchestrator 生命周期的单一可变字典，
每个状态节点读取/写入它，并在 status 字段中记录当前所处阶段。

状态流转：
  routing → planning → executing → summarizing → terminated
"""
from typing import Any, Dict, List, Optional, TypedDict


class StepResult(TypedDict):
    """单个执行步骤的结果。"""
    step_index: int
    description: str       # 步骤的中文说明
    tool: str              # 调用的工具名称
    output: str            # 工具执行后的文本输出（stdout）
    tile_url: Optional[str]  # 若产生了地图图层，记录其 tile URL
    success: bool


class WorkflowState(TypedDict):
    """全局工作流状态字典。贯穿 orchestrator 全流程。"""
    session_id: str
    query: str

    # 状态机核心字段
    intent: str    # "execution" | "knowledge" | "unknown"
    status: str    # "routing" | "planning" | "executing" | "summarizing" | "terminated"

    # 规划与执行
    plan: List[Dict[str, Any]]   # 有序子任务列表，每项含 description/type/asset_id
    steps: List[StepResult]      # 已完成步骤的结果列表
    current_step: int            # 当前正在执行的步骤索引

    # 跨步骤共享上下文（inspect 步骤写入，execute 步骤读取）
    context: Dict[str, Any]
    session_context: Dict[str, Any]   # 会话级上下文（跨请求持久）

    # 输出
    map_update: Optional[Dict[str, Any]]   # 地图更新，最终转为 MapUpdate
    final_reply: Optional[str]             # 汇总后的最终回复
    error: Optional[str]


def make_initial_state(query: str, session_id: str = "") -> WorkflowState:
    """构造初始工作流状态。"""
    return {
        "session_id": session_id,
        "query": query,
        "intent": "unknown",
        "status": "routing",
        "plan": [],
        "steps": [],
        "current_step": 0,
        "context": {},
        "session_context": {},
        "map_update": None,
        "final_reply": None,
        "error": None,
    }


def format_status(state: WorkflowState) -> str:
    """返回当前状态的人类可读摘要，用于聊天界面中展示中间状态。"""
    lines = [
        f"**[工作流状态]** `{state['status']}`",
        f"- 意图识别：`{state['intent']}`",
    ]
    if state["plan"]:
        lines.append(f"- 任务计划（共 {len(state['plan'])} 步）：")
        for i, step in enumerate(state["plan"]):
            marker = "✅" if i < len(state["steps"]) else ("⏳" if i == state["current_step"] else "⬜")
            lines.append(f"  {marker} 步骤 {i+1}：{step.get('description', '')}")
    if state["steps"]:
        last = state["steps"][-1]
        preview = last["output"][:200].strip() if last["output"] else "(无输出)"
        lines.append(f"- 最新输出预览：\n  ```\n  {preview}\n  ```")
    return "\n".join(lines)
