"""
tool.planning.database.execution_log_store — Execution log persistence.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class ExecutionLogStore:
    """Stores execution logs for tasks and plans."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def log_execution(self, action: str, status: str, task_id: str = "", plan_id: str = "", session_id: str = "", input_summary: str = "", output_summary: str = "", tokens_used: int = 0, model_used: str = "", duration: float = 0.0, error: str = "") -> int:
        now = time.time()
        cursor = self._conn.execute(
            "INSERT INTO execution_logs (task_id, plan_id, session_id, action, status, input_summary, output_summary, tokens_used, model_used, duration, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, plan_id, session_id, action, status, input_summary, output_summary, tokens_used, model_used, duration, error, now),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_logs_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM execution_logs WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_logs_by_session(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM execution_logs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_logs_by_plan(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM execution_logs WHERE plan_id = ? ORDER BY created_at",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]
