"""Deterministic local operations — no LLM, no network.

These tools handle work that does not need a model: filesystem inspection,
text search, precise patching, git summaries, JSON extraction, and simple
text transforms. Every executor accepts keyword arguments only, matching
``ToolRegistry.execute()``.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.settings import get_settings
from tool.execute.execute_local_ops.backup import backup_file
from tool.execute.execute_local_ops.path_guard import (
    PathGuardError,
    is_binary_file,
    resolve_inside,
    resolve_workspace,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per file
_MAX_SEARCH_HITS = 500
_MAX_SEARCH_FILES = 5000
_MAX_READ_BYTES = 5 * 1024 * 1024  # 5 MB for file_stat hashing


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _workspace(workspace_path: str | None) -> Path:
    return resolve_workspace(workspace_path, get_settings().project_root)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return payload


def _protected(workspace: Path, *targets: Path) -> dict[str, Any] | None:
    """Refuse writes to .git and memory state.

    File tools run on the host, not in the sandbox. A hook or git config written
    here would run with your full permissions the next time you use git, and
    edited memory would plant instructions in future prompts.
    """
    for target in targets:
        parts = target.relative_to(workspace).parts if target.is_relative_to(workspace) else target.parts
        if ".git" in parts or ".agent_memory" in parts or ".vault" in parts:
            return _error(f"Writing inside {target.name if len(parts) == 1 else parts[0]} is not allowed.")
    return None


def _ok(**payload: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"ok": True}
    base.update(payload)
    return base


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _iter_files(root: Path, glob: str | None) -> list[Path]:
    if glob:
        return [p for p in root.rglob(glob) if p.is_file()]
    return [p for p in root.rglob("*") if p.is_file()]


# --------------------------------------------------------------------------- #
# file_search
# --------------------------------------------------------------------------- #
def _file_search_sync(
    workspace: Path,
    pattern: str,
    glob: str | None,
    use_regex: bool,
    case_sensitive: bool,
    max_hits: int,
    max_files: int,
) -> dict[str, Any]:
    if not pattern:
        return _error("pattern must not be empty")

    flags = 0 if case_sensitive else re.IGNORECASE
    if use_regex:
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            return _error(f"invalid regex: {exc}")
    else:
        compiled = re.compile(re.escape(pattern), flags)

    hits: list[dict[str, Any]] = []
    scanned = 0
    skipped_binary = 0
    skipped_large = 0

    for path in _iter_files(workspace, glob):
        if scanned >= max_files:
            break
        if len(hits) >= max_hits:
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_SEARCH_FILE_BYTES:
            skipped_large += 1
            continue
        if is_binary_file(path):
            skipped_binary += 1
            continue
        scanned += 1
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    if compiled.search(line):
                        hits.append(
                            {
                                "file": str(path.relative_to(workspace)),
                                "line": lineno,
                                "text": line.rstrip("\n")[:400],
                            }
                        )
                        if len(hits) >= max_hits:
                            break
        except OSError as exc:
            logger.debug("local_ops: read failed %s: %s", path, exc)

    return _ok(
        pattern=pattern,
        regex=use_regex,
        case_sensitive=case_sensitive,
        scanned_files=scanned,
        skipped_binary=skipped_binary,
        skipped_large=skipped_large,
        hit_count=len(hits),
        truncated=len(hits) >= max_hits,
        hits=hits,
    )


async def file_search(
    pattern: str,
    glob: str | None = None,
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_hits: int = 100,
    max_files: int = 2000,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    max_hits = max(1, min(_MAX_SEARCH_HITS, int(max_hits)))
    max_files = max(1, min(_MAX_SEARCH_FILES, int(max_files)))
    return await asyncio.to_thread(
        _file_search_sync,
        workspace,
        pattern,
        glob,
        use_regex,
        case_sensitive,
        max_hits,
        max_files,
    )


# --------------------------------------------------------------------------- #
# file_write
# --------------------------------------------------------------------------- #
def _file_write_sync(
    workspace: Path, path: str, content: str, create_backup: bool
) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, path, must_exist=False)
    except PathGuardError as exc:
        return _error(str(exc))

    refused = _protected(workspace, target)
    if refused:
        return refused

    backup_path: str | None = None
    if target.exists() and create_backup:
        bp = backup_file(workspace, target)
        backup_path = str(bp.relative_to(workspace)) if bp else None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, content)
    except OSError as exc:
        return _error(f"write failed: {exc}")

    return _ok(
        path=str(target.relative_to(workspace)),
        bytes_written=len(content.encode("utf-8")),
        backup=backup_path,
    )


async def file_write(
    path: str,
    content: str,
    create_backup: bool = True,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_file_write_sync, workspace, path, content, create_backup)


# --------------------------------------------------------------------------- #
# file_patch
# --------------------------------------------------------------------------- #
def _match_ignoring_whitespace(original: str, search: str, replace: str) -> tuple[str, str] | None:
    """Find a block that differs only in whitespace, and re-indent the replacement."""
    wanted = [line.strip() for line in search.strip("\n").splitlines()]
    if not any(wanted):
        return None
    lines = original.splitlines(keepends=True)
    stripped = [line.strip() for line in lines]
    starts = [
        i for i in range(len(lines) - len(wanted) + 1)
        if stripped[i : i + len(wanted)] == wanted
    ]
    if len(starts) != 1:
        return None
    start = starts[0]
    actual = "".join(lines[start : start + len(wanted)])
    first_search = next(line for line in search.strip("\n").splitlines() if line.strip())
    first_actual = next(line for line in lines[start : start + len(wanted)] if line.strip())
    search_indent = first_search[: len(first_search) - len(first_search.lstrip())]
    actual_indent = first_actual[: len(first_actual) - len(first_actual.lstrip())]
    adjusted = []
    for line in replace.strip("\n").splitlines():
        body = line[len(search_indent):] if line.startswith(search_indent) else line.lstrip()
        adjusted.append(f"{actual_indent}{body}" if line.strip() else "")
    text = "\n".join(adjusted)
    if actual.endswith("\n"):
        text += "\n"
    return actual, text


def _closest_lines(original: str, search: str, limit: int = 3) -> list[str]:
    """Where the first line of a failed search nearly matches, as line: text."""
    first = next((line.strip() for line in search.splitlines() if line.strip()), "")
    if not first:
        return []
    lines = original.splitlines()
    stripped = [line.strip() for line in lines]
    return [
        f"{stripped.index(match) + 1}: {match[:120]}"
        for match in difflib.get_close_matches(first, stripped, n=limit, cutoff=0.6)
    ]


def _file_patch_sync(
    workspace: Path, path: str, search: str, replace: str, count: int
) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, path, must_exist=True)
    except PathGuardError as exc:
        return _error(str(exc))

    refused = _protected(workspace, target)
    if refused:
        return refused

    if not search:
        return _error("search block must not be empty")

    try:
        original = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _error(f"read failed: {exc}")

    occurrences = original.count(search)
    fuzzy = False
    if occurrences == 0:
        # Models often get indentation or trailing spaces slightly wrong. Accept
        # that only when it identifies one unmistakable place in the file.
        relocated = _match_ignoring_whitespace(original, search, replace)
        if relocated is None:
            return _error(
                "search block not found. It must match the file exactly; read the file again and copy it.",
                path=str(target.relative_to(workspace)),
                closest_lines=_closest_lines(original, search),
            )
        search, replace = relocated
        occurrences = 1
        fuzzy = True

    bp = backup_file(workspace, target)
    backup_path = str(bp.relative_to(workspace)) if bp else None

    if count <= 0:
        patched = original.replace(search, replace)
        replaced = occurrences
    else:
        patched = original.replace(search, replace, count)
        replaced = min(count, occurrences)

    try:
        _atomic_write_text(target, patched)
    except OSError as exc:
        return _error(f"write failed: {exc}")

    return _ok(
        path=str(target.relative_to(workspace)),
        occurrences=occurrences,
        replaced=replaced,
        backup=backup_path,
        whitespace_adjusted=fuzzy,
    )


async def file_patch(
    path: str,
    search: str,
    replace: str,
    count: int = 0,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_file_patch_sync, workspace, path, search, replace, count)


# --------------------------------------------------------------------------- #
# file_move
# --------------------------------------------------------------------------- #
def _file_move_sync(workspace: Path, src: str, dst: str) -> dict[str, Any]:
    try:
        source = resolve_inside(workspace, src, must_exist=True)
        target = resolve_inside(workspace, dst, must_exist=False)
    except PathGuardError as exc:
        return _error(str(exc))

    refused = _protected(workspace, source, target)
    if refused:
        return refused

    if target.exists():
        return _error("destination already exists", destination=str(target.relative_to(workspace)))

    bp = backup_file(workspace, source)
    backup_path = str(bp.relative_to(workspace)) if bp else None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except OSError as exc:
        return _error(f"move failed: {exc}")

    return _ok(
        source=str(source.relative_to(workspace)),
        destination=str(target.relative_to(workspace)),
        backup=backup_path,
    )


async def file_move(
    source: str, destination: str, workspace_path: str | None = None
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_file_move_sync, workspace, source, destination)


# --------------------------------------------------------------------------- #
# file_delete
# --------------------------------------------------------------------------- #
def _file_delete_sync(workspace: Path, path: str) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, path, must_exist=True)
    except PathGuardError as exc:
        return _error(str(exc))

    refused = _protected(workspace, target)
    if refused:
        return refused

    if target.is_dir():
        return _error("refusing to delete a directory; use execute_command for that")

    bp = backup_file(workspace, target)
    backup_path = str(bp.relative_to(workspace)) if bp else None

    try:
        target.unlink()
    except OSError as exc:
        return _error(f"delete failed: {exc}")

    return _ok(path=str(target.relative_to(workspace)), backup=backup_path)


async def file_delete(path: str, workspace_path: str | None = None) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_file_delete_sync, workspace, path)


# --------------------------------------------------------------------------- #
# dir_create
# --------------------------------------------------------------------------- #
def _dir_create_sync(workspace: Path, path: str) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, path, must_exist=False)
    except PathGuardError as exc:
        return _error(str(exc))

    refused = _protected(workspace, target)
    if refused:
        return refused
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error(f"mkdir failed: {exc}")
    return _ok(path=str(target.relative_to(workspace)))


async def dir_create(path: str, workspace_path: str | None = None) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_dir_create_sync, workspace, path)


# --------------------------------------------------------------------------- #
# file_stat
# --------------------------------------------------------------------------- #
def _file_stat_sync(workspace: Path, path: str, compute_hash: bool) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, path, must_exist=True)
    except PathGuardError as exc:
        return _error(str(exc))

    try:
        st = target.stat()
    except OSError as exc:
        return _error(f"stat failed: {exc}")

    payload: dict[str, Any] = {
        "path": str(target.relative_to(workspace)),
        "is_file": target.is_file(),
        "is_dir": target.is_dir(),
        "size_bytes": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        "mode": oct(st.st_mode & 0o777),
    }

    if compute_hash and target.is_file() and st.st_size <= _MAX_READ_BYTES:
        try:
            h = hashlib.sha256()
            with target.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            payload["sha256"] = h.hexdigest()
        except OSError as exc:
            payload["sha256_error"] = str(exc)
    elif compute_hash:
        payload["sha256_skipped"] = "file too large or not a file"

    return _ok(**payload)


async def file_stat(
    path: str, compute_hash: bool = True, workspace_path: str | None = None
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_file_stat_sync, workspace, path, compute_hash)


# --------------------------------------------------------------------------- #
# count_lines
# --------------------------------------------------------------------------- #
def _count_lines_sync(workspace: Path, path: str) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, path, must_exist=True)
    except PathGuardError as exc:
        return _error(str(exc))

    if not target.is_file():
        return _error("not a file")
    if is_binary_file(target):
        return _error("binary file")

    lines = words = chars = 0
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                lines += 1
                words += len(line.split())
                chars += len(line)
    except OSError as exc:
        return _error(f"read failed: {exc}")

    return _ok(
        path=str(target.relative_to(workspace)),
        lines=lines,
        words=words,
        chars=chars,
    )


async def count_lines(path: str, workspace_path: str | None = None) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_count_lines_sync, workspace, path)


# --------------------------------------------------------------------------- #
# git_status / git_diff
# --------------------------------------------------------------------------- #
def _run_git(workspace: Path, args: list[str], timeout: float = 15.0) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            # Repository config can name programs to run, and a repository's
            # config is not trusted: the sandbox can write to it in a new repo.
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return _error("git is not installed")
    except subprocess.TimeoutExpired:
        return _error("git command timed out")
    except OSError as exc:
        return _error(f"git failed: {exc}")

    if proc.returncode != 0:
        return _error(
            f"git exited with {proc.returncode}",
            stderr=proc.stderr.strip()[:2000],
        )
    return _ok(stdout=proc.stdout)


async def git_status(workspace_path: str | None = None) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    result = await asyncio.to_thread(_run_git, workspace, ["status", "--porcelain=v1", "-b"])
    if not result.get("ok"):
        return result
    lines = [ln for ln in result["stdout"].splitlines() if ln.strip()]
    branch = ""
    entries: list[dict[str, str]] = []
    for ln in lines:
        if ln.startswith("## "):
            branch = ln[3:]
            continue
        if len(ln) >= 3:
            entries.append({"status": ln[:2].strip(), "path": ln[3:]})
    return _ok(branch=branch, changed=len(entries), entries=entries)


async def git_diff(
    path: str | None = None,
    staged: bool = False,
    max_lines: int = 400,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))

    args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv"]
    if staged:
        args.append("--cached")
    if path:
        try:
            target = resolve_inside(workspace, path, must_exist=False)
        except PathGuardError as exc:
            return _error(str(exc))
        args.extend(["--", str(target.relative_to(workspace))])

    result = await asyncio.to_thread(_run_git, workspace, args)
    if not result.get("ok"):
        return result

    lines = result["stdout"].splitlines()
    truncated = len(lines) > max_lines
    return _ok(
        staged=staged,
        line_count=len(lines),
        truncated=truncated,
        diff="\n".join(lines[:max_lines]),
    )


# --------------------------------------------------------------------------- #
# json_query
# --------------------------------------------------------------------------- #
def _json_query_sync(workspace: Path, path: str, dotted: str) -> dict[str, Any]:
    try:
        target = resolve_inside(workspace, path, must_exist=True)
    except PathGuardError as exc:
        return _error(str(exc))

    try:
        data = json.loads(target.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return _error(f"json load failed: {exc}")

    if not dotted:
        return _ok(value=data)

    current: Any = data
    for part in dotted.split("."):
        if isinstance(current, dict):
            if part not in current:
                return _error(f"key not found: {part}")
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError:
                return _error(f"list index must be integer: {part}")
            if idx < 0 or idx >= len(current):
                return _error(f"index out of range: {idx}")
            current = current[idx]
        else:
            return _error(f"cannot descend into {type(current).__name__} at {part}")

    return _ok(value=current)


async def json_query(
    path: str, dotted_path: str = "", workspace_path: str | None = None
) -> dict[str, Any]:
    try:
        workspace = _workspace(workspace_path)
    except PathGuardError as exc:
        return _error(str(exc))
    return await asyncio.to_thread(_json_query_sync, workspace, path, dotted_path)


# --------------------------------------------------------------------------- #
# text_transform
# --------------------------------------------------------------------------- #
def _slugify(text: str) -> str:
    lowered = text.strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


async def text_transform(
    text: str,
    operation: str,
    find: str = "",
    replace: str = "",
) -> dict[str, Any]:
    op = operation.strip().lower()
    if op == "upper":
        return _ok(result=text.upper())
    if op == "lower":
        return _ok(result=text.lower())
    if op == "trim":
        return _ok(result=text.strip())
    if op == "slug":
        return _ok(result=_slugify(text))
    if op == "replace":
        if not find:
            return _error("find must not be empty for replace")
        return _ok(result=text.replace(find, replace))
    return _error(f"unknown operation: {operation}")


# --------------------------------------------------------------------------- #
# time_now
# --------------------------------------------------------------------------- #
async def time_now(timezone_name: str = "local") -> dict[str, Any]:
    now_utc = datetime.now(tz=UTC)
    if timezone_name.lower() == "utc":
        return _ok(iso=now_utc.isoformat(), epoch=now_utc.timestamp(), tz="UTC")
    local = datetime.now()
    return _ok(iso=local.isoformat(), epoch=local.timestamp(), tz="local")


__all__ = [
    "file_search",
    "file_write",
    "file_patch",
    "file_move",
    "file_delete",
    "dir_create",
    "file_stat",
    "count_lines",
    "git_status",
    "git_diff",
    "json_query",
    "text_transform",
    "time_now",
]
