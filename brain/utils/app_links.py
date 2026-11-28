"""An HTML file the agent made, opened as an app: in the web chat, in Telegram (a Mini App), or from the terminal.

``publish(html)`` freezes a copy of the page and the files beside it (CSS, JS, images) under a random token, and
``/a/<token>/<file>`` serves that copy. The copy means a later edit or a deleted chat does not break a link that was
already sent, and only that folder is exposed, never the rest of the workspace.

Every response carries ``Content-Security-Policy: sandbox ...``: the page's scripts run, but in an origin of their
own, so they cannot read this server's cookies or call its API, even though the page comes from the same host.
The token is the only key (32 random bytes), which is why ``/a/`` needs no access PIN. Copies older than
``KEEP_DAYS`` are removed.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import re
import secrets
import shutil
import time
from pathlib import Path

from brain.config import paths

KEEP_DAYS = 30
MAX_BYTES = 30 * 1024 * 1024
MAX_FILES = 400
_SKIP_DIRS = frozenset({"node_modules", ".git", ".agent_memory", ".vault", "__pycache__", "uploads", ".venv", "vendor"})
_SKIP_FILES = re.compile(r"(^\.env.*|.*\.(pem|key|p12|pfx|sqlite|db)$|^id_(rsa|ed25519|ecdsa)$)", re.IGNORECASE)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
HEADERS = {
    # frame-ancestors 'self': only this server's own pages (the Studio's Video Editor preview) may frame an app.
    "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads; frame-ancestors 'self'",
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex",
    "Cache-Control": "no-store",
}


def store() -> Path:
    return paths.data_dir() / "apps"


def is_app(path: Path | str) -> bool:
    return Path(path).suffix.lower() in {".html", ".htm"}


def publish(html: Path) -> str:
    """Freeze ``html`` and its folder; returns the token. Raises ValueError when the folder is too big."""
    html = html.resolve()
    if not html.is_file() or not is_app(html):
        raise ValueError(f"{html.name} is not an HTML file")
    prune()
    token = secrets.token_urlsafe(32)
    target = store() / token
    target.mkdir(parents=True)
    copied, size = 0, 0
    try:
        for source in sorted(html.parent.rglob("*")):
            relative = source.relative_to(html.parent)
            if source.is_symlink() or not source.is_file() or _SKIP_FILES.match(source.name):
                continue
            if any(part in _SKIP_DIRS or part.startswith(".") for part in relative.parts[:-1]):
                continue
            size += source.stat().st_size
            copied += 1
            if size > MAX_BYTES or copied > MAX_FILES:
                raise ValueError(f"folder {html.parent.name} is too large to share as an app "
                                 f"(max {MAX_FILES} files, {MAX_BYTES // 2**20} MB)")
            destination = target / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        (target / "meta.json").write_text(json.dumps({"entry": html.name, "made_at": time.time()}), encoding="utf-8")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return token


def entry(token: str) -> str | None:
    if not _TOKEN.match(token or ""):
        return None
    with contextlib.suppress(OSError, ValueError):
        return str(json.loads((store() / token / "meta.json").read_text(encoding="utf-8"))["entry"])
    return None


def path_for(token: str) -> str:
    """The link (path only) that opens the app."""
    return f"/a/{token}/{entry(token) or 'index.html'}"


def resolve(token: str, relative: str) -> Path | None:
    """The frozen file ``relative`` of app ``token``, or None (unknown token, a path that escapes, a missing file)."""
    if entry(token) is None:
        return None
    root = (store() / token / "files").resolve()
    candidate = (root / (relative or entry(token) or "")).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def content_type(path: Path) -> str:
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"{kind}; charset=utf-8" if kind.startswith("text/") or kind in {"application/javascript", "application/json"} else kind


def prune(now: float | None = None) -> int:
    now = time.time() if now is None else now
    removed = 0
    root = store()
    if not root.is_dir():
        return 0
    for folder in root.iterdir():
        made = 0.0
        with contextlib.suppress(OSError, ValueError, KeyError):
            made = float(json.loads((folder / "meta.json").read_text(encoding="utf-8"))["made_at"])
        if now - made > KEEP_DAYS * 86400:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


def response(token: str, relative: str = "") -> object:
    """The Starlette response for ``/a/<token>/<relative>``."""
    from starlette.responses import FileResponse, HTMLResponse

    path = resolve(token, relative)
    if path is None:
        return HTMLResponse("<!doctype html><meta charset=utf-8><p>Aplikasi ini tidak ada atau sudah kedaluwarsa.</p>",
                            status_code=404, headers=HEADERS)
    return FileResponse(path, media_type=content_type(path), headers=HEADERS)
