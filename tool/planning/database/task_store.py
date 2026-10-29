"""
tool.planning.database.task_store — CRUD operations for tasks.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from utils.helpers import generate_request_id
from utils.logger import get_logger

logger = get_logger(__name__)


class TaskStore:
    """CRUD operations for individual tasks within plans."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_task(self, plan_id: str, name: str, description: str = "", priority: int = 0, estimated_tokens: int = 0, depends_on: list[str] | None = None) -> str:
        now = time.time()
        task_id = f"task-{generate_request_id().split('-')[1]}"
        self._conn.execute(
            "INSERT INTO tasks (id, plan_id, name, description, status, priority, estimated_tokens, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, plan_id, name, description, "pending", priority, estimated_tokens, now, now),
        )
        if depends_on:
            for dep_id in depends_on:
                self._conn.execute(
                    "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
                    (task_id, dep_id),
                )
        self._conn.commit()
        logger.info(f"[task_store] Created task: {task_id} ({name})")
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def update_status(self, task_id: str, status: str, result: str = "", error: str = "") -> bool:
        now = time.time()
        updates: dict[str, Any] = {"status": status, "updated_at": now}
        if result:
            updates["result"] = result
        if error:
            updates["error"] = error
        if status == "running":
            updates["started_at"] = now
        elif status in ("completed", "failed"):
            updates["completed_at"] = now
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", values)
        self._conn.commit()
        return True

    def get_pending_tasks(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? AND status = 'pending' ORDER BY priority DESC",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_plan(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? ORDER BY priority DESC, created_at",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_dependencies(self, task_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [r["depends_on_task_id"] for r in rows]

    def are_dependencies_met(self, task_id: str) -> bool:
        deps = self.get_dependencies(task_id)
        if not deps:
            return True
        placeholders = ",".join("?" * len(deps))
        row = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM tasks WHERE id IN ({placeholders}) AND status != 'completed'",
            deps,
        ).fetchone()
        if not row:
            return True
        return bool(int(row["cnt"]) == 0)
