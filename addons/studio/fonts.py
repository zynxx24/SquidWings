"""TTF files for the render engine: the same Google Fonts the editor shows, from the same cache.

The editor asks for ``Family:wght@300;...;900`` through ``/api/fonts/css2`` and the server keeps the
CSS and the font files in ``.agent_memory/fonts``. The engine needs one file per (family, weight):
``ttf(family, weight)`` reads that cache (fetching it once when missing) and picks the closest
weight. Offline, or for a family Google does not have, a system font stands in so text still renders.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from brain.config.settings import get_settings
from brain.utils.logger import get_logger

logger = get_logger(__name__)

_WEIGHTS = "300;400;500;600;700;800;900"
_FACE_RE = re.compile(r"@font-face\s*{(.*?)}", re.DOTALL)
_SYSTEM_FALLBACKS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)


def font_dir() -> Path:
    path = get_settings().project_root / ".agent_memory" / "fonts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def css_key(families: list[str]) -> str:
    """The cache file name the web route uses, so both share one cache."""
    return re.sub(r"[^A-Za-z0-9]+", "_", "|".join(sorted(families)))[:180] + ".css"


def _css(family: str) -> str:
    for query in (f"{family}:wght@{_WEIGHTS}", family):
        cached = font_dir() / css_key([query])
        if cached.exists():
            return cached.read_text(encoding="utf-8")
        try:
            answer = httpx.get("https://fonts.googleapis.com/css2", params={"family": query, "display": "swap"}, timeout=15)
        except Exception as exc:  # noqa: BLE001 - offline or blocked: the system font stands in
            logger.debug(f"[studio-fonts] {family}: {exc}")
            return ""
        if answer.status_code == 200:
            css = re.sub(r"https://fonts\.gstatic\.com/", "/api/fonts/file/", answer.text)
            cached.write_text(css, encoding="utf-8")
            return css
    return ""


def _file(path: str) -> Path | None:
    """A font file from the CSS (``/api/fonts/file/<path>``), downloaded into the cache once."""
    if ".." in path or not re.match(r"^[A-Za-z0-9/_.-]{5,200}$", path):
        return None
    cached = font_dir() / "files" / path.replace("/", "_")
    if not cached.exists():
        try:
            answer = httpx.get(f"https://fonts.gstatic.com/{path}", timeout=20)
        except Exception:  # noqa: BLE001 - offline or blocked: the next face, or the system font
            return None
        if answer.status_code != 200:
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(answer.content)
    return cached


def system_font() -> Path | None:
    return next((Path(p) for p in _SYSTEM_FALLBACKS if Path(p).is_file()), None)


def ttf(family: str, weight: int = 400) -> Path | None:
    """The TTF for this family and weight (the nearest weight it has), else a system font."""
    css = _css(family) if family and re.match(r"^[A-Za-z0-9 ]{1,60}$", family) else ""
    faces: list[tuple[int, str]] = []
    for block in _FACE_RE.findall(css):
        if "font-style: italic" in block:
            continue
        w = re.search(r"font-weight:\s*(\d+)", block)
        url = re.search(r"url\(/api/fonts/file/([^)]+)\)\s*format\('truetype'\)", block)
        if w and url:
            faces.append((int(w.group(1)), url.group(1)))
    for _, path in sorted(faces, key=lambda face: (abs(face[0] - int(weight or 400)), face[0])):
        found = _file(path)
        if found is not None:
            return found
    return system_font()


__all__ = ["css_key", "font_dir", "system_font", "ttf"]
