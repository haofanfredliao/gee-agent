"""结构化对话日志：JSONL 格式落盘。

每次 orchestrator 完成工作流，调用 write_log() 追加一条记录到
data/logs/conversations.jsonl，格式为 newline-delimited JSON。
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_logger = logging.getLogger("gee_agent.log_store")


def _get_log_path() -> Path:
    try:
        from backend.app.core.config import _project_root
        base = _project_root()
    except Exception:
        base = Path(__file__).resolve().parents[4]
    log_dir = base / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "conversations.jsonl"


def write_log(
    session_id: str,
    *,
    intent: str,
    query: str,
    plan_steps: int = 0,
    reply_preview: str = "",
    duration_ms: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    追加一条会话记录到 JSONL 日志文件（非阻塞，失败时静默记录到 logger）。

    Parameters
    ----------
    session_id    : 会话 UUID
    intent        : "execution" | "knowledge" | "geo_query"
    query         : 用户原始输入（截断至 500 字符）
    plan_steps    : execution 分支的步骤数
    reply_preview : 最终回复摘要（截断至 300 字符）
    duration_ms   : 整个工作流耗时（毫秒）
    extra         : 附加键值对（如 map_update 信息）
    """
    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "intent": intent,
        "query": query[:500],
        "plan_steps": plan_steps,
        "reply_preview": reply_preview[:300],
    }
    if duration_ms is not None:
        record["duration_ms"] = duration_ms
    if extra:
        record["extra"] = extra
    try:
        with open(_get_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        _logger.warning("log_store write failed: %s", exc)
