"""
tool.execute.execute_discard_change_file.change_tracker — Track file modifications.

Records all file changes made by the agent for auditing and rollback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ChangeRecord:
    """A single recorded file change.

    Attributes:
        file_path: Path to the changed file.
        operation: Type of change (create, edit, delete, replace).
        timestamp: When the change was made.
        session_id: Session that made the change.
        diff_summary: Short summary of the change.
    """
    file_path: str
    operation: str
    timestamp: float = field(default_factory=time.time)
    session_id: str = "default"
    diff_summary: str = ""


class ChangeTracker:
    """Tracks all file modifications made by the agent.

    Maintains an in-memory log of all changes per session,
    enabling rollback and auditing.
    """

    def __init__(self) -> None:
        self._changes: list[ChangeRecord] = []

    def record_change(
        self,
        file_path: str,
        operation: str,
        session_id: str = "default",
        diff_summary: str = "",
    ) -> ChangeRecord:
        """Record a file change.

        Args:
            file_path: Path to the changed file.
            operation: Type of change.
            session_id: Session identifier.
            diff_summary: Short summary of the diff.

        Returns:
            The created ChangeRecord.
        """
        record = ChangeRecord(
            file_path=file_path,
            operation=operation,
            session_id=session_id,
            diff_summary=diff_summary,
        )
        self._changes.append(record)
        logger.debug(f"[tracker] Recorded: {operation} on {file_path}")
        return record

    def get_changes(
        self,
        session_id: str | None = None,
        file_path: str | None = None,
    ) -> list[ChangeRecord]:
        """Get recorded changes with optional filtering.

        Args:
            session_id: Filter by session.
            file_path: Filter by file path.

        Returns:
            List of matching ChangeRecords.
        """
        results = self._changes
        if session_id:
            results = [c for c in results if c.session_id == session_id]
        if file_path:
            results = [c for c in results if c.file_path == file_path]
        return results

    def get_changes_since(self, since_timestamp: float) -> list[ChangeRecord]:
        """Get changes made after a timestamp.

        Args:
            since_timestamp: Unix timestamp cutoff.

        Returns:
            List of ChangeRecords after the timestamp.
        """
        return [c for c in self._changes if c.timestamp >= since_timestamp]

    def get_modified_files(self, session_id: str | None = None) -> list[str]:
        """Get unique list of modified file paths.

        Args:
            session_id: Optional session filter.

        Returns:
            List of unique file paths.
        """
        changes = self.get_changes(session_id=session_id)
        seen: set[str] = set()
        result: list[str] = []
        for c in changes:
            if c.file_path not in seen:
                seen.add(c.file_path)
                result.append(c.file_path)
        return result

    def clear(self, session_id: str | None = None) -> int:
        """Clear recorded changes.

        Args:
            session_id: If provided, only clear changes for this session.

        Returns:
            Number of records cleared.
        """
        if session_id:
            before = len(self._changes)
            self._changes = [c for c in self._changes if c.session_id != session_id]
            cleared = before - len(self._changes)
        else:
            cleared = len(self._changes)
            self._changes.clear()
        return cleared

    @property
    def total_changes(self) -> int:
        """Total number of recorded changes."""
        return len(self._changes)
