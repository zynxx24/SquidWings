"""
security.integrity — File hash manifests to detect tampering.

The agent writes files; something must confirm they were not altered
afterwards. This module produces a manifest of (path -> sha256) and can
verify a tree against it in a single streaming pass.

Use cases:
    * Detect out-of-band edits to generated code.
    * Detect prompt-injection payloads planted in files the agent trusts.
    * Supply-chain check before executing a generated script.

Hashing is streamed in 1 MiB chunks so memory stays flat regardless of
file size. Symlinks are not followed (recorded as their target string) to
avoid escaping the scan root.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB

# Files that change constantly and carry no security value.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
)


@dataclass(slots=True)
class FileRecord:
    """One manifest entry."""

    path: str          # POSIX-style path relative to the manifest root
    sha256: str
    size: int
    mtime: float

    def to_dict(self) -> dict[str, object]:
        return {"sha256": self.sha256, "size": self.size, "mtime": self.mtime}

    @classmethod
    def from_dict(cls, path: str, data: dict[str, object]) -> FileRecord:
        raw_size = data.get("size", 0)
        size = int(raw_size) if isinstance(raw_size, (int, float, str)) else 0

        raw_mtime = data.get("mtime", 0.0)
        mtime = float(raw_mtime) if isinstance(raw_mtime, (int, float, str)) else 0.0

        return cls(
            path=path,
            sha256=str(data.get("sha256", "")),
            size=size,
            mtime=mtime,
        )


@dataclass
class Diff:
    """Result of verifying a tree against a manifest."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.added or self.removed or self.modified)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    def summary(self) -> str:
        if self.clean:
            return "integrity OK"
        parts = []
        if self.modified:
            parts.append(f"{len(self.modified)} modified")
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        return "integrity drift: " + ", ".join(parts)

    def to_dict(self) -> dict[str, list[str]]:
        return {"added": self.added, "removed": self.removed, "modified": self.modified}


def hash_file(path: str | Path) -> str:
    """Stream a file and return its hex sha256."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _is_excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    import fnmatch

    parts = rel.split("/")
    for pat in patterns:
        if fnmatch.fnmatchcase(rel, pat):
            return True
        if any(fnmatch.fnmatchcase(part, pat) for part in parts):
            return True
    return False


def iter_files(
    root: str | Path,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
    max_files: int = 50_000,
) -> Iterator[Path]:
    """Yield regular files under `root`, skipping excluded patterns."""
    base = Path(root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        rel_dir = Path(dirpath).relative_to(base).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        # Prune excluded directories in-place for speed.
        dirnames[:] = [
            d for d in dirnames if not _is_excluded(prefix + d, excludes)
        ]
        for name in filenames:
            rel = prefix + name
            if _is_excluded(rel, excludes):
                continue
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            yield full
            count += 1
            if count >= max_files:
                return


class Manifest:
    """A set of file hashes rooted at a directory."""

    __slots__ = ("root", "records", "created_at", "version")

    VERSION = 1

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.records: dict[str, FileRecord] = {}
        self.created_at: float = time.time()
        self.version: int = self.VERSION

    # ── building ────────────────────────────────────────────────────────
    @classmethod
    def build(
        cls,
        root: str | Path,
        excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
        max_files: int = 50_000,
    ) -> Manifest:
        """Hash every file under `root`."""
        m = cls(root)
        for full in iter_files(m.root, excludes, max_files):
            try:
                st = full.stat()
                rel = full.relative_to(m.root).as_posix()
                m.records[rel] = FileRecord(
                    path=rel,
                    sha256=hash_file(full),
                    size=st.st_size,
                    mtime=st.st_mtime,
                )
            except (OSError, ValueError):
                continue
        return m

    # ── verification ────────────────────────────────────────────────────
    def verify(
        self,
        root: str | Path | None = None,
        excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
        check_mtime: bool = False,
    ) -> Diff:
        """Compare the manifest against the current filesystem state.

        Args:
            root: Override root (defaults to the manifest root).
            excludes: Patterns to skip.
            check_mtime: When False (default), a file whose size and mtime
                match is trusted without re-hashing. Set True for a full
                content verification.
        """
        base = Path(root).resolve() if root else self.root
        diff = Diff()
        seen: set[str] = set()

        for full in iter_files(base, excludes, max_files=len(self.records) * 2 + 1000):
            try:
                rel = full.relative_to(base).as_posix()
            except ValueError:
                continue
            seen.add(rel)
            record = self.records.get(rel)
            if record is None:
                diff.added.append(rel)
                continue
            try:
                st = full.stat()
            except OSError:
                diff.removed.append(rel)
                continue
            if not check_mtime and st.st_size == record.size and st.st_mtime == record.mtime:
                continue
            try:
                if hash_file(full) != record.sha256:
                    diff.modified.append(rel)
            except OSError:
                diff.removed.append(rel)

        diff.removed.extend(p for p in self.records if p not in seen)
        diff.added.sort()
        diff.removed.sort()
        diff.modified.sort()
        return diff

    def verify_file(self, path: str | Path) -> bool:
        """Check a single file against the manifest."""
        p = Path(path)
        try:
            rel = p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return False
        record = self.records.get(rel)
        if record is None:
            return False
        try:
            return hash_file(p) == record.sha256
        except OSError:
            return False

    # ── persistence ─────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "root": str(self.root),
            "created_at": self.created_at,
            "files": {p: r.to_dict() for p, r in self.records.items()},
        }

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(out)  # atomic
        return out

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        m = cls(data.get("root", "."))
        m.created_at = float(data.get("created_at", 0.0) or 0.0)
        m.version = int(data.get("version", cls.VERSION))
        for rel, rec in (data.get("files") or {}).items():
            m.records[rel] = FileRecord.from_dict(rel, rec)
        return m

    def __len__(self) -> int:
        return len(self.records)

    def __repr__(self) -> str:
        return f"Manifest(root={self.root}, files={len(self.records)})"


def snapshot(root: str | Path, out: str | Path) -> Manifest:
    """Build and persist a manifest in one call."""
    m = Manifest.build(root)
    m.save(out)
    return m


def verify(root: str | Path, manifest_path: str | Path, check_mtime: bool = False) -> Diff:
    """Load a manifest and verify the tree."""
    return Manifest.load(manifest_path).verify(root, check_mtime=check_mtime)
