"""The IDE add-on's API: the projects the AI made (each chat's folder), their files, and what the AI changed.

A project is a folder directly under ``workspace/sessions`` (one per chat) or ``workspace/projects``, named in URLs as
``s:<folder>`` or ``p:<folder>``. Every path is resolved inside its project by the path guard, and the history folder
(``.agent_memory``) and the vault are never served as files.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from brain.utils import file_history
from brain.utils.file_history import FileHistory, is_binary, scan
from brain.utils.workspace_context import ID_FILE, PROJECTS_ROOT, SESSIONS_ROOT
from tools.local_ops.path_guard import PathGuardError, resolve_inside
from web.router_api import admin

router = APIRouter(prefix="/api/ide", tags=["ide"])

ROOTS = {"s": SESSIONS_ROOT, "p": PROJECTS_ROOT}
PRIVATE = {".agent_memory", ".vault"}
MAX_EDIT_BYTES = 5_000_000
MAX_SEARCH_HITS = 2000
MAX_SEARCH_FILE = 2_000_000
# A page opened from the project runs its own scripts, but with no origin of its own: it cannot reach this app's
# API or storage. Its own files are loaded from an opaque origin, so they are marked as loadable from anywhere.
PREVIEW_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads; frame-ancestors 'self'"
PREVIEW_HEADERS = {
    "Content-Security-Policy": PREVIEW_CSP,
    "X-Frame-Options": "SAMEORIGIN",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Access-Control-Allow-Origin": "*",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
_TAG_RE = re.compile(r"^\[(\w+)\]\s*")


def roots() -> dict[str, Path]:
    return ROOTS


def _project(project: str) -> Path:
    kind, _, name = project.partition(":")
    root = roots().get(kind)
    if root is None or not name or name in {".", ".."} or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404, "Proyek tidak ada.")
    folder = root / name
    if not folder.is_dir():
        raise HTTPException(404, "Proyek tidak ada.")
    return folder


def _inside(folder: Path, path: str, *, must_exist: bool = True) -> Path:
    try:
        target = resolve_inside(folder.resolve(), path, must_exist=must_exist)
    except PathGuardError:
        raise HTTPException(404, "File tidak ada di proyek ini.") from None
    relative = target.relative_to(folder.resolve()).parts
    if not relative or relative[0] in PRIVATE or relative[-1] == ID_FILE:
        raise HTTPException(404, "File tidak ada di proyek ini.")
    return target


def _relative(folder: Path, target: Path) -> str:
    return target.relative_to(folder.resolve()).as_posix()


def _describe(kind: str, folder: Path) -> dict[str, Any]:
    files = scan(folder)
    updated = max((m for m, _ in files.values()), default=int(folder.stat().st_mtime_ns)) / 1e9
    tag_match = _TAG_RE.match(folder.name)
    session = ""
    with contextlib.suppress(OSError):
        session = (folder / ID_FILE).read_text(encoding="utf-8").strip()
    if not session and kind == "s" and folder.name.startswith("sess-"):
        session = folder.name
    turns = FileHistory(folder).turns()
    return {
        "id": f"{kind}:{folder.name}",
        "name": _TAG_RE.sub("", folder.name) or folder.name,
        "tag": tag_match.group(1) if tag_match else ("proyek" if kind == "p" else ""),
        "session": session,
        "files": len(files),
        "size": sum(s for _, s in files.values()),
        "updated": updated,
        "ai_turns": sum(1 for t in turns if t.by == "ai"),
        "last_change": turns[-1].ended if turns else 0,
    }


@router.get("/projects")
async def projects() -> dict[str, Any]:
    def work() -> list[dict[str, Any]]:
        found = []
        for kind, root in roots().items():
            if not root.is_dir():
                continue
            for folder in root.iterdir():
                if folder.is_dir() and not folder.name.startswith("."):
                    with contextlib.suppress(OSError):
                        found.append(_describe(kind, folder))
        return sorted(found, key=lambda p: p["updated"], reverse=True)

    return {"projects": await asyncio.to_thread(work)}


@router.get("/p/{project}/tree")
async def tree(project: str) -> dict[str, Any]:
    folder = _project(project)

    def work() -> dict[str, Any]:
        files = scan(folder)
        folders: set[str] = set()
        for relative in files:
            parts = relative.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                folders.add("/".join(parts[:i]))
        # Empty folders count too.
        for current, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not file_history._skip_dir(d)]
            relative = Path(current).relative_to(folder).as_posix()
            if relative != "." and not names and not dirs:
                folders.add(relative)
        history = FileHistory(folder)
        turns = history.turns()
        last_ai = next((t for t in reversed(turns) if t.by == "ai"), None)
        return {
            "root": str(folder),
            "files": [{"path": p, "mtime": m / 1e9, "size": s} for p, (m, s) in sorted(files.items())],
            "folders": sorted(folders),
            "changed": history.changed_files(),
            "last_turn": {p.path: p.status for p in last_ai.changes} if last_ai else {},
            "stamp": max((m for m, _ in files.values()), default=0) / 1e9 + len(files) + len(turns),
        }

    return await asyncio.to_thread(work)


@router.get("/p/{project}/file")
async def read_file(project: str, path: str = Query(..., min_length=1, max_length=1000)) -> dict[str, Any]:
    folder = _project(project)
    target = _inside(folder, path)
    if not target.is_file():
        raise HTTPException(404, "Itu folder, bukan file.")
    stat = target.stat()
    # Nanoseconds go out as text: a JavaScript number would round them, and every save would look like a conflict.
    info: dict[str, Any] = {"path": _relative(folder, target), "size": stat.st_size, "mtime": str(stat.st_mtime_ns)}
    if stat.st_size > MAX_EDIT_BYTES:
        return {**info, "text": None, "binary": False, "too_big": True}
    data = await asyncio.to_thread(target.read_bytes)
    if is_binary(data):
        return {**info, "text": None, "binary": True, "too_big": False}
    return {**info, "text": data.decode("utf-8", errors="replace"), "binary": False, "too_big": False}


class SaveInput(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    text: str = Field(max_length=MAX_EDIT_BYTES)
    mtime: str | None = Field(default=None, max_length=40)   # the version the editor opened (ns); a newer one on disk is a conflict


@router.put("/p/{project}/file", dependencies=[Depends(admin)])
async def save_file(project: str, payload: SaveInput, force: bool = False) -> dict[str, Any]:
    folder = _project(project)
    target = _inside(folder, payload.path, must_exist=False)
    if target.is_dir():
        raise HTTPException(409, "Sudah ada folder dengan nama itu.")
    if payload.mtime and target.exists() and not force and str(target.stat().st_mtime_ns) != payload.mtime:
        raise HTTPException(409, "File ini sudah diubah (mungkin oleh AI) sejak kamu buka. Muat ulang atau simpan paksa.")

    def work() -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.ide-tmp")
        temporary.write_text(payload.text, encoding="utf-8", newline="")
        os.replace(temporary, target)
        return str(target.stat().st_mtime_ns)

    return {"path": _relative(folder, target), "mtime": await asyncio.to_thread(work)}


class PathInput(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    to: str | None = Field(default=None, max_length=1000)


@router.post("/p/{project}/mkdir", dependencies=[Depends(admin)])
async def make_folder(project: str, payload: PathInput) -> dict[str, Any]:
    folder = _project(project)
    target = _inside(folder, payload.path, must_exist=False)
    if target.exists():
        raise HTTPException(409, "Nama itu sudah ada.")
    target.mkdir(parents=True)
    return {"path": _relative(folder, target)}


@router.post("/p/{project}/rename", dependencies=[Depends(admin)])
async def rename(project: str, payload: PathInput) -> dict[str, Any]:
    folder = _project(project)
    source = _inside(folder, payload.path)
    target = _inside(folder, payload.to or "", must_exist=False)
    if target.exists():
        raise HTTPException(409, "Nama tujuan sudah ada.")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    return {"path": _relative(folder, target)}


@router.post("/p/{project}/delete", dependencies=[Depends(admin)])
async def delete(project: str, payload: PathInput) -> dict[str, Any]:
    """Move a file or folder into the project's own trash (``.agent_memory/trash``), never past recovery."""
    folder = _project(project)
    target = _inside(folder, payload.path)
    trash = folder / ".agent_memory" / "trash" / time.strftime("%Y%m%d-%H%M%S")
    trash.mkdir(parents=True, exist_ok=True)
    destination = trash / _relative(folder, target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, str(target), str(destination))
    return {"path": payload.path, "trash": destination.relative_to(folder).as_posix()}


@router.get("/p/{project}/raw/{path:path}")
async def raw(project: str, path: str) -> FileResponse:
    """A project file as itself, for the preview: a page runs sandboxed, with relative links to its own files."""
    folder = _project(project)
    target = _inside(folder, path)
    if target.is_dir():
        index = target / "index.html"
        if not index.is_file():
            raise HTTPException(404, "Folder ini tidak punya index.html.")
        target = index
    return FileResponse(target, headers=PREVIEW_HEADERS)


@router.get("/p/{project}/search")
async def search(project: str, q: str = Query(..., min_length=1, max_length=300), regex: bool = False,
                 case: bool = False) -> dict[str, Any]:
    folder = _project(project)
    flags = 0 if case else re.IGNORECASE
    try:
        pattern = re.compile(q if regex else re.escape(q), flags)
    except re.error as exc:
        raise HTTPException(422, f"Pola tidak valid: {exc}") from None

    def work() -> dict[str, Any]:
        hits: list[dict[str, Any]] = []
        files = 0
        for relative, (_, size) in sorted(scan(folder).items()):
            if size > MAX_SEARCH_FILE:
                continue
            try:
                data = (folder / relative).read_bytes()
            except OSError:
                continue
            if is_binary(data):
                continue
            matched = False
            for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
                found = pattern.search(line)
                if found:
                    matched = True
                    hits.append({"path": relative, "line": number, "col": found.start() + 1, "text": line[:300]})
                    if len(hits) >= MAX_SEARCH_HITS:
                        return {"hits": hits, "files": files + 1, "more": True}
            files += matched
        return {"hits": hits, "files": files, "more": False}

    return await asyncio.to_thread(work)


@router.get("/p/{project}/turns")
async def turns(project: str) -> dict[str, Any]:
    folder = _project(project)
    found = await asyncio.to_thread(FileHistory(folder).turns)
    return {"turns": [t.public() for t in reversed(found)]}


@router.get("/p/{project}/diff")
async def diff(project: str, turn: str = Query(..., max_length=40), path: str = Query(..., max_length=1000)) -> dict[str, Any]:
    folder = _project(project)
    history = FileHistory(folder)
    found = history.turn(turn)
    change = next((c for c in found.changes if c.path == path), None) if found else None
    if change is None:
        raise HTTPException(404, "Perubahan itu tidak ada.")
    before, after = history.content(change.before), history.content(change.after)
    stored = (change.status == "added" or before is not None) and (change.status == "deleted" or after is not None)
    return {
        "path": path, "status": change.status, "binary": change.binary, "stored": stored,
        "before": before.decode("utf-8", errors="replace") if before is not None else "",
        "after": after.decode("utf-8", errors="replace") if after is not None else "",
        "prompt": found.prompt if found else "", "ended": found.ended if found else 0,
    }


class RestoreInput(BaseModel):
    turn: str = Field(min_length=1, max_length=40)
    path: str | None = Field(default=None, max_length=1000)


@router.post("/p/{project}/restore", dependencies=[Depends(admin)])
async def restore(project: str, payload: RestoreInput) -> dict[str, Any]:
    folder = _project(project)
    try:
        done = await asyncio.to_thread(FileHistory(folder).restore, payload.turn, payload.path)
    except KeyError:
        raise HTTPException(404, "Perubahan itu tidak ada.") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"turn": done.public() if done else None}


@router.get("/p/{project}/zip")
async def download(project: str) -> Response:
    folder = _project(project)

    def work() -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(scan(folder)):
                archive.write(folder / relative, relative)
        return buffer.getvalue()

    data = await asyncio.to_thread(work)
    name = re.sub(r"[^\w.-]+", "_", _TAG_RE.sub("", folder.name)).strip("_") or "proyek"
    return StreamingResponse(iter([data]), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
