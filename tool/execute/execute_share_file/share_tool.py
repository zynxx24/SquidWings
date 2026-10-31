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
    if relative.parts and relative.parts[0] in shared_files.PRIVATE_DIRS:
        return {"ok": False, "error": "Session memory cannot be shared."}
    if not target.is_file():
        return {"ok": False, "error": f"File not found: {path}"}
    size = target.stat().st_size
    if size > shared_files.MAX_SHARE_BYTES:
        limit = shared_files.MAX_SHARE_BYTES // (1024 * 1024)
        return {"ok": False, "error": f"File is {size // (1024 * 1024)} MB; the limit is {limit} MB."}
    shared_files.add(root, relative.as_posix())
    kind = shared_files.kind_of(target.name)
    url = shared_files.url_for(session, relative.as_posix())
    markdown = shared_files.markdown(session, relative.as_posix(), caption.strip() or target.name)
    return {
        "ok": True, "name": target.name, "kind": kind, "size": size, "url": url, "markdown": markdown,
        "note": "The file is shown to the user under your answer. Do not paste the link again.",
    }
