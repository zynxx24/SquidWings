"""What the AI changed in a chat's folder, turn by turn, so it can be seen, compared and undone.

At the start of a turn the folder is scanned (only files whose size or time changed are read again); at the end it is
scanned once more, and every file that appeared, changed or disappeared in between is written down as one turn. This
works whatever wrote the file: the file tools, a shell command, a build, a generator. Edits made by hand between
turns (the IDE, an editor, a ``git pull``) are taken in as the new starting point and never blamed on the AI.

Text files up to ``MAX_STORED_BYTES`` are kept by content (one copy per distinct content, named by its hash), so a
change shows as a diff and can be put back. Bigger and binary files are tracked by size and time only.

Everything lives in ``<folder>/.agent_memory/history/``: ``index.json`` (the last known state), ``turns.jsonl``
(one line per turn) and ``objects/`` (the stored contents).
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HISTORY_DIR = Path(".agent_memory") / "history"
# Folders that are tools' output or caches, never the work itself.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".agent_memory", ".vault",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".cache", ".parcel-cache", ".turbo",
    ".gradle", ".idea", ".vscode", "vendor", "target", ".dart_tool", ".svelte-kit",
}
SKIP_FILES = {".session-id", ".DS_Store", "Thumbs.db"}
MAX_STORED_BYTES = 1_000_000
_SNIFF = 8192

_lock = threading.Lock()


@dataclass
class Change:
    path: str
    status: str                    # "added", "modified" or "deleted"
    before: str | None = None      # content hash before the turn, when stored
    after: str | None = None       # content hash after the turn, when stored
    binary: bool = False
    size: int = 0
    added: int = 0                 # lines added and removed, for text
    removed: int = 0


@dataclass
class Turn:
    id: str
    started: float
    ended: float
    by: str = "ai"                 # "ai", "editor" (saved in the IDE) or "restore"
    prompt: str = ""
    session: str = ""
    changes: list[Change] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["added"] = sum(c.added for c in self.changes)
        data["removed"] = sum(c.removed for c in self.changes)
        return data


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info")


def scan(root: Path) -> dict[str, tuple[int, int]]:
    """Every file of the folder that counts as work: relative path -> (mtime_ns, size)."""
    found: dict[str, tuple[int, int]] = {}
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if not _skip_dir(entry.name):
                        stack.append(Path(entry.path))
                elif entry.is_file() and entry.name not in SKIP_FILES:
                    stat = entry.stat()
                    found[Path(entry.path).relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
    return found


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:_SNIFF]


def _lines(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines()


def line_stats(before: bytes | None, after: bytes | None) -> tuple[int, int]:
    """Lines added and removed between two texts."""
    added = removed = 0
    for line in difflib.unified_diff(_lines(before or b""), _lines(after or b""), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


_STATUSES = {"added", "modified", "deleted"}
_BY = {"ai", "editor", "restore"}
_HEX = set("0123456789abcdef")


def _digest(value: Any) -> str | None:
    return value if isinstance(value, str) and len(value) == 64 and set(value) <= _HEX else None


def _clean_turn(data: dict[str, Any]) -> Turn | None:
    """A turn as read back from ``turns.jsonl``, checked field by field. The file sits in the chat's folder, where
    the AI can write too, so nothing in it is trusted to be well formed before it reaches a page."""
    changes = []
    for item in data.get("changes") or []:
        status = str(item.get("status"))
        path = str(item.get("path") or "")
        if status not in _STATUSES or not path or len(path) > 1000:
            continue
        changes.append(Change(
            path, status, _digest(item.get("before")), _digest(item.get("after")), bool(item.get("binary")),
            int(item.get("size") or 0), int(item.get("added") or 0), int(item.get("removed") or 0),
        ))
    turn_id = str(data.get("id") or "")
    if not changes or not turn_id.isalnum() or len(turn_id) > 40:
        return None
    by = str(data.get("by") or "ai")
    return Turn(turn_id, float(data.get("started") or 0), float(data.get("ended") or 0), by if by in _BY else "ai",
                str(data.get("prompt") or "")[:300], str(data.get("session") or "")[:80], changes)


class FileHistory:
    """The change history of one folder."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.dir = self.root / HISTORY_DIR
        self.objects = self.dir / "objects"
        self.index_path = self.dir / "index.json"
        self.turns_path = self.dir / "turns.jsonl"

    # ── storage ───────────────────────────────────────────────────────────────
    def _load_index(self) -> dict[str, list[Any]]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_index(self, index: dict[str, list[Any]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.index_path)

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest

    def _store(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._object_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
        return digest

    def content(self, digest: str | None) -> bytes | None:
        """A stored content by its hash, or None."""
        if not digest or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            return None
        try:
            return self._object_path(digest).read_bytes()
        except OSError:
            return None

    def _read(self, relative: str, size: int) -> tuple[str | None, bool]:
        """Store a file's content when it is small text. Returns (hash or None, binary)."""
        if size > MAX_STORED_BYTES:
            return None, False
        try:
            data = (self.root / relative).read_bytes()
        except OSError:
            return None, False
        if is_binary(data):
            return None, True
        return self._store(data), False

    def _refresh(self, index: dict[str, list[Any]], now: dict[str, tuple[int, int]]) -> list[Change]:
        """Bring the index up to the folder as it is now. Returns what differs from what the index knew."""
        changes: list[Change] = []
        for relative, (mtime, size) in now.items():
            known = index.get(relative)
            if known and known[0] == mtime and known[1] == size:
                continue
            digest, binary = self._read(relative, size)
            before = known[2] if known else None
            if known and digest is not None and digest == before:
                index[relative] = [mtime, size, digest, binary]     # touched, not changed
                continue
            index[relative] = [mtime, size, digest, binary]
            added, removed = (0, 0) if binary else line_stats(self.content(before), self.content(digest))
            changes.append(Change(relative, "modified" if known else "added", before, digest, binary, size, added, removed))
        for relative in [r for r in index if r not in now]:
            known = index.pop(relative)
            added, removed = (0, 0) if known[3] else line_stats(self.content(known[2]), None)
            changes.append(Change(relative, "deleted", known[2], None, bool(known[3]), 0, added, removed))
        return sorted(changes, key=lambda c: c.path)

    def _append(self, turn: Turn) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.turns_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")

    # ── turns ─────────────────────────────────────────────────────────────────
    def begin(self) -> float:
        """Take the folder as it is now as the starting point (edits since the last turn are not the AI's).
        Returns the moment, to hand to ``end``."""
        with _lock:
            index = self._load_index()
            self._refresh(index, scan(self.root))
            self._save_index(index)
        return time.time()

    def end(self, started: float, prompt: str = "", session: str = "", by: str = "ai") -> Turn | None:
        """Write down what changed since ``begin``. None when nothing did."""
        with _lock:
            index = self._load_index()
            changes = self._refresh(index, scan(self.root))
            self._save_index(index)
            if not changes:
                return None
            turn = Turn(uuid.uuid4().hex[:12], started, time.time(), by, " ".join(prompt.split())[:300], session, changes)
            self._append(turn)
            return turn

    def turns(self) -> list[Turn]:
        """Every turn that changed something, oldest first."""
        out: list[Turn] = []
        try:
            lines = self.turns_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            with contextlib.suppress(ValueError, TypeError, KeyError, AttributeError):
                turn = _clean_turn(json.loads(line))
                if turn is not None:
                    out.append(turn)
        return out

    def turn(self, turn_id: str) -> Turn | None:
        return next((t for t in self.turns() if t.id == turn_id), None)

    def restore(self, turn_id: str, path: str | None = None) -> Turn | None:
        """Put files back as they were before a turn: changed ones get their old content, new ones are removed,
        removed ones come back. Only stored (text) contents can be put back. The undo is itself a turn."""
        turn = self.turn(turn_id)
        if turn is None:
            raise KeyError(turn_id)
        chosen = [c for c in turn.changes if path is None or c.path == path]
        if not chosen:
            raise KeyError(path or turn_id)
        started = self.begin()
        for change in chosen:
            target = self.root / change.path
            if change.status == "added":
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                continue
            data = self.content(change.before)
            if data is None:
                raise ValueError(f"Isi lama {change.path} tidak tersimpan (biner atau lebih dari 1 MB), jadi tidak bisa dikembalikan.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        label = f"Kembalikan {path}" if path else f"Kembalikan giliran: {turn.prompt}"
        return self.end(started, label, turn.session, by="restore")

    def changed_files(self, since: float = 0.0) -> dict[str, str]:
        """Path -> last status, for files changed by turns that ended after ``since``."""
        out: dict[str, str] = {}
        for turn in self.turns():
            if turn.ended > since:
                for change in turn.changes:
                    out[change.path] = change.status
        return out
