"""Normalized note record (L3 → L2 boundary).

A :class:`NoteRecord` is everything the graph builder needs about one note
without carrying the body around: type, lifecycle status, confidence, tags,
explicit relations, and a short summary.

``build_record`` is deliberately tolerant. A note with broken frontmatter
still becomes a usable record instead of vanishing from the index, because
losing a note silently is worse than indexing it with defaults.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault import frontmatter as F


@dataclass
class NoteRecord:
    """One vault note, normalized."""

    path: str
    title: str
    note_type: str = C.TYPE_NOTE
    status: str = C.STATUS_ACTIVE
    confidence: str = C.CONFIDENCE_MEDIUM
    updated: str = ""
    superseded_by: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    relations: list[tuple[str, str]] = field(default_factory=list)
    summary: str = ""
    headings: list[str] = field(default_factory=list)
    size: int = 0
    mtime: float = 0.0
    frontmatter: dict[str, Any] = field(default_factory=dict)

    # -- derived ------------------------------------------------------
    @property
    def is_current(self) -> bool:
        return self.status == C.STATUS_ACTIVE

    @property
    def is_stale(self) -> bool:
        return self.status in C.STALE_STATUSES

    @property
    def needs_review(self) -> bool:
        return self.is_current and self.confidence == C.CONFIDENCE_LOW

    @property
    def degree_hint(self) -> int:
        """Explicit out-degree, used before the graph is built."""
        return len(self.links) + len(self.relations)

    def to_node(self) -> dict[str, Any]:
        """Serialize for ``graph.json``. No body — nodes stay small."""
        return {
            "id": self.path,
            "path": self.path,
            "title": self.title,
            "type": self.note_type,
            "status": self.status,
            "confidence": self.confidence,
            "updated": self.updated,
            "superseded_by": self.superseded_by,
            "tags": list(self.tags),
            "summary": self.summary,
            "size": self.size,
            "mtime": self.mtime,
        }

    def to_index_entry(self) -> dict[str, Any]:
        """Serialize for the incremental index. Includes relations."""
        entry = self.to_node()
        entry["links"] = list(self.links)
        entry["relations"] = [list(r) for r in self.relations]
        entry["headings"] = list(self.headings)
        return entry


def build_record(rel_path: str, text: str, size: int, mtime: float) -> NoteRecord:
    """Turn raw note text into a validated record. Never raises."""
    meta, body = F.parse_frontmatter(text or "")

    note_type = C.normalize_type(meta.get("type") or C.type_for_folder(rel_path))
    status = C.normalize_status(meta.get("status"))
    confidence = C.normalize_confidence(meta.get("confidence"))

    title = str(meta.get("title") or "").strip()
    if not title:
        title = F.first_heading(body)
    if not title:
        title = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    return NoteRecord(
        path=rel_path,
        title=title,
        note_type=note_type,
        status=status,
        confidence=confidence,
        updated=str(meta.get("updated") or "").strip(),
        superseded_by=str(meta.get("superseded_by") or "").strip(),
        tags=F.extract_tags(body, meta),
        links=F.extract_links(body),
        relations=F.extract_relations(meta),
        summary=F.extract_summary(body),
        headings=F.extract_headings(body),
        size=size,
        mtime=mtime,
        frontmatter=meta,
    )


def record_from_entry(rel_path: str, entry: dict[str, Any]) -> NoteRecord:
    """Rebuild a record from a cached index entry (no file read)."""
    return NoteRecord(
        path=rel_path,
        title=str(entry.get("title", "")),
        note_type=C.normalize_type(entry.get("type")),
        status=C.normalize_status(entry.get("status")),
        confidence=C.normalize_confidence(entry.get("confidence")),
        updated=str(entry.get("updated", "")),
        superseded_by=str(entry.get("superseded_by", "")),
        tags=list(entry.get("tags", [])),
        links=list(entry.get("links", [])),
        relations=[tuple(r) for r in entry.get("relations", [])],
        summary=str(entry.get("summary", "")),
        headings=list(entry.get("headings", [])),
        size=int(entry.get("size", 0)),
        mtime=float(entry.get("mtime", 0.0)),
    )


def make_record(
    rel_path: str,
    body: str,
    *,
    note_type: str | None = None,
    status: str = C.STATUS_ACTIVE,
    confidence: str = C.CONFIDENCE_MEDIUM,
    tags: Iterable[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[NoteRecord, str]:
    """Build a record for a note the agent is about to write.

    Returns ``(record, text)`` where ``text`` is exactly what should land on
    disk. The record is derived from that text, so memory and file can never
    drift apart.
    """
    meta: dict[str, Any] = dict(extra or {})
    meta["type"] = C.normalize_type(note_type or C.type_for_folder(rel_path))
    meta["status"] = C.normalize_status(status)
    meta["confidence"] = C.normalize_confidence(confidence)
    meta["updated"] = time.strftime("%Y-%m-%d")
    if tags:
        meta["tags"] = sorted({str(t).lstrip("#") for t in tags if str(t).strip()})

    header = F.serialize_frontmatter(meta)
    text = f"{header}\n{body.lstrip()}" if header else body
    return build_record(rel_path, text, len(text.encode("utf-8")), time.time()), text


def render_note(record: NoteRecord, body: str) -> str:
    """Serialize a record + body back to Markdown with frontmatter."""
    meta = dict(record.frontmatter)
    meta["type"] = record.note_type
    meta["status"] = record.status
    meta["confidence"] = record.confidence
    if record.updated:
        meta["updated"] = record.updated
    if record.superseded_by:
        meta["superseded_by"] = record.superseded_by
    if record.tags:
        meta["tags"] = list(record.tags)
    meta.pop("_unparsed", None)
    header = F.serialize_frontmatter(meta)
    return f"{header}\n{body.lstrip()}" if header else body


__all__ = [
    "NoteRecord",
    "build_record",
    "record_from_entry",
    "make_record",
    "render_note",
]
