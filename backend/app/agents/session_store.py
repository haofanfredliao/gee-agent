"""In-memory session store for cross-request workflow context.

This module provides a minimal persistence layer for session-scoped context
until a database-backed store is introduced.
"""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict

_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _sid(session_id: str) -> str:
    return (session_id or "default").strip() or "default"


def _ensure(session_id: str) -> Dict[str, Any]:
    sid = _sid(session_id)
    if sid not in _SESSIONS:
        _SESSIONS[sid] = {
            "context": {},
            "map_context": {},
            "last_query": None,
            "last_reply": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    return _SESSIONS[sid]


def load_session_context(session_id: str) -> Dict[str, Any]:
    """Load a copy of session-scoped context for workflow initialization."""
    return deepcopy(_ensure(session_id).get("context", {}))


def save_session_state(
    session_id: str,
    *,
    context_updates: Dict[str, Any] | None = None,
    map_context: Dict[str, Any] | None = None,
    last_query: str | None = None,
    last_reply: str | None = None,
) -> None:
    """Persist session context and latest interaction metadata."""
    record = _ensure(session_id)
    if context_updates:
        record.setdefault("context", {}).update(context_updates)
    if map_context:
        record["map_context"] = map_context
    if last_query is not None:
        record["last_query"] = last_query
    if last_reply is not None:
        record["last_reply"] = last_reply
    record["updated_at"] = datetime.now(timezone.utc).isoformat()


def load_map_context(session_id: str) -> Dict[str, Any]:
    """Load latest known map context for a session."""
    return deepcopy(_ensure(session_id).get("map_context", {}))