"""
brain.memory.persistent_memory — Persistent memory for cross-model context.

Memory system yang persist progress/edits/keputusan antar sesi
dan antar pergantian model. Saat ganti model, AI bisa scan
memory sebelumnya → hemat token karena ga perlu re-discover
context dari nol.

Memory disimpan sebagai JSON file di disk — survive restart.

Struktur memory per session:
- session_id: ID sesi
- files_touched: {path: {action, last_content_hash, lines_changed, timestamp}}
- decisions: [{decision, reason, timestamp}]
- progress: [{phase, step, status, detail, timestamp}]
- key_facts: [{fact, source, timestamp}]
- model_switches: [{from_model, to_model, timestamp, reason}]
- summary: Compact summary of everything (auto-generated)

Designed untuk super-vision: AI baca summary → langsung tahu
di mana dia berhenti, file apa yg udah diedit, keputusan apa
yg udah dibuat. Hemat token karena summary compact.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FileRecord:
    """Record of a file the AI has touched."""
    path: str
    action: str = ""  # "created", "edited", "read", "replaced"
    content_hash: str = ""  # MD5 of last known content
    lines_changed: int = 0
    last_edit_summary: str = ""  # What was changed
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "action": self.action,
            "content_hash": self.content_hash,
            "lines_changed": self.lines_changed,
            "last_edit_summary": self.last_edit_summary,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FileRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ProgressEntry:
    """Tracks progress through multi-step tasks."""
    phase: str = ""
    step: str = ""
    status: str = "pending"  # pending, in_progress, done, failed
    detail: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "step": self.step,
            "status": self.status,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProgressEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SessionMemory:
    """Complete memory state for a session."""
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    # Core memory entries
    files_touched: dict[str, FileRecord] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    progress: list[ProgressEntry] = field(default_factory=list)
    key_facts: list[dict[str, Any]] = field(default_factory=list)
    model_switches: list[dict[str, Any]] = field(default_factory=list)
    errors_encountered: list[dict[str, Any]] = field(default_factory=list)

    # Auto-generated compact summary
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "files_touched": {
                k: v.to_dict() for k, v in self.files_touched.items()
            },
            "decisions": self.decisions,
            "progress": [p.to_dict() for p in self.progress],
            "key_facts": self.key_facts,
            "model_switches": self.model_switches,
            "errors_encountered": self.errors_encountered,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SessionMemory:
        mem = cls(
            session_id=d.get("session_id", ""),
            created_at=d.get("created_at", time.time()),
            last_updated=d.get("last_updated", time.time()),
            decisions=d.get("decisions", []),
            key_facts=d.get("key_facts", []),
            model_switches=d.get("model_switches", []),
            errors_encountered=d.get("errors_encountered", []),
            summary=d.get("summary", ""),
        )
        # Reconstruct file records
        for k, v in d.get("files_touched", {}).items():
            mem.files_touched[k] = FileRecord.from_dict(v)
        # Reconstruct progress
        for p in d.get("progress", []):
            mem.progress.append(ProgressEntry.from_dict(p))
        return mem


class PersistentMemory:
    """Persistent memory system that saves to disk.

    Memory survives restart dan model switches. AI bisa scan
    memory summary → langsung pickup dari tempat berhenti.

    Usage:
        mem = PersistentMemory(storage_dir="./brain/memory/store")

        # Record file edit
        mem.record_file_touch("session-1", "/path/file.py", "edited",
                              lines_changed=42, summary="Added login route")

        # Record progress
        mem.record_progress("session-1", "Phase 4", "Create orchestrator", "done")

        # Record decision
        mem.record_decision("session-1", "Use FastAPI instead of Flask",
                           reason="Better async + WebSocket support")

        # When switching models: get compact summary
        summary = mem.get_memory_summary("session-1")
        # → Inject into system prompt for new model
    """

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self._storage = Path(storage_dir) if storage_dir else (
            Path.cwd() / "brain" / "memory" / "store"
        )
        self._storage.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, SessionMemory] = {}

    def _path_for(self, session_id: str) -> Path:
        """Get file path for a session's memory."""
        safe_id = session_id.replace("/", "_").replace("\\", "_")
        return self._storage / f"{safe_id}.json"

    def _load(self, session_id: str) -> SessionMemory:
        """Load session memory from cache or disk."""
        if session_id in self._cache:
            return self._cache[session_id]

        path = self._path_for(session_id)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                mem = SessionMemory.from_dict(data)
                self._cache[session_id] = mem
                logger.debug(f"[memory] Loaded memory for session {session_id}")
                return mem
            except Exception as e:
                logger.warning(f"[memory] Failed to load {path}: {e}")

        mem = SessionMemory(session_id=session_id)
        self._cache[session_id] = mem
        return mem

    def _save(self, session_id: str) -> None:
        """Save session memory to disk."""
        mem = self._cache.get(session_id)
        if not mem:
            return

        mem.last_updated = time.time()
        path = self._path_for(session_id)

        try:
            path.write_text(
                json.dumps(mem.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug(f"[memory] Saved memory for session {session_id}")
        except Exception as e:
            logger.error(f"[memory] Failed to save {path}: {e}")

    # ─── Record Methods ──────────────────────────────────────────────

    def record_file_touch(
        self,
        session_id: str,
        file_path: str,
        action: str,
        lines_changed: int = 0,
        summary: str = "",
        content: str | None = None,
    ) -> None:
        """Record that AI touched a file.

        Args:
            session_id: Session ID.
            file_path: Path to file.
            action: "created", "edited", "read", "replaced", "deleted".
            lines_changed: Number of lines changed.
            summary: Short description of what changed.
            content: File content (for hashing, optional).
        """
        mem = self._load(session_id)

        content_hash = ""
        if content:
            content_hash = hashlib.md5(content.encode()).hexdigest()[:12]

        rec = FileRecord(
            path=file_path,
            action=action,
            content_hash=content_hash,
            lines_changed=lines_changed,
            last_edit_summary=summary,
        )
        mem.files_touched[file_path] = rec
        self._save(session_id)

    def record_progress(
        self,
        session_id: str,
        phase: str,
        step: str,
        status: str = "done",
        detail: str = "",
    ) -> None:
        """Record progress on a task.

        Args:
            session_id: Session ID.
            phase: Phase name (e.g., "Phase 4").
            step: Step name (e.g., "Create orchestrator.py").
            status: "pending", "in_progress", "done", "failed".
            detail: Extra detail.
        """
        mem = self._load(session_id)

        # Update existing entry if same phase+step
        for p in mem.progress:
            if p.phase == phase and p.step == step:
                p.status = status
                p.detail = detail
                p.timestamp = time.time()
                self._save(session_id)
                return

        mem.progress.append(ProgressEntry(
            phase=phase, step=step, status=status, detail=detail,
        ))
        self._save(session_id)

    def record_decision(
        self,
        session_id: str,
        decision: str,
        reason: str = "",
    ) -> None:
        """Record a design decision."""
        mem = self._load(session_id)
        mem.decisions.append({
            "decision": decision,
            "reason": reason,
            "timestamp": time.time(),
        })
        # Keep last 50 decisions
        if len(mem.decisions) > 50:
            mem.decisions = mem.decisions[-50:]
        self._save(session_id)

    def record_key_fact(
        self,
        session_id: str,
        fact: str,
        source: str = "",
    ) -> None:
        """Record an important fact."""
        mem = self._load(session_id)
        mem.key_facts.append({
            "fact": fact,
            "source": source,
            "timestamp": time.time(),
        })
        # Keep last 100 facts
        if len(mem.key_facts) > 100:
            mem.key_facts = mem.key_facts[-100:]
        self._save(session_id)

    def record_model_switch(
        self,
        session_id: str,
        from_model: str,
        to_model: str,
        reason: str = "",
    ) -> None:
        """Record a model switch event."""
        mem = self._load(session_id)
        mem.model_switches.append({
            "from": from_model,
            "to": to_model,
            "reason": reason,
            "timestamp": time.time(),
        })
        self._save(session_id)

    def record_error(
        self,
        session_id: str,
        error: str,
        context: str = "",
    ) -> None:
        """Record an error encountered."""
        mem = self._load(session_id)
        mem.errors_encountered.append({
            "error": error,
            "context": context,
            "timestamp": time.time(),
        })
        # Keep last 20
        if len(mem.errors_encountered) > 20:
            mem.errors_encountered = mem.errors_encountered[-20:]
        self._save(session_id)

    # ─── Retrieval Methods ───────────────────────────────────────────

    def get_memory(self, session_id: str) -> SessionMemory:
        """Get full session memory."""
        return self._load(session_id)

    def get_memory_summary(
        self,
        session_id: str,
        max_length: int = 2000,
    ) -> str:
        """Generate a compact memory summary for injection into prompts.

        This is the main method for token-saving: generates a compact
        summary that tells the AI exactly where things left off.

        Args:
            session_id: Session ID.
            max_length: Max summary length in chars.

        Returns:
            Compact markdown summary string.
        """
        mem = self._load(session_id)

        if not mem.files_touched and not mem.progress and not mem.decisions:
            return ""

        parts: list[str] = []
        parts.append("=== MEMORY (Previous Session Context) ===")

        # Files touched
        if mem.files_touched:
            parts.append("\n📁 FILES TOUCHED:")
            for path, rec in sorted(
                mem.files_touched.items(),
                key=lambda x: x[1].timestamp,
                reverse=True,
            )[:20]:  # Last 20 files
                summary = f" — {rec.last_edit_summary}" if rec.last_edit_summary else ""
                parts.append(
                    f"  [{rec.action}] {path} "
                    f"({rec.lines_changed}L){summary}"
                )

        # Progress
        if mem.progress:
            parts.append("\n📊 PROGRESS:")
            for p in mem.progress[-15:]:  # Last 15 progress entries
                icon = {
                    "done": "✅",
                    "in_progress": "🔄",
                    "pending": "⏳",
                    "failed": "❌",
                }.get(p.status, "·")
                detail = f" — {p.detail}" if p.detail else ""
                parts.append(f"  {icon} {p.phase}/{p.step}: {p.status}{detail}")

        # Decisions
        if mem.decisions:
            parts.append("\n🧠 KEY DECISIONS:")
            for d in mem.decisions[-10:]:  # Last 10
                reason = f" (reason: {d['reason']})" if d.get("reason") else ""
                parts.append(f"  • {d['decision']}{reason}")

        # Key facts
        if mem.key_facts:
            parts.append("\n📌 KEY FACTS:")
            for f in mem.key_facts[-10:]:  # Last 10
                parts.append(f"  • {f['fact']}")

        # Model switches
        if mem.model_switches:
            last_switch = mem.model_switches[-1]
            parts.append(
                f"\n🔀 LAST MODEL: {last_switch['from']} → {last_switch['to']}"
            )

        # Errors
        if mem.errors_encountered:
            parts.append("\n⚠️ RECENT ERRORS:")
            for e in mem.errors_encountered[-5:]:  # Last 5
                parts.append(f"  • {e['error'][:100]}")

        parts.append("\n=== END MEMORY ===")

        summary = "\n".join(parts)

        # Truncate if too long
        if len(summary) > max_length:
            summary = summary[:max_length - 20] + "\n... [truncated]"

        # Cache the summary
        mem.summary = summary
        self._save(session_id)

        return summary

    def get_files_touched(self, session_id: str) -> dict[str, FileRecord]:
        """Get all files this session has touched."""
        return self._load(session_id).files_touched

    def get_progress(self, session_id: str) -> list[ProgressEntry]:
        """Get progress entries."""
        return self._load(session_id).progress

    def clear_session(self, session_id: str) -> None:
        """Clear memory for a session."""
        self._cache.pop(session_id, None)
        path = self._path_for(session_id)
        if path.exists():
            path.unlink()
        logger.info(f"[memory] Cleared memory for {session_id}")

    def list_sessions(self) -> list[str]:
        """List all sessions with saved memory."""
        sessions = []
        for f in self._storage.glob("*.json"):
            sessions.append(f.stem)
        return sessions
