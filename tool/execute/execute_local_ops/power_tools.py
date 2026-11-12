"""Bulk file tools: find, read many, copy, replace across files, zip, and media conversion.

Same rules as ``local_ops_tool``: every path is resolved inside the workspace,
nothing goes through a shell, output is bounded, and the agent's private state
(``.agent_memory``, ``.vault``) is never read, copied, or written.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from tool.execute.execute_local_ops.backup import backup_file
from tool.execute.execute_local_ops.local_ops_tool import (
    _atomic_write_text,
    _error,
    _ok,
    _protected,
    _workspace,
)
from tool.execute.execute_local_ops.path_guard import PathGuardError, is_binary_file, resolve_inside
from utils.shared_files import PRIVATE_DIRS

# Never walked into: tool caches, dependencies, and the agent's own state.
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", *PRIVATE_DIRS})
_MAX_WALK_ENTRIES = 200_000

_MAX_FIND_RESULTS = 1000
_MAX_READ_FILES = 20
_MAX_CHARS_PER_FILE = 100_000
_MAX_TOTAL_CHARS = 120_000

_MAX_COPY_FILES = 10_000
_MAX_COPY_BYTES = 1024 * 1024 * 1024

_MAX_REPLACE_FILE_BYTES = 2 * 1024 * 1024
_MAX_REPLACE_FILES = 1000
_MAX_REPLACE_SCAN = 5000
_SAMPLE_LINES = 3

_MAX_ARCHIVE_ENTRIES = 10_000
_MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
# Already compressed: deflating them again only costs time.
_STORED_SUFFIXES = frozenset({
    ".zip", ".gz", ".xz", ".bz2", ".7z", ".mp4", ".webm", ".mov", ".mkv", ".mp3", ".m4a",
    ".ogg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
})

_PRESET_SUFFIX = {"mp4": ".mp4", "webm": ".webm", "gif": ".gif", "webp": ".webp", "poster": ".png"}
# Media only. A playlist or concat list could make ffmpeg read files outside the workspace.
_MEDIA_INPUTS = frozenset({
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".mpg", ".mpeg", ".ts",
    ".gif", ".webp", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff",
    ".mp3", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".flac",
})
_MAX_MEDIA_TIMEOUT = 3600


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _rel(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix() or "."


def _private(workspace: Path, *targets: Path) -> dict[str, Any] | None:
    """Refuse reading the agent's memory: it is not the user's file to hand around."""
    for target in targets:
        parts = target.relative_to(workspace).parts if target.is_relative_to(workspace) else target.parts
        hidden = next((p for p in parts if p in PRIVATE_DIRS), None)
        if hidden:
            return _error(f"Reading inside {hidden} is not allowed.")
    return None


def _as_list(value: Any) -> list[str]:
    """Models send a list, a JSON list in a string, or a comma separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except ValueError:
                value = text.strip("[]").split(",")
        else:
            value = re.split(r"[,\n]", text)
    if not isinstance(value, list | tuple):
        value = [value]
    return [str(item).strip().strip("'\"") for item in value if str(item).strip()]


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Glob to regex where ``*`` stays within one folder and ``**`` crosses folders."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and "]" in pattern[i + 1 :]:
            end = pattern.index("]", i + 1)
            body = pattern[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _glob_matcher(pattern: str) -> Any:
    """A pattern without a slash matches the name anywhere, like ``rglob``."""
    pattern = (pattern or "*").strip().lstrip("./") or "*"
    compiled = _glob_regex(pattern)
    if "/" not in pattern:
        return lambda rel: compiled.match(rel.rsplit("/", 1)[-1]) is not None
    return lambda rel: compiled.match(rel) is not None


def _walk(root: Path, skip: frozenset[str] = _SKIP_DIRS) -> Iterator[tuple[Path, bool]]:
    """Yield (path, is_dir) under root in a stable order. Symlinks are never followed or listed."""
    seen = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(current)
        kept = []
        for name in sorted(dirnames):
            if name in skip or (base / name).is_symlink():
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in kept:
            seen += 1
            yield base / name, True
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink():
                continue
            seen += 1
            yield path, False
        if seen >= _MAX_WALK_ENTRIES:
            return


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# file_find
# --------------------------------------------------------------------------- #
def _file_find_sync(
    workspace: Path,
    pattern: str,
    path: str,
    name_contains: str,
    kind: str,
    min_size: int | None,
    max_size: int | None,
    modified_within_minutes: float | None,
    max_results: int,
) -> dict[str, Any]:
    try:
        root = resolve_inside(workspace, path or ".", must_exist=True)
    except PathGuardError as exc:
        return _error(str(exc))
    refused = _private(workspace, root)
    if refused:
        return refused
    if not root.is_dir():
        return _error("path is not a directory")
    kind = (kind or "file").lower()
    if kind not in {"file", "dir", "any"}:
        return _error("kind must be file, dir or any")

    matches = _glob_matcher(pattern)
    needle = (name_contains or "").lower()
    newer_than = time.time() - float(modified_within_minutes) * 60 if modified_within_minutes else None
    sized = min_size is not None or max_size is not None

    found: list[tuple[float, dict[str, Any]]] = []
    scanned = 0
    for entry, is_dir in _walk(root):
        scanned += 1
        if (is_dir and kind == "file") or (not is_dir and kind == "dir") or (is_dir and sized):
            continue
        rel = _rel(workspace, entry)
        if not matches(_rel(root, entry)) or (needle and needle not in entry.name.lower()):
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        size = 0 if is_dir else st.st_size
        if min_size is not None and size < int(min_size):
            continue
        if max_size is not None and size > int(max_size):
            continue
        if newer_than is not None and st.st_mtime < newer_than:
            continue
        found.append((st.st_mtime, {
            "path": rel,
            "type": "dir" if is_dir else "file",
            "size": size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(timespec="seconds"),
        }))

    found.sort(key=lambda item: (-item[0], item[1]["path"]))
    results = [item for _, item in found[:max_results]]
    return _ok(
        root=_rel(workspace, root),
        pattern=pattern,
        count=len(results),
        total_matched=len(found),
        truncated=len(found) > max_results or scanned >= _MAX_WALK_ENTRIES,
        results=results,
    )


async def file_find(
    pattern: str = "*",
    path: str = ".",
    name_contains: str = "",
    kind: str = "file",
    min_size: int | None = None,
    max_size: int | None = None,
    modified_within_minutes: float | None = None,
    max_results: int = 200,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(
        _file_find_sync, workspace, pattern, path, name_contains, kind, min_size, max_size,
        modified_within_minutes, _clamp(max_results, 1, _MAX_FIND_RESULTS, 200),
    )


# --------------------------------------------------------------------------- #
# file_read_many
# --------------------------------------------------------------------------- #
def _read_one(workspace: Path, raw: str, cap: int) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, raw, must_exist=True)
    except PathGuardError as exc:
        return {"path": raw, "ok": False, "error": str(exc)}
    refused = _private(workspace, target)
    if refused:
        return {"path": raw, "ok": False, "error": refused["error"]}
    rel = _rel(workspace, target)
    if not target.is_file():
        return {"path": rel, "ok": False, "error": "not a file"}
    if is_binary_file(target):
        return {"path": rel, "ok": False, "skipped": "binary", "size": target.stat().st_size}
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(cap + 1)
        size = target.stat().st_size
    except OSError as exc:
        return {"path": rel, "ok": False, "error": f"read failed: {exc}"}
    truncated = len(text) > cap
    return {"path": rel, "ok": True, "size": size, "truncated": truncated, "content": text[:cap]}


def _file_read_many_sync(workspace: Path, paths: list[str], per_file: int, total: int) -> dict[str, Any]:
    if not paths:
        return _error("paths must list at least one file")
    files: list[dict[str, Any]] = []
    used = 0
    for raw in paths[:_MAX_READ_FILES]:
        left = total - used
        if left <= 0:
            files.append({"path": raw, "ok": False, "skipped": "total character limit reached"})
            continue
        item = _read_one(workspace, raw, min(per_file, left))
        used += len(item.get("content", ""))
        files.append(item)
    payload = _ok(files=files, read=sum(1 for f in files if f.get("ok")), total_chars=used)
    if len(paths) > _MAX_READ_FILES:
        payload["not_read"] = paths[_MAX_READ_FILES:]
        payload["note"] = f"Only the first {_MAX_READ_FILES} paths are read per call."
    if any(f.get("truncated") for f in files):
        payload["hint"] = "Some files were cut off; read the rest with read_file and a line range."
    return payload


async def file_read_many(
    paths: list[str],
    max_chars_per_file: int = 20_000,
    max_total_chars: int = _MAX_TOTAL_CHARS,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(
        _file_read_many_sync, workspace, _as_list(paths),
        _clamp(max_chars_per_file, 1, _MAX_CHARS_PER_FILE, 20_000),
        _clamp(max_total_chars, 1, _MAX_TOTAL_CHARS, _MAX_TOTAL_CHARS),
    )


# --------------------------------------------------------------------------- #
# file_copy
# --------------------------------------------------------------------------- #
def _copy_tree(workspace: Path, source: Path, target: Path, overwrite: bool) -> dict[str, Any]:
    plan: list[tuple[Path, Path]] = []
    dirs: list[Path] = [target]
    size = 0
    skipped: list[str] = []
    conflicts: list[str] = []
    for entry, is_dir in _walk(source, frozenset({".git", *PRIVATE_DIRS})):
        dest = target / entry.relative_to(source)
        if is_dir:
            dirs.append(dest)
            continue
        if dest.exists() and (dest.is_dir() or not overwrite):
            conflicts.append(_rel(workspace, dest))
            continue
        plan.append((entry, dest))
        size += entry.stat().st_size
        if len(plan) > _MAX_COPY_FILES or size > _MAX_COPY_BYTES:
            return _error(f"too much to copy: the limit is {_MAX_COPY_FILES} files and "
                          f"{_MAX_COPY_BYTES // (1024 * 1024)} MB")
    for name in sorted(os.listdir(source)):
        if name in {".git", *PRIVATE_DIRS} or (source / name).is_symlink():
            skipped.append(name)
    if conflicts:
        return _error("destination files already exist; pass overwrite=true to replace them",
                      conflicts=conflicts[:20])
    for folder in dirs:
        folder.mkdir(parents=True, exist_ok=True)
    for src, dest in plan:
        shutil.copy2(src, dest)
    return _ok(
        source=_rel(workspace, source),
        destination=_rel(workspace, target),
        files_copied=len(plan),
        bytes_copied=size,
        skipped=skipped,
    )


def _file_copy_sync(workspace: Path, src: str, dst: str, overwrite: bool) -> dict[str, Any]:
    try:
        source = resolve_inside(workspace, src, must_exist=True)
        target = resolve_inside(workspace, dst, must_exist=False)
    except PathGuardError as exc:
        return _error(str(exc))
    refused = _private(workspace, source) or _protected(workspace, target)
    if refused:
        return refused

    try:
        if source.is_dir():
            if target == source or target.is_relative_to(source):
                return _error("cannot copy a folder into itself")
            if target.exists() and not target.is_dir():
                return _error("destination is a file", destination=_rel(workspace, target))
            if target.exists() and not overwrite:
                return _error("destination already exists; pass overwrite=true to merge into it",
                              destination=_rel(workspace, target))
            return _copy_tree(workspace, source, target, overwrite)

        if target.is_dir():
            target = target / source.name
            refused = _protected(workspace, target)
            if refused:
                return refused
        if target == source:
            return _error("source and destination are the same file")
        if target.exists() and target.is_dir():
            return _error("destination is a folder", destination=_rel(workspace, target))
        backup: str | None = None
        if target.exists():
            if not overwrite:
                return _error("destination already exists; pass overwrite=true to replace it",
                              destination=_rel(workspace, target))
            bp = backup_file(workspace, target)
            backup = _rel(workspace, bp) if bp else None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        return _error(f"copy failed: {exc}")
    return _ok(
        source=_rel(workspace, source),
        destination=_rel(workspace, target),
        files_copied=1,
        bytes_copied=target.stat().st_size,
        backup=backup,
    )


async def file_copy(
    source: str, destination: str, overwrite: bool = False, workspace_path: str | None = None
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_file_copy_sync, workspace, source, destination, bool(overwrite))


# --------------------------------------------------------------------------- #
# file_replace_all
# --------------------------------------------------------------------------- #
def _file_replace_all_sync(
    workspace: Path,
    find: str,
    replace: str,
    glob: str,
    path: str,
    use_regex: bool,
    case_sensitive: bool,
    dry_run: bool,
    max_files: int,
) -> dict[str, Any]:
    if not find:
        return _error("find must not be empty")
    if not glob:
        return _error("glob must not be empty, e.g. '**/*.py'")
    try:
        root = resolve_inside(workspace, path or ".", must_exist=True)
    except PathGuardError as exc:
        return _error(str(exc))
    refused = _private(workspace, root) or _protected(workspace, root)
    if refused:
        return refused
    if not root.is_dir():
        return _error("path is not a directory")

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(find if use_regex else re.escape(find), flags)
    except re.error as exc:
        return _error(f"invalid regex: {exc}")

    def substitute(text: str) -> tuple[str, int]:
        # Literal mode must not read \1 or \n in the replacement as regex escapes.
        if use_regex:
            return compiled.subn(replace, text)
        return compiled.subn(lambda _m: replace, text)

    matches = _glob_matcher(glob)
    changed: list[dict[str, Any]] = []
    skipped = {"binary": 0, "large": 0, "not_utf8": 0}
    scanned = 0
    truncated = False
    for entry, is_dir in _walk(root):
        if is_dir or not matches(_rel(root, entry)):
            continue
        if scanned >= _MAX_REPLACE_SCAN:
            truncated = True
            break
        scanned += 1
        try:
            if entry.stat().st_size > _MAX_REPLACE_FILE_BYTES:
                skipped["large"] += 1
                continue
            if is_binary_file(entry):
                skipped["binary"] += 1
                continue
            # newline="" keeps CRLF files as they are.
            with entry.open("r", encoding="utf-8", newline="") as fh:
                original = fh.read()
        except UnicodeDecodeError:
            skipped["not_utf8"] += 1
            continue
        except OSError:
            continue
        try:
            updated, count = substitute(original)
        except re.error as exc:
            return _error(f"bad replacement: {exc}")
        if not count:
            continue
        if len(changed) >= max_files:
            truncated = True
            break
        samples = []
        for lineno, line in enumerate(original.splitlines(), 1):
            if compiled.search(line):
                samples.append({"line": lineno, "before": line[:200], "after": substitute(line)[0][:200]})
                if len(samples) >= _SAMPLE_LINES:
                    break
        item: dict[str, Any] = {"path": _rel(workspace, entry), "matches": count, "samples": samples}
        if not dry_run:
            mode = entry.stat().st_mode
            bp = backup_file(workspace, entry)
            item["backup"] = _rel(workspace, bp) if bp else None
            try:
                _atomic_write_text(entry, updated)
                os.chmod(entry, stat.S_IMODE(mode))
            except OSError as exc:
                return _error(f"write failed for {item['path']}: {exc}", changed=changed)
        changed.append(item)

    payload = _ok(
        dry_run=dry_run,
        files_matched=len(changed),
        total_matches=sum(item["matches"] for item in changed),
        scanned_files=scanned,
        skipped=skipped,
        truncated=truncated,
        files=changed,
    )
    if dry_run and changed:
        payload["hint"] = "Nothing was changed. Call again with dry_run=false to apply."
    return payload


async def file_replace_all(
    find: str,
    replace: str,
    glob: str,
    path: str = ".",
    use_regex: bool = False,
    case_sensitive: bool = True,
    dry_run: bool = True,
    max_files: int = 200,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(
        _file_replace_all_sync, workspace, find, replace, glob, path, bool(use_regex),
        bool(case_sensitive), bool(dry_run), _clamp(max_files, 1, _MAX_REPLACE_FILES, 200),
    )


# --------------------------------------------------------------------------- #
# archive
# --------------------------------------------------------------------------- #
def _zip_sync(workspace: Path, paths: list[str], archive_path: str, overwrite: bool) -> dict[str, Any]:
    if not paths:
        return _error("paths must list what to put in the zip")
    try:
        target = resolve_inside(workspace, archive_path, must_exist=False)
        sources = [resolve_inside(workspace, raw, must_exist=True) for raw in paths]
    except PathGuardError as exc:
        return _error(str(exc))
    refused = _protected(workspace, target) or _private(workspace, *sources)
    if refused:
        return refused
    if target.suffix.lower() != ".zip":
        return _error("archive_path must end with .zip")
    if target.exists() and not overwrite:
        return _error("archive already exists; pass overwrite=true to replace it", archive=_rel(workspace, target))

    entries: dict[str, Path] = {}
    total = 0
    for source in sources:
        base = source.parent
        items = [(source, source.is_dir())] if source != workspace else []
        if source.is_dir():
            items.extend(_walk(source, frozenset({".git", *PRIVATE_DIRS})))
        for entry, is_dir in items:
            if entry == target:
                continue
            name = entry.relative_to(base if source != workspace else workspace).as_posix()
            name = f"{name}/" if is_dir else name
            if name in entries:
                continue
            entries[name] = entry
            total += 0 if is_dir else entry.stat().st_size
            if len(entries) > _MAX_ARCHIVE_ENTRIES or total > _MAX_ARCHIVE_BYTES:
                return _error(f"too much to zip: the limit is {_MAX_ARCHIVE_ENTRIES} entries and "
                              f"{_MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")

    temporary = target.with_suffix(".zip.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, entry in entries.items():
                if name.endswith("/"):
                    zf.writestr(zipfile.ZipInfo(name), b"")
                    continue
                stored = entry.suffix.lower() in _STORED_SUFFIXES
                zf.write(entry, name, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        return _error(f"zip failed: {exc}")
    files = sum(1 for name in entries if not name.endswith("/"))
    return _ok(action="zip", archive=_rel(workspace, target), files=files,
               bytes_in=total, size=target.stat().st_size)


def _entry_target(dest_root: Path, name: str) -> Path | None:
    """Where a zip entry would land, or None when its name tries to leave the destination."""
    name = name.replace("\\", "/")
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or ".." in pure.parts or re.match(r"^[A-Za-z]:", name):
        return None
    target = (dest_root / pure).resolve()
    return target if target.is_relative_to(dest_root) else None


def _unzip_sync(workspace: Path, archive_path: str, destination: str, overwrite: bool) -> dict[str, Any]:
    try:
        source = resolve_inside(workspace, archive_path, must_exist=True)
        dest_root = resolve_inside(workspace, destination or ".", must_exist=False)
    except PathGuardError as exc:
        return _error(str(exc))
    refused = _private(workspace, source) or _protected(workspace, dest_root)
    if refused:
        return refused
    if not source.is_file():
        return _error("archive_path is not a file")
    if dest_root.exists() and not dest_root.is_dir():
        return _error("destination is a file")

    try:
        zf = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        return _error(f"not a readable zip file: {exc}")
    with zf:
        infos = zf.infolist()
        if len(infos) > _MAX_ARCHIVE_ENTRIES:
            return _error(f"zip has {len(infos)} entries; the limit is {_MAX_ARCHIVE_ENTRIES}")
        declared = sum(info.file_size for info in infos)
        if declared > _MAX_ARCHIVE_BYTES:
            return _error(f"zip unpacks to {declared // (1024 * 1024)} MB; "
                          f"the limit is {_MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")

        # Check every entry first, so a bad archive writes nothing at all.
        plan: list[tuple[zipfile.ZipInfo, Path]] = []
        conflicts: list[str] = []
        for info in infos:
            if stat.S_ISLNK(info.external_attr >> 16):
                return _error(f"zip contains a symlink entry, refusing it: {info.filename}")
            target = _entry_target(dest_root, info.filename)
            if target is None:
                return _error(f"zip entry escapes the destination, refusing the archive: {info.filename}")
            refused = _protected(workspace, target)
            if refused:
                return _error(f"zip entry writes into protected state, refusing the archive: {info.filename}")
            if not info.is_dir() and target.exists() and (target.is_dir() or not overwrite):
                conflicts.append(_rel(workspace, target))
            plan.append((info, target))
        if conflicts:
            return _error("files already exist; pass overwrite=true to replace them", conflicts=conflicts[:20])

        written: list[Path] = []
        total = 0
        try:
            for info, target in plan:
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    written.append(target)
                    # The sizes in the header can lie; count what really comes out.
                    while chunk := src.read(1024 * 1024):
                        total += len(chunk)
                        if total > _MAX_ARCHIVE_BYTES:
                            raise ValueError("zip unpacks to more than its header says; stopped")
                        out.write(chunk)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            for path in written:
                path.unlink(missing_ok=True)
            return _error(f"unzip failed: {exc}")

    return _ok(action="unzip", archive=_rel(workspace, source), destination=_rel(workspace, dest_root),
               files=len(written), bytes_out=total)


async def archive(
    action: str,
    archive_path: str,
    paths: list[str] | None = None,
    destination: str = ".",
    overwrite: bool = False,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    act = (action or "").strip().lower()
    if act == "zip":
        return await asyncio.to_thread(_zip_sync, workspace, _as_list(paths), archive_path, bool(overwrite))
    if act == "unzip":
        return await asyncio.to_thread(_unzip_sync, workspace, archive_path, destination, bool(overwrite))
    return _error("action must be zip or unzip")


# --------------------------------------------------------------------------- #
# media_convert
# --------------------------------------------------------------------------- #
def _ffmpeg_args(
    preset: str, source: Path, output: Path, fps: int | None, width: int | None,
    start: float | None, duration: float | None, at_second: float, overwrite: bool,
) -> list[str]:
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y" if overwrite else "-n"]
    if preset == "poster":
        args += ["-ss", f"{at_second:g}", "-i", str(source), "-frames:v", "1", "-update", "1"]
        if width:
            args += ["-vf", f"scale={width}:-2"]
        return [*args, str(output)]

    if start:
        args += ["-ss", f"{start:g}"]
    args += ["-i", str(source)]
    if duration:
        args += ["-t", f"{duration:g}"]
    rate = f"fps={fps}," if fps else ""
    if preset in {"mp4", "webm"}:
        # H.264 in yuv420p needs even dimensions.
        scale = f"scale={width}:-2" if width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        args += ["-vf", rate + scale]
        if preset == "mp4":
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
        else:
            args += ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-row-mt", "1", "-deadline", "good",
                     "-cpu-used", "4", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-b:a", "96k"]
        return [*args, str(output)]

    fps = fps or 12
    size = width or 480
    scale = f"fps={fps},scale='min({size},iw)':-1:flags=lanczos"
    if preset == "gif":
        graph = f"{scale},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
        args += ["-vf", graph, "-loop", "0", "-an"]
    else:
        args += ["-vf", scale, "-c:v", "libwebp", "-lossless", "0", "-q:v", "75", "-loop", "0", "-an"]
    return [*args, str(output)]


def _media_convert_sync(
    workspace: Path, source: str, output: str, preset: str, fps: int | None, width: int | None,
    start: float | None, duration: float | None, at_second: float, overwrite: bool, timeout: int,
) -> dict[str, Any]:
    from utils.toolkit import ffmpeg_exe, install_hint

    ffmpeg = ffmpeg_exe()      # the system's, else the copy bundled with imageio-ffmpeg
    if not ffmpeg:
        return _error(f"ffmpeg is not installed. Install it with: {install_hint('ffmpeg')}")
    preset = (preset or "").strip().lower()
    if preset not in _PRESET_SUFFIX:
        return _error(f"preset must be one of {', '.join(_PRESET_SUFFIX)}")
    try:
        src = resolve_inside(workspace, source, must_exist=True)
        out = resolve_inside(workspace, output, must_exist=False)
    except PathGuardError as exc:
        return _error(str(exc))
    refused = _private(workspace, src) or _protected(workspace, out)
    if refused:
        return refused
    if not src.is_file():
        return _error("source is not a file")
    if src.suffix.lower() not in _MEDIA_INPUTS:
        return _error(f"source must be a video, image or audio file, not {src.suffix or 'no extension'}")
    if out.suffix.lower() != _PRESET_SUFFIX[preset]:
        return _error(f"the {preset} preset writes {_PRESET_SUFFIX[preset]} files; change the output name")
    if out == src:
        return _error("output must differ from source")
    if out.exists() and not overwrite:
        return _error("output already exists; pass overwrite=true to replace it", output=_rel(workspace, out))
    for name, value, low, high in (("fps", fps, 1, 60), ("width", width, 16, 3840)):
        if value is not None and not low <= int(value) <= high:
            return _error(f"{name} must be between {low} and {high}")
    if any(v is not None and float(v) < 0 for v in (start, duration, at_second)):
        return _error("times must not be negative")

    args = _ffmpeg_args(
        preset, src, out, int(fps) if fps else None, int(width) if width else None,
        float(start) if start else None, float(duration) if duration else None,
        float(at_second or 0), overwrite,
    )
    args[0] = ffmpeg
    out.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        out.unlink(missing_ok=True)
        return _error(f"ffmpeg took longer than {timeout}s and was stopped")
    except OSError as exc:
        return _error(f"ffmpeg failed to start: {exc}")
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        return _error(f"ffmpeg exited with {proc.returncode}", stderr=proc.stderr.strip()[-1500:])
    return _ok(
        preset=preset,
        source=_rel(workspace, src),
        output=_rel(workspace, out),
        size=out.stat().st_size,
        seconds=round(time.monotonic() - began, 2),
        hint="Call share_file with this output to show it to the user.",
    )


async def media_convert(
    source: str,
    output: str,
    preset: str,
    fps: int | None = None,
    width: int | None = None,
    start: float | None = None,
    duration: float | None = None,
    at_second: float = 0.0,
    overwrite: bool = False,
    timeout: int = 600,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    try:
        return await asyncio.to_thread(
            _media_convert_sync, workspace, source, output, preset, fps, width, start, duration,
            at_second, bool(overwrite), _clamp(timeout, 1, _MAX_MEDIA_TIMEOUT, 600),
        )
    except (TypeError, ValueError) as exc:
        return _error(f"invalid option: {exc}")


__all__ = ["archive", "file_copy", "file_find", "file_read_many", "file_replace_all", "media_convert"]
