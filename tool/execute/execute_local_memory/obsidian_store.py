"""Obsidian vault reader/writer with a lightweight JSON index.

Notes are Markdown files with optional YAML frontmatter. The index lives at
``<vault>/.squidwings_index.json`` and is rebuilt lazily when stale.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault import frontmatter as F
from tool.execute.execute_local_ops.path_guard import (
    PathGuardError,
    is_binary_file,
    resolve_inside,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Kept as a dot-file at the vault root: a bare ``index.json`` would show up
# as a note in Obsidian. The vault index in ``.squidwings/`` is a different,
# newer cache and must not be confused with this one.
_INDEX_FILENAME = ".squidwings_index.json"
_MAX_NOTE_BYTES = C.MAX_NOTE_BYTES
_MAX_INDEX_ENTRIES = C.MAX_INDEX_ENTRIES


@dataclass
class NoteMeta:
    path: str
    title: str
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    size: int = 0
    mtime: float = 0.0


# Frontmatter parsing is delegated to the single vault implementation so the
# store, the index, and the graph builder can never disagree about what a note
# means. These thin wrappers keep the historical private names working.
def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    return F.parse_frontmatter(text)


def _extract_tags(body: str, meta: dict[str, Any]) -> list[str]:
    return F.extract_tags(body, meta)


def _extract_links(body: str) -> list[str]:
    return F.extract_links(body)


def _note_title(path: Path, meta: dict[str, Any]) -> str:
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    heading = F.first_heading(meta.get("_body", "") if isinstance(meta, dict) else "")
    return heading or path.stem


class ObsidianStore:
    """Read/write/search an Obsidian vault with a cached index."""

    def __init__(self, vault_path: str, folder: str = "") -> None:
        self.vault = Path(vault_path).expanduser()
        self.folder = folder.strip("/")
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        if not self.vault.exists():
            raise PathGuardError(f"obsidian vault not found: {self.vault}")
        if not self.vault.is_dir():
            raise PathGuardError(f"obsidian vault is not a directory: {self.vault}")

    def _root(self) -> Path:
        self.validate()
        root = self.vault / self.folder if self.folder else self.vault
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _index_path(self) -> Path:
        return self.vault / _INDEX_FILENAME

    # ------------------------------------------------------------------ #
    # Index
    # ------------------------------------------------------------------ #
    def _load_index(self) -> dict[str, Any]:
        path = self._index_path()
        if not path.exists():
            return {"version": 1, "built_at": 0.0, "notes": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "notes" not in data:
                raise ValueError("invalid index shape")
            return data
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("obsidian: index corrupt, rebuilding: %s", exc)
            return {"version": 1, "built_at": 0.0, "notes": {}}

    def _save_index(self, index: dict[str, Any]) -> None:
        path = self._index_path()
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("obsidian: index save failed: %s", exc)

    def _scan_note(self, path: Path) -> NoteMeta | None:
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size > _MAX_NOTE_BYTES or is_binary_file(path):
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        meta, body = _parse_frontmatter(text)
        rel = str(path.relative_to(self.vault))
        return NoteMeta(
            path=rel,
            title=_note_title(path, meta),
            tags=_extract_tags(body, meta),
            links=_extract_links(body),
            size=size,
            mtime=path.stat().st_mtime,
        )

    def _rebuild_index(self) -> dict[str, Any]:
        self.validate()
        notes: dict[str, Any] = {}
        for path in self.vault.rglob("*.md"):
            if len(notes) >= _MAX_INDEX_ENTRIES:
                break
            if any(part.startswith(".") for part in path.relative_to(self.vault).parts):
                continue
            meta = self._scan_note(path)
            if meta is None:
                continue
            notes[meta.path] = {
                "title": meta.title,
                "tags": meta.tags,
                "links": meta.links,
                "size": meta.size,
                "mtime": meta.mtime,
            }
        index = {"version": 1, "built_at": time.time(), "notes": notes}
        self._save_index(index)
        return index

    def _ensure_index(self) -> dict[str, Any]:
        index = self._load_index()
        if not index.get("notes") or index.get("built_at", 0) == 0:
            return self._rebuild_index()
        return index

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        tag: str | None = None,
        limit: int = 20,
        include_body: bool = False,
    ) -> list[dict[str, Any]]:
        index = self._ensure_index()
        needle = query.casefold().strip()
        tag_needle = tag.casefold().strip() if tag else None
        results: list[dict[str, Any]] = []
        for rel, meta in index["notes"].items():
            if tag_needle and tag_needle not in [t.casefold() for t in meta.get("tags", [])]:
                continue
            title = str(meta.get("title", "")).casefold()
            tags = [t.casefold() for t in meta.get("tags", [])]
            matched = not needle or needle in title or any(needle in t for t in tags)
            snippet = ""
            if not matched and needle:
                try:
                    text = (self.vault / rel).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if needle in text.casefold():
                    matched = True
                    idx = text.casefold().find(needle)
                    snippet = text[max(0, idx - 60): idx + 140].replace("\n", " ").strip()
            if not matched:
                continue
            entry = {
                "path": rel,
                "title": meta.get("title", ""),
                "tags": meta.get("tags", []),
                "mtime": meta.get("mtime", 0),
            }
            if include_body and snippet:
                entry["snippet"] = snippet
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def read(self, rel_path: str) -> dict[str, Any]:
        target = resolve_inside(self.vault, rel_path, must_exist=True)
        if target.suffix.lower() != ".md":
            raise PathGuardError("only .md notes can be read")
        if target.stat().st_size > _MAX_NOTE_BYTES:
            raise PathGuardError("note too large")
        text = target.read_text(encoding="utf-8", errors="replace")
        meta, body = _parse_frontmatter(text)
        return {
            "path": str(target.relative_to(self.vault)),
            "frontmatter": meta,
            "body": body,
            "tags": _extract_tags(body, meta),
            "links": _extract_links(body),
        }

    def write(
        self,
        rel_path: str,
        body: str,
        frontmatter: dict[str, Any] | None = None,
        append: bool = False,
    ) -> dict[str, Any]:
        root = self._root()
        target = resolve_inside(root, rel_path, must_exist=False)
        if target.suffix.lower() != ".md":
            target = target.with_suffix(".md")
        target.parent.mkdir(parents=True, exist_ok=True)

        if append and target.exists():
            existing = target.read_text(encoding="utf-8", errors="replace")
            new_text = existing.rstrip() + "\n\n" + body
        else:
            fm = frontmatter or {}
            lines = ["---"]
            for key, value in fm.items():
                if isinstance(value, list):
                    lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
                else:
                    lines.append(f"{key}: {value}")
            lines.append("---")
            lines.append("")
            new_text = "\n".join(lines) + body

        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(target)

        index = self._load_index()
        meta = self._scan_note(target)
        if meta is not None:
            index["notes"][meta.path] = {
                "title": meta.title,
                "tags": meta.tags,
                "links": meta.links,
                "size": meta.size,
                "mtime": meta.mtime,
            }
            self._save_index(index)

        return {
            "path": str(target.relative_to(self.vault)),
            "bytes": len(new_text.encode("utf-8")),
            "appended": append,
        }

    def list_notes(self, folder: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        index = self._ensure_index()
        prefix = (folder or self.folder).strip("/")
        out: list[dict[str, Any]] = []
        for rel, meta in index["notes"].items():
            if prefix and not rel.startswith(prefix + "/") and rel != prefix:
                continue
            out.append({"path": rel, "title": meta.get("title", ""), "tags": meta.get("tags", [])})
            if len(out) >= limit:
                break
        return out

    def stats(self) -> dict[str, Any]:
        index = self._ensure_index()
        notes = index.get("notes", {})
        total_size = sum(int(m.get("size", 0)) for m in notes.values())
        return {
            "vault": str(self.vault),
            "folder": self.folder,
            "note_count": len(notes),
            "total_bytes": total_size,
            "index_built_at": index.get("built_at", 0),
        }


__all__ = ["ObsidianStore", "NoteMeta"]
