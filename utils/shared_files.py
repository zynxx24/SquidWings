"""Files the agent handed to the user, kept per session.

The agent calls ``share_file`` on a file it made. That file's path goes into the
session's own manifest, and the web server serves only paths listed there. A
session can never be used to read another session's files, or any file the
agent did not choose to share.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote


def _limit_mb() -> int:
    try:
        return max(1, int(os.getenv("SHARE_MAX_MB", "500")))
    except ValueError:
        return 500


# Largest file the agent can hand to the chat (videos included). SHARE_MAX_MB changes it.
MAX_SHARE_BYTES = _limit_mb() * 1024 * 1024
# Agent state in the session folder. Never served, even through a symlink.
PRIVATE_DIRS = {".agent_memory", ".vault"}
# Shown in the chat. Everything else is offered as a download, never opened inline,
# because an HTML or SVG file served from this origin could run script.
INLINE_KINDS = {
    ".mp4": "video", ".webm": "video", ".mov": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".m4a": "audio",
}


def _manifest(workspace: str | Path) -> Path:
    return Path(workspace) / ".agent_memory" / "shared.json"


def shared(workspace: str | Path) -> list[str]:
    try:
        items = json.loads(_manifest(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(item) for item in items] if isinstance(items, list) else []


def add(workspace: str | Path, relative: str) -> None:
    items = shared(workspace)
    if relative in items:
        return
    path = _manifest(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps([*items, relative]), encoding="utf-8")
    temporary.replace(path)


def kind_of(name: str) -> str:
    return INLINE_KINDS.get(Path(name).suffix.lower(), "file")


def url_for(session_id: str, relative: str) -> str:
    return f"/api/session/{quote(session_id, safe='')}/files/{quote(relative)}"


def markdown(session_id: str, relative: str, label: str = "") -> str:
    """The Markdown that shows a shared file in the chat: media inline, anything else as a link."""
    url = url_for(session_id, relative)
    # Brackets or a line break in the label would end the Markdown link early.
    text = " ".join((label or Path(relative).name).split())[:200]
    text = text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"![{text}]({url})" if kind_of(relative) != "file" else f"[Unduh {text}]({url})"


def missing_from(workspace: str | Path, session_id: str, text: str) -> str:
    """Markdown for every shared file the text does not show yet, ready to append to it."""
    links = [
        markdown(session_id, relative)
        for relative in shared(workspace)
        if url_for(session_id, relative) not in text and resolve(workspace, relative)
    ]
    return "".join(f"\n\n{link}" for link in links)


VIDEO_SUFFIXES = (".mp4", ".webm", ".mov")
# Folders a render never lands in, and that are big enough to make a walk slow.
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", *PRIVATE_DIRS}
_MIN_VIDEO_BYTES = 1024


def _looks_like_video(path: Path) -> bool:
    """A real container, not an empty or half-written file named .mp4."""
    try:
        with path.open("rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    if path.suffix.lower() == ".webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if path.suffix.lower() == ".mov":
        return head[4:8] in {b"ftyp", b"moov", b"wide", b"mdat", b"free"}   # older QuickTime starts with an atom
    return head[4:8] == b"ftyp"


def find_new_videos(workspace: str | Path, since: float, limit: int = 20000) -> list[str]:
    """Videos made in the folder at or after ``since``, best first: newest, and ``share.*`` before a draft.

    For runs whose agents rendered a video but forgot to call share_file. Kit examples copied
    into the folder keep their old modification times, so they are not picked up.
    """
    root = Path(workspace).resolve()
    if not root.is_dir():
        return []
    found: list[tuple[bool, float, str]] = []
    seen = 0
    for directory, dirs, files in root.walk():
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            seen += 1
            if seen > limit:
                break
            path = directory / name
            if path.suffix.lower() not in VIDEO_SUFFIXES or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime + 1 < since or stat.st_size < _MIN_VIDEO_BYTES or stat.st_size > MAX_SHARE_BYTES:
                continue
            if not _looks_like_video(path):
                continue
            found.append((path.stem.lower() == "share", stat.st_mtime, path.relative_to(root).as_posix()))
        if seen > limit:
            break
    found.sort(reverse=True)
    return [relative for _, _, relative in found]


def resolve(workspace: str | Path, relative: str) -> Path | None:
    """The file behind a shared path, or None if it was not shared or has gone."""
    if relative not in shared(workspace):
        return None
    root = Path(workspace).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    # The file may have been swapped for a symlink into the agent's memory since it was shared.
    if path.relative_to(root).parts[0] in PRIVATE_DIRS:
        return None
    return path
