"""数据库连接预留：SQLite 连接与初始表结构。

当前为 SQLite 实现，为后续迁移至 PostgreSQL 预留接口。
SessionStore 与 log_store 未来可从内存/文件迁移到此层。

用法
----
    from backend.app.services.db import get_connection, init_db

    # 在 FastAPI startup 事件中调用一次：
    init_db()
"""
import sqlite3
from pathlib import Path


def _get_db_path() -> Path:
    try:
        from backend.app.core.config import _project_root
        base = _project_root()
    except Exception:
        base = Path(__file__).resolve().parents[4]
    db_dir = base / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "gee_agent.db"


def get_connection() -> sqlite3.Connection:
    """
    返回一个 SQLite 连接。

    check_same_thread=False 允许在 FastAPI 异步请求中跨线程使用。
    调用方负责在使用完毕后关闭连接（建议用 with 语句）。
    """
    conn = sqlite3.connect(str(_get_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    初始化数据库表结构（幂等，表不存在才建立）。

    表定义：
      sessions     — 会话元信息（id, created_at, updated_at, context JSON）
      chat_history — 逐条消息记录（session_id FK, role, content, created_at）
    """
    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            context     TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            role        TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );
        """)
