"""app/tools 包：按功能分为 explanation（获取信息）和 execution（执行操作）两个子模块。"""
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


class ToolResult(TypedDict, total=False):
    """标准化工具返回结构，供 orchestrator 统一处理步骤结果。"""

    status: str             # "ok" | "error" | "placeholder"
    output: str             # 主输出（文本、摘要等）
    error: Optional[str]    # 错误信息（status=="error" 时填写）
    data: Dict[str, Any]    # 结构化附加数据（tile_url、layers、stats 等）

