"""
tool.planning.database.plan_store — CRUD operations for plans.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from utils.helpers import generate_request_id
from utils.logger import get_logger

logger = get_logger(__name__)


class PlanStore:
    """CRUD operations for plans in SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_plan(self, name: str, description: str = "", session_id: str = "", metadata: dict | None = None) -> str:
        now = time.time()
        plan_id = f"plan-{generate_request_id().split('-')[1]}"
        self._conn.execute(
            "INSERT INTO plans (id, name, description, status, created_at, updated_at, session_id, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, name, description, "pending", now, now, session_id, json.dumps(metadata or {})),
        )
        self._conn.commit()
        logger.info(f"[plan_store] Created plan: {plan_id} ({name})")
        return plan_id

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return dict(row) if row else None

    def update_plan(self, plan_id: str, **updates: Any) -> bool:
        updates["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [plan_id]
        self._conn.execute(f"UPDATE plans SET {sets} WHERE id = ?", values)
        self._conn.commit()
        return True

    def delete_plan(self, plan_id: str) -> bool:
        self._conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        self._conn.commit()
        return True

    def list_plans(self, session_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM plans WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
