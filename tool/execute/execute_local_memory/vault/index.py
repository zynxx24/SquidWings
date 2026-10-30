"""Incremental vault index (L3).

The index caches one entry per note so search and graph building never have
to re-read unchanged files. Invalidation is by ``(mtime_ns, size)`` — the
cheapest signal that survives editors which preserve mtime poorly.

The index is disposable: delete ``.squidwings/index.json`` and the next
call rebuilds it from the vault. The vault is always the source of truth.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault.schema import (
    NoteRecord,
    build_record,
    record_from_entry,
)
from utils.logger import get_logger

logger = get_logger(__name__)


class VaultIndex:
    """Incremental index over a vault directory."""

    def __init__(self, vault: str | Path, extra_excludes: Iterable[str] = ()) -> None:
        self.vault = Path(vault).expanduser()
        self.extra_excludes = tuple(extra_excludes)
        self._entries: dict[str, dict[str, Any]] = {}
        self._signatures: dict[str, tuple[int, int]] = {}
        self._loaded = False
        self.stats: dict[str, int] = {
            "scanned": 0, "reused": 0, "rebuilt": 0,
            "skipped": 0, "removed": 0, "errors": 0,
        }

    # -- paths --------------------------------------------------------
    @property
    def index_file(self) -> Path:
        return C.index_dir(self.vault) / C.INDEX_FILENAME

    def validate(self) -> None:
        if not self.vault.exists():
            raise FileNotFoundError(f"vault not found: {self.vault}")
        if not self.vault.is_dir():
            raise NotADirectoryError(f"vault is not a directory: {self.vault}")

    # -- persistence --------------------------------------------------
    def load(self) -> None:
        """Load the cached index. Corrupt or missing → empty, no raise."""
        self._loaded = True
        path = self.index_file
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("vault index unreadable, rebuilding: %s", exc)
            return
        if not isinstance(data, dict) or data.get("version") != C.INDEX_VERSION:
            logger.info("vault index version mismatch, rebuilding")
            return
        entries = data.get("notes")
        if not isinstance(entries, dict):
            return
        self._entries = entries
        self._signatures = {
            rel: (int(e.get("mtime_ns", 0)), int(e.get("size", 0)))
            for rel, e in entries.items()
        }

    def save(self) -> None:
        """Write the index atomically. Failure is logged, never raised."""
        path = self.index_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": C.INDEX_VERSION,
                "built_at": time.time(),
                "vault": str(self.vault),
                "notes": self._entries,
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(path)
        except OSError as exc:
            logger.warning("vault index save failed: %s", exc)

    # -- scanning -----------------------------------------------------
    def _iter_notes(self) -> Iterable[tuple[str, Path]]:
        for path in self.vault.rglob("*.md"):
            try:
                rel = path.relative_to(self.vault).as_posix()
            except ValueError:
                continue
            if C.is_excluded(rel, self.extra_excludes):
                self.stats["skipped"] += 1
                continue
            yield rel, path

    def _signature(self, path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def refresh(self, force: bool = False) -> dict[str, NoteRecord]:
        """Scan the vault, re-reading only changed files."""
        self.validate()
        if not self._loaded:
            self.load()
        if force:
            self._entries.clear()
            self._signatures.clear()

        for key in self.stats:
            self.stats[key] = 0

        seen: set[str] = set()
        for rel, path in self._iter_notes():
            seen.add(rel)
            self.stats["scanned"] += 1
            sig = self._signature(path)
            if sig is None:
                self.stats["errors"] += 1
                continue
            if not force and self._signatures.get(rel) == sig:
                self.stats["reused"] += 1
                continue
            if sig[1] > C.MAX_NOTE_BYTES:
                self.stats["skipped"] += 1
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.debug("vault index: cannot read %s: %s", rel, exc)
                self.stats["errors"] += 1
                continue
            record = build_record(rel, text, sig[1], sig[0] / 1e9)
            entry = record.to_index_entry()
            entry["mtime_ns"] = sig[0]
            self._entries[rel] = entry
            self._signatures[rel] = sig
            self.stats["rebuilt"] += 1

        for rel in list(self._entries):
            if rel not in seen:
                self._entries.pop(rel, None)
                self._signatures.pop(rel, None)
                self.stats["removed"] += 1

        if len(self._entries) > C.MAX_INDEX_ENTRIES:
            keep = sorted(
                self._entries.items(),
                key=lambda kv: kv[1].get("mtime", 0),
                reverse=True,
            )[: C.MAX_INDEX_ENTRIES]
            self._entries = dict(keep)
            self._signatures = {
                k: v for k, v in self._signatures.items() if k in self._entries
            }

        self.save()
        return self.records()

    # -- accessors ----------------------------------------------------
    def records(self) -> dict[str, NoteRecord]:
        return {rel: record_from_entry(rel, e) for rel, e in self._entries.items()}

    def record(self, rel_path: str) -> NoteRecord | None:
        entry = self._entries.get(rel_path)
        return record_from_entry(rel_path, entry) if entry else None

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, rel_path: object) -> bool:
        return rel_path in self._entries

    # -- search -------------------------------------------------------
    def search(
        self,
        query: str = "",
        *,
        tag: str | None = None,
        note_type: str | None = None,
        status: str | None = None,
        include_stale: bool = False,
        limit: int = 20,
    ) -> list[NoteRecord]:
        """Filter cached records. No file I/O — pure index scan."""
        needle = (query or "").casefold().strip()
        tag_needle = (tag or "").casefold().strip() or None
        type_needle = C.normalize_type(note_type) if note_type else None
        status_needle = C.normalize_status(status) if status else None

        hits: list[NoteRecord] = []
        for rel, entry in self._entries.items():
            rec = record_from_entry(rel, entry)
            if not include_stale and rec.is_stale:
                continue
            if status_needle and rec.status != status_needle:
                continue
            if type_needle and rec.note_type != type_needle:
                continue
            if tag_needle and tag_needle not in [t.casefold() for t in rec.tags]:
                continue
            if needle:
                haystack = " ".join([
                    rec.title, rec.summary, " ".join(rec.tags), rec.path,
                ]).casefold()
                if needle not in haystack:
                    continue
            hits.append(rec)

        hits.sort(key=lambda r: r.mtime, reverse=True)
        return hits[: max(1, limit)]

    # -- maintenance --------------------------------------------------
    def prune_orphan_files(self) -> int:
        """Remove index entries whose files no longer exist."""
        gone = [rel for rel in self._entries if not (self.vault / rel).exists()]
        for rel in gone:
            self._entries.pop(rel, None)
            self._signatures.pop(rel, None)
        if gone:
            self.save()
        return len(gone)


__all__ = ["VaultIndex"]
