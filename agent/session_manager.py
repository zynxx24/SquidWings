"""
agent.session_manager — Session lifecycle management.

Tracks per-session state: session_id, history, model_usage,
token_usage, active workspace, timing, and more.

Usage:
    manager = SessionManager()
    session = manager.create_session()
    session.record_model_usage("ag/claude-sonnet-4-6", prompt_tokens=500, completion_tokens=200)
    stats = manager.get_session_stats(session.session_id)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from utils.helpers import generate_session_id
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ModelUsageRecord:
    """Record of a single model API call.

    Attributes:
        model_id: Model that was called.
        prompt_tokens: Number of input tokens.
        completion_tokens: Number of output tokens.
        latency_ms: Request latency in milliseconds.
        task_type: Task type for this call.
        success: Whether the call succeeded.
        timestamp: When the call was made.
    """
    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    task_type: str = ""
    success: bool = True
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        """Total tokens used in this call."""
        return self.prompt_tokens + self.completion_tokens


@dataclass
class SessionState:
    """Full state of an active session.

    Attributes:
        session_id: Unique session identifier.
        created_at: Timestamp when the session was created.
        last_activity: Timestamp of the last activity.
        model_usage: List of all model usage records.
        override_model: User-selected model override (None = auto-routing).
        active_workspace: Active workspace path (as string, None = no workspace).
        metadata: Arbitrary session metadata.
        is_active: Whether the session is currently active.
        turn_count: Number of user turns in this session.
    """
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    model_usage: list[ModelUsageRecord] = field(default_factory=list)
    override_model: str | None = None
    active_workspace: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    turn_count: int = 0

    def touch(self) -> None:
        """Update last_activity timestamp."""
        self.last_activity = time.time()

    @property
    def total_prompt_tokens(self) -> int:
        """Cumulative prompt tokens across all calls."""
        return sum(r.prompt_tokens for r in self.model_usage)

    @property
    def total_completion_tokens(self) -> int:
        """Cumulative completion tokens across all calls."""
        return sum(r.completion_tokens for r in self.model_usage)

    @property
    def total_tokens(self) -> int:
        """Cumulative total tokens across all calls."""
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def models_used(self) -> set[str]:
        """Unique set of model IDs used in this session."""
        return {r.model_id for r in self.model_usage}

    @property
    def duration_seconds(self) -> float:
        """Session duration in seconds (from creation to last activity)."""
        return self.last_activity - self.created_at

    @property
    def success_rate(self) -> float:
        """Percentage of successful API calls."""
        if not self.model_usage:
            return 1.0
        successes = sum(1 for r in self.model_usage if r.success)
        return successes / len(self.model_usage)


class SessionManager:
    """Manages session lifecycles.

    Creates, retrieves, and ends sessions. Tracks all model usage
    and token statistics per session.

    Usage:
        manager = SessionManager()
        session = manager.create_session()
        session_id = session.session_id

        # After an API call:
        manager.record_model_usage(session_id, "ag/claude-sonnet-4-6", 500, 200, 1234.5)

        # Get stats:
        stats = manager.get_session_stats(session_id)

        # End session:
        manager.end_session(session_id)
    """

    def __init__(self, max_sessions: int = 50) -> None:
        """Initialize the session manager.

        Args:
            max_sessions: Maximum concurrent sessions before oldest is evicted.
        """
        self._sessions: dict[str, SessionState] = {}
        self._max_sessions = max_sessions

    def create_session(
        self,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionState:
        """Create a new session.

        Args:
            session_id: Optional custom session ID (auto-generated if None).
            metadata: Optional initial metadata.

        Returns:
            The newly created SessionState.
        """
        sid = session_id or generate_session_id()

        # Evict oldest session if at capacity
        if len(self._sessions) >= self._max_sessions:
            self._evict_oldest()

        session = SessionState(
            session_id=sid,
            metadata=metadata or {},
        )
        self._sessions[sid] = session

        logger.info(f"[session] Created session: {sid}")
        return session

    def get_session(self, session_id: str) -> SessionState | None:
        """Get a session by ID.

        Args:
            session_id: Session identifier.

        Returns:
            SessionState or None if not found.
        """
        return self._sessions.get(session_id)

    def get_or_create_session(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> SessionState:
        """Get an existing session or create a new one.

        Args:
            session_id: Session identifier.
            metadata: Optional metadata for new session.

        Returns:
            The existing or newly created SessionState.
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = self.create_session(session_id=session_id, metadata=metadata)
        return session

    def end_session(self, session_id: str) -> SessionState | None:
        """End (deactivate) a session.

        Does not delete the session — it's marked inactive and kept
        for statistics retrieval.

        Args:
            session_id: Session identifier.

        Returns:
            The ended SessionState, or None if not found.
        """
        session = self._sessions.get(session_id)
        if session:
            session.is_active = False
            session.touch()
            logger.info(
                f"[session] Ended session: {session_id} "
                f"(turns={session.turn_count}, tokens={session.total_tokens})"
            )
        return session

    def delete_session(self, session_id: str) -> bool:
        """Completely remove a session from the manager.

        Args:
            session_id: Session identifier.

        Returns:
            True if session was removed, False if not found.
        """
        removed = self._sessions.pop(session_id, None)
        if removed:
            logger.info(f"[session] Deleted session: {session_id}")
            return True
        return False

    def record_model_usage(
        self,
        session_id: str,
        model_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        task_type: str = "",
        success: bool = True,
    ) -> ModelUsageRecord | None:
        """Record an API call in the session.

        Args:
            session_id: Session identifier.
            model_id: Model used.
            prompt_tokens: Input token count.
            completion_tokens: Output token count.
            latency_ms: Request latency.
            task_type: The classified task type.
            success: Whether the call succeeded.

        Returns:
            The created ModelUsageRecord, or None if session not found.
        """
        session = self._sessions.get(session_id)
        if not session:
            logger.warning(f"[session] Cannot record usage: session {session_id} not found")
            return None

        record = ModelUsageRecord(
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            task_type=task_type,
            success=success,
        )
        session.model_usage.append(record)
        session.touch()

        logger.debug(
            f"[session] Recorded usage: model={model_id}, "
            f"tokens={record.total_tokens}, latency={latency_ms:.0f}ms"
        )
        return record

    def increment_turn(self, session_id: str) -> int:
        """Increment the turn counter for a session.

        Args:
            session_id: Session identifier.

        Returns:
            The new turn count, or 0 if session not found.
        """
        session = self._sessions.get(session_id)
        if session:
            session.turn_count += 1
            session.touch()
            return session.turn_count
        return 0

    def get_session_stats(self, session_id: str) -> dict[str, Any]:
        """Get comprehensive stats for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Dict with session statistics.
        """
        session = self._sessions.get(session_id)
        if not session:
            return {}

        # Per-model breakdown
        model_breakdown: dict[str, dict[str, Any]] = {}
        for record in session.model_usage:
            if record.model_id not in model_breakdown:
                model_breakdown[record.model_id] = {
                    "calls": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "avg_latency_ms": 0.0,
                    "successes": 0,
                    "failures": 0,
                }
            stats = model_breakdown[record.model_id]
            stats["calls"] += 1
            stats["total_tokens"] += record.total_tokens
            stats["prompt_tokens"] += record.prompt_tokens
            stats["completion_tokens"] += record.completion_tokens
            stats["avg_latency_ms"] += record.latency_ms
            if record.success:
                stats["successes"] += 1
            else:
                stats["failures"] += 1

        # Calculate average latencies
        for _model_id, stats in model_breakdown.items():
            if stats["calls"] > 0:
                stats["avg_latency_ms"] = stats["avg_latency_ms"] / stats["calls"]

        return {
            "session_id": session.session_id,
            "is_active": session.is_active,
            "created_at": session.created_at,
            "duration_seconds": session.duration_seconds,
            "turn_count": session.turn_count,
            "total_tokens": session.total_tokens,
            "total_prompt_tokens": session.total_prompt_tokens,
            "total_completion_tokens": session.total_completion_tokens,
            "total_api_calls": len(session.model_usage),
            "models_used": list(session.models_used),
            "success_rate": session.success_rate,
            "model_breakdown": model_breakdown,
            "override_model": session.override_model,
            "active_workspace": session.active_workspace,
        }

    @property
    def active_sessions(self) -> list[SessionState]:
        """All currently active sessions."""
        return [s for s in self._sessions.values() if s.is_active]

    @property
    def session_count(self) -> int:
        """Total number of sessions (active + ended)."""
        return len(self._sessions)

    def _evict_oldest(self) -> None:
        """Evict the oldest inactive session, or oldest overall if all active."""
        # Prefer evicting inactive sessions
        inactive = [
            (sid, s) for sid, s in self._sessions.items() if not s.is_active
        ]
        if inactive:
            oldest_id = min(inactive, key=lambda x: x[1].created_at)[0]
        else:
            oldest_id = min(self._sessions, key=lambda sid: self._sessions[sid].created_at)

        removed = self._sessions.pop(oldest_id, None)
        if removed:
            logger.debug(f"[session] Evicted session: {oldest_id}")

    def clear_all(self) -> None:
        """Clear all sessions."""
        self._sessions.clear()
        logger.debug("[session] All sessions cleared")
