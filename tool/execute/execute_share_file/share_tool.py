"""Hand a file from the session's folder to the user, as a video, image, or download."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import shared_files
from utils.workspace_context import get_active_session, get_active_workspace


async def share_file(path: str, caption: str = "") -> dict[str, Any]:
    """Make a file the agent made viewable in the chat. Returns its link."""
    workspace = get_active_workspace()
    session = get_active_session()
    if not workspace or not session:
        return {"ok": False, "error": "No session is open, so there is nowhere to share the file."}
    root = Path(workspace).resolve()
    target = (root / path.strip()).resolve()
    if not target.is_relative_to(root):
        return {"ok": False, "error": "Only files inside this session's folder can be shared."}
    relative = target.relative_to(root)
    if relative.parts and relative.parts[0] in {".agent_memory", ".vault"}:
        return {"ok": False, "error": "Session memory cannot be shared."}
    if not target.is_file():
        return {"ok": False, "error": f"File not found: {path}"}
    size = target.stat().st_size
    if size > shared_files.MAX_SHARE_BYTES:
        return {"ok": False, "error": f"File is {size // (1024 * 1024)} MB; the limit is 500 MB."}
    shared_files.add(root, relative.as_posix())
    kind = shared_files.kind_of(target.name)
    url = shared_files.url_for(session, relative.as_posix())
    label = caption.strip()[:200] or target.name
    markdown = f"![{label}]({url})" if kind != "file" else f"[Unduh {label}]({url})"
    return {
        "ok": True, "name": target.name, "kind": kind, "size": size, "url": url, "markdown": markdown,
        "note": "The file is shown to the user under your answer. Do not paste the link again.",
    }
