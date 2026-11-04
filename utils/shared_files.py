"""Files the agent handed to the user, kept per session.

The agent calls ``share_file`` on a file it made. That file's path goes into the
session's own manifest, and the web server serves only paths listed there. A
session can never be used to read another session's files, or any file the
agent did not choose to share.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

MAX_SHARE_BYTES = 500 * 1024 * 1024
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
