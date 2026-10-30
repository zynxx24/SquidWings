"""
brain.memory.conversation_store — Store and manage conversation history with disk persistence.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.helpers import is_valid_session_id
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Message:
    """A single conversation message.

    Attributes:
        role: Message role (system, user, assistant, tool).
        content: Message text content.
        timestamp: When the message was created.
        model_used: Which model generated this (for assistant messages).
        token_count: Estimated token count.
        tool_calls: Tool calls in this message (if any).
        tool_call_id: Tool call ID (for tool result messages).
        tool_trace: What tools did during this turn, for the model's next turn.
    """
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    model_used: str = ""
    token_count: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str = ""
    tool_trace: str = ""

    def to_api_format(self) -> dict[str, Any]:
        """Convert to API message format."""
        content = self.content
        if self.tool_trace:
            # Only the final text is stored, so without this the next turn has
            # no idea which files were read or changed and re-reads everything.
            content = f"{content}\n\n[Internal note, not part of the reply. Tools run this turn: {self.tool_trace}]"
        msg: dict[str, Any] = {"role": self.role, "content": content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "model_used": self.model_used,
            "token_count": self.token_count,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
            "tool_trace": self.tool_trace,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Create a Message from dictionary."""
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp", time.time()),
            model_used=data.get("model_used", ""),
            token_count=data.get("token_count", 0),
            tool_calls=data.get("tool_calls", []),
            tool_call_id=data.get("tool_call_id", ""),
            tool_trace=data.get("tool_trace", ""),
        )


class ConversationStore:
    """Conversation history store with memory cache and disk persistence.

    Stores messages per session with metadata for token tracking,
    model usage analysis, and chat history retrieval.
    """

    def __init__(
        self,
        max_messages_per_session: int = 100,
        storage_dir: str | Path | None = None,
    ) -> None:
        self._sessions: dict[str, list[Message]] = {}
        self._session_metadata: dict[str, dict[str, Any]] = {}
        self.max_messages = max_messages_per_session

        if storage_dir is not None:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path(__file__).resolve().parent / "chat_store"

        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            self._load_all_from_disk()
        except Exception as e:
            logger.warning(f"[memory] Could not initialize chat_store directory {self.storage_dir}: {e}")

    def _load_all_from_disk(self) -> None:
        """Load all saved session files from storage_dir."""
        if not self.storage_dir.exists():
            return

        for file_path in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                session_id = data.get("session_id") or file_path.stem
                raw_messages = data.get("messages", [])
                messages = [Message.from_dict(m) for m in raw_messages]
                self._sessions[session_id] = messages
                self._session_metadata[session_id] = {
                    "title": data.get("title") or self._generate_title(messages),
                    "created_at": data.get("created_at") or (messages[0].timestamp if messages else time.time()),
                    "updated_at": data.get("updated_at") or (messages[-1].timestamp if messages else time.time()),
                    "model_used": data.get("model_used") or (messages[-1].model_used if messages else ""),
                }
            except Exception as e:
                logger.warning(f"[memory] Failed to load session from {file_path}: {e}")

    def _session_file(self, session_id: str) -> Path | None:
        """Disk path for a session, or None when the id is unsafe as a file name."""
        if not self.storage_dir:
            return None
        if not is_valid_session_id(session_id):
            logger.warning(f"[memory] Refusing unsafe session id: {session_id!r}")
            return None
        return self.storage_dir / f"{session_id}.json"

    def _save_session(self, session_id: str) -> None:
        """Save a single session to disk."""
        file_path = self._session_file(session_id)
        if file_path is None:
            return
        try:
            messages = self._sessions.get(session_id, [])
            meta = self._session_metadata.get(session_id, {})
            title = meta.get("title") or self._generate_title(messages)
            created_at = meta.get("created_at") or (messages[0].timestamp if messages else time.time())
            updated_at = meta.get("updated_at") or time.time()
            model_used = meta.get("model_used") or (messages[-1].model_used if messages else "")

            self._session_metadata[session_id] = {
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
                "model_used": model_used,
            }

            data = {
                "session_id": session_id,
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
                "model_used": model_used,
                "messages": [m.to_dict() for m in messages],
            }
            # A crash mid-write must not cost the whole conversation.
            temporary = file_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(file_path)
        except Exception as e:
            logger.warning(f"[memory] Failed to save session {session_id}: {e}")

    def _generate_title(self, messages: list[Message]) -> str:
        """Generate a title from the first user message."""
        for m in messages:
            if m.role == "user" and m.content.strip():
                clean = " ".join(m.content.strip().split())
                if len(clean) > 42:
                    return clean[:42] + "..."
                return clean
        return "New Chat"

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model_used: str = "",
        token_count: int = 0,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str = "",
        tool_trace: str = "",
    ) -> Message:
        """Add a message to a session and persist it.

        Args:
            session_id: Session identifier.
            role: Message role.
            content: Message content.
            model_used: Model that generated this.
            token_count: Token count estimate.
            tool_calls: Optional tool calls.
            tool_call_id: Optional tool call ID.
            tool_trace: Optional summary of the tools run this turn.

        Returns:
            The created Message.
        """
        now = time.time()
        if session_id not in self._sessions:
            self._sessions[session_id] = []
            self._session_metadata[session_id] = {
                "title": "New Chat",
                "created_at": now,
                "updated_at": now,
                "model_used": model_used or "",
            }

        msg = Message(
            role=role,
            content=content,
            model_used=model_used,
            token_count=token_count,
            tool_calls=tool_calls or [],
            tool_call_id=tool_call_id,
            tool_trace=tool_trace,
            timestamp=now,
        )

        session = self._sessions[session_id]
        session.append(msg)

        # Update metadata
        meta = self._session_metadata.setdefault(session_id, {
            "title": "New Chat",
            "created_at": now,
            "updated_at": now,
            "model_used": "",
        })
        meta["updated_at"] = now
        if model_used:
            meta["model_used"] = model_used

        if meta.get("title") in ("", "New Chat") and role == "user":
            meta["title"] = self._generate_title(session)

        # Trim old messages if over limit
        if len(session) > self.max_messages:
            removed = len(session) - self.max_messages
            self._sessions[session_id] = session[removed:]
            logger.debug(f"[memory] Trimmed {removed} old messages from {session_id}")

        self._save_session(session_id)
        return msg

    def get_history(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[Message]:
        """Get conversation history for a session.

        Args:
            session_id: Session identifier.
            limit: Optional max messages to return (most recent).

        Returns:
            List of Message objects.
        """
        if session_id not in self._sessions:
            file_path = self._session_file(session_id)
            if file_path is not None and file_path.exists():
                try:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
                    self._sessions[session_id] = [Message.from_dict(m) for m in data.get("messages", [])]
                    self._session_metadata[session_id] = {
                        "title": data.get("title") or self._generate_title(self._sessions[session_id]),
                        "created_at": data.get("created_at") or time.time(),
                        "updated_at": data.get("updated_at") or time.time(),
                        "model_used": data.get("model_used") or "",
                    }
                except Exception as e:
                    logger.warning(f"[memory] Failed to load session {session_id}: {e}")

        messages = self._sessions.get(session_id, [])
        if limit:
            return messages[-limit:]
        return list(messages)

    def get_history_as_api_messages(
        self,
        session_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get history formatted for the API."""
        messages = self.get_history(session_id, limit)
        return [m.to_api_format() for m in messages]

    def get_total_tokens(self, session_id: str) -> int:
        """Get total token count for a session."""
        return sum(m.token_count for m in self._sessions.get(session_id, []))

    def clear_session(self, session_id: str) -> None:
        """Clear all messages for a session."""
        if session_id in self._sessions:
            self._sessions[session_id] = []
            if session_id in self._session_metadata:
                self._session_metadata[session_id]["updated_at"] = time.time()
            self._save_session(session_id)
        logger.debug(f"[memory] Cleared session {session_id}")

    def delete_session(self, session_id: str) -> bool:
        """Delete a session completely from memory and disk."""
        self._sessions.pop(session_id, None)
        self._session_metadata.pop(session_id, None)
        file_path = self._session_file(session_id)
        if file_path is not None and file_path.exists():
            try:
                file_path.unlink()
                logger.debug(f"[memory] Deleted session file {file_path}")
                return True
            except Exception as e:
                logger.warning(f"[memory] Failed to delete session file {file_path}: {e}")
                return False
        return True

    def set_title(self, session_id: str, title: str) -> None:
        """Set a custom title for a session."""
        meta = self._session_metadata.setdefault(session_id, {
            "title": title,
            "created_at": time.time(),
            "updated_at": time.time(),
            "model_used": "",
        })
        meta["title"] = title
        meta["updated_at"] = time.time()
        self._save_session(session_id)

    def get_session_meta(self, session_id: str) -> dict[str, Any]:
        """Get metadata for a session."""
        if session_id not in self._session_metadata and session_id in self._sessions:
            messages = self._sessions[session_id]
            self._session_metadata[session_id] = {
                "title": self._generate_title(messages),
                "created_at": messages[0].timestamp if messages else time.time(),
                "updated_at": messages[-1].timestamp if messages else time.time(),
                "model_used": messages[-1].model_used if messages else "",
            }
        return dict(self._session_metadata.get(session_id, {
            "title": "New Chat",
            "created_at": time.time(),
            "updated_at": time.time(),
            "model_used": "",
        }))

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all chat sessions with metadata, ordered by updated_at descending."""
        results: list[dict[str, Any]] = []
        for sid, messages in self._sessions.items():
            meta = self._session_metadata.get(sid, {})
            title = meta.get("title") or self._generate_title(messages)
            created_at = meta.get("created_at") or (messages[0].timestamp if messages else time.time())
            updated_at = meta.get("updated_at") or (messages[-1].timestamp if messages else created_at)
            model_used = meta.get("model_used") or (messages[-1].model_used if messages else "")

            preview = ""
            for m in reversed(messages):
                if m.content.strip():
                    preview = " ".join(m.content.strip().split())[:80]
                    break

            results.append({
                "session_id": sid,
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
                "message_count": len(messages),
                "model": model_used,
                "preview": preview,
            })

        results.sort(key=lambda x: x["updated_at"], reverse=True)
        return results

    def clear_all(self) -> None:
        """Clear all sessions."""
        self._sessions.clear()
        self._session_metadata.clear()
        if self.storage_dir and self.storage_dir.exists():
            for f in self.storage_dir.glob("*.json"):
                with contextlib.suppress(Exception):
                    f.unlink()

    @property
    def session_ids(self) -> list[str]:
        """All active session IDs."""
        return list(self._sessions.keys())

    def session_message_count(self, session_id: str) -> int:
        """Number of messages in a session."""
        return len(self._sessions.get(session_id, []))

    def get_last_used_model(self, session_id: str) -> str | None:
        """Get the model ID that was last successfully used in this session."""
        messages = self._sessions.get(session_id)
        if not messages and self.storage_dir:
            self.get_history(session_id)  # lazily loads the session from disk
            messages = self._sessions.get(session_id)
        if not messages:
            return None
        for m in reversed(messages):
            if m.role == "assistant" and m.model_used:
                return m.model_used
        return None
