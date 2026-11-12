"""Which looks were used lately, across every chat, so a new chat does not get the same design again.

Unlike most state (see per-session isolation), this is shared on purpose: the point is that
two chats asking for "a landing page" or "slides" come out different. Only style names are
kept, per medium (web, slides, image, video, document, office), the last ``KEEP`` of each.

``STYLE_HISTORY_FILE`` moves the file; tests point it at a temporary folder.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path

KEEP = 10
_lock = threading.Lock()


def _path() -> Path:
    override = os.getenv("STYLE_HISTORY_FILE", "").strip()
    if override:
        return Path(override)
    from config.settings import get_settings

    return get_settings().project_root / ".agent_memory" / "style_history.json"


def _load(path: Path) -> dict[str, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(n) for n in v if isinstance(n, str)] for k, v in data.items() if isinstance(v, list)}


def recent(medium: str) -> list[str]:
    """Oldest first."""
    with _lock:
        return _load(_path()).get(medium, [])


def remember(medium: str, name: str) -> None:
    path = _path()
    with _lock:
        data = _load(path)
        data[medium] = [n for n in data.get(medium, []) if n != name][-(KEEP - 1):] + [name]
        with contextlib.suppress(OSError):  # a read-only disk only costs the variety, never the request
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, path)
