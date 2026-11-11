"""Per-session workspace context shared by the web UI and the agent loop.

``workspace/`` is where the model works, kept apart from the agent's own code:

* ``workspace/sessions/[web] <title>/``: one folder per web chat, ``[team] <title>`` per team run.
* ``workspace/projects/[terminal] <title>/``: the project a CLI session works in (or one opened with ``/tree``).

A folder is named after its chat: the source tag and the short title the AI gave it, so it is easy
to find in a file manager. The session id still identifies it inside the app: ``sessions/.names.json``
maps each id to its folder, and each folder carries a ``.session-id`` file so the map can be rebuilt.
Folders made before this naming keep their id as name until ``name_session_folder`` renames them.

The agent loop reads
the active path from a ContextVar, which stays isolated between concurrent
sessions, and falls back to the SQUIDWINGS_WORKSPACE env var.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
from contextvars import ContextVar
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = _PROJECT_ROOT / "workspace"
SESSIONS_ROOT = WORKSPACE_ROOT / "sessions"
PROJECTS_ROOT = WORKSPACE_ROOT / "projects"

_active_workspace: ContextVar[str] = ContextVar("active_workspace", default="")
_active_session: ContextVar[str] = ContextVar("active_session", default="")
_active_ponytail: ContextVar[str] = ContextVar("active_ponytail", default="")


TAGS = ("terminal", "web", "team")
TITLE_MAX = 28                      # characters of title after the tag: "[web] " + at most 28
NAMES_FILE = ".names.json"
ID_FILE = ".session-id"
_names_lock = threading.RLock()
_names_cache: tuple[Path, float, dict[str, str]] | None = None
_pending_names: dict[str, str] = {}   # id -> folder name to use when its folder is first made


def _safe_id(session_id: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "").strip("._") or "default")[:64]


def clip_title(title: str, limit: int = TITLE_MAX) -> str:
    """One line of at most ``limit`` characters, cut at a word when it can be."""
    clean = " ".join((title or "").split()).strip(" .,:;-")
    if len(clean) <= limit:
        return clean
    cut = clean[:limit + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space >= limit // 2 else clean[:limit]).rstrip(" .,:;-")


def folder_label(tag: str, title: str) -> str:
    """``[web] Judul``: a folder name any file system accepts, the title cut to TITLE_MAX characters."""
    clean = clip_title(re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", " ", title or "")).strip(" .")
    tag = tag if tag in TAGS else "web"
    return f"[{tag}] {clean}" if clean else f"[{tag}]"


def _names_path() -> Path:
    return SESSIONS_ROOT / NAMES_FILE


def _read_names() -> dict[str, str]:
    global _names_cache
    path = _names_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    if _names_cache and _names_cache[0] == path and _names_cache[1] == stamp:
        return dict(_names_cache[2])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    names = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    _names_cache = (path, stamp, names)
    return dict(names)


def _write_names(names: dict[str, str]) -> None:
    global _names_cache
    path = _names_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(names, indent=1, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    _names_cache = None


def _free_name(root: Path, name: str, own: Path | None = None) -> str:
    """``name``, or ``name 2``, ``name 3``... when another folder already has it."""
    candidate, k = name, 1
    while (root / candidate).exists() and (own is None or (root / candidate).resolve() != own.resolve()):
        k += 1
        candidate = f"{name} {k}"
    return candidate


def _mark(folder: Path, session_id: str) -> None:
    with contextlib.suppress(OSError):
        (folder / ID_FILE).write_text(session_id, encoding="utf-8")


def session_workspace(session_id: str, create: bool = True) -> Path:
    """This chat's folder: ``[web] <title>`` once named, else its id. Created when ``create``."""
    safe = _safe_id(session_id)
    with _names_lock:
        name = _read_names().get(safe)
        if name and not (SESSIONS_ROOT / name).is_dir():
            name = rebuild_session_names().get(safe)      # renamed or moved by hand
        if name and (SESSIONS_ROOT / name).is_dir():
            return SESSIONS_ROOT / name
        legacy = SESSIONS_ROOT / safe
        if legacy.is_dir() or not create:
            return legacy
        pending = _pending_names.pop(safe, None)
        if not pending:
            legacy.mkdir(parents=True, exist_ok=True)
            return legacy
        name = _free_name(SESSIONS_ROOT, pending)
        path = SESSIONS_ROOT / name
        path.mkdir(parents=True, exist_ok=True)
        _mark(path, safe)
        names = _read_names()
        names[safe] = name
        _write_names(names)
        return path


def reserve_session_name(session_id: str, tag: str, title: str) -> None:
    """The name a chat's folder gets if it is made from now on (before the AI's title is known)."""
    with _names_lock:
        _pending_names[_safe_id(session_id)] = folder_label(tag, title)


def name_session_folder(session_id: str, tag: str, title: str) -> Path | None:
    """Name (or rename) a chat's folder ``[tag] title``. Call between turns, never while a run works in it.

    Returns the folder, or None when the chat has no folder yet (it gets the name when made).
    """
    safe = _safe_id(session_id)
    wanted = folder_label(tag, title)
    with _names_lock:
        current = session_workspace(session_id, create=False)
        if not current.is_dir():
            _pending_names[safe] = wanted
            return None
        name = _free_name(SESSIONS_ROOT, wanted, own=current)
        target = SESSIONS_ROOT / name
        if target != current:
            try:
                current.rename(target)
            except OSError:
                return current
        _mark(target, safe)
        names = _read_names()
        names[safe] = name
        _write_names(names)
        return target


def forget_session_folder(session_id: str) -> None:
    """Drop a deleted chat from the name map."""
    safe = _safe_id(session_id)
    with _names_lock:
        _pending_names.pop(safe, None)
        names = _read_names()
        if names.pop(safe, None) is not None:
            _write_names(names)


def rebuild_session_names() -> dict[str, str]:
    """Rebuild ``.names.json`` from the ``.session-id`` files, for folders renamed or moved by hand."""
    found: dict[str, str] = {}
    with _names_lock:
        if SESSIONS_ROOT.is_dir():
            for folder in SESSIONS_ROOT.iterdir():
                marker = folder / ID_FILE
                if folder.is_dir() and marker.is_file():
                    try:
                        found[_safe_id(marker.read_text(encoding="utf-8").strip())] = folder.name
                    except OSError:
                        continue
        _write_names(found)
    return found


def set_active_workspace(path: str | Path) -> None:
    _active_workspace.set(str(path))


def get_active_workspace() -> str:
    return _active_workspace.get()


def set_active_session(session_id: str) -> None:
    _active_session.set(session_id)


def get_active_session() -> str:
    return _active_session.get()


def set_active_ponytail(mode: str) -> None:
    """This session's Ponytail mode. Empty means the PONYTAIL_MODE default."""
    _active_ponytail.set(mode)


def get_active_ponytail() -> str:
    return _active_ponytail.get()
