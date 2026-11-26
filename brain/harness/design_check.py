"""Did the page the agent just wrote follow the design direction it picked, or fall back to the default look?

``style_pick`` notes its direction in the chat's folder (``.agent_memory/style.json``). After a turn that changed a
page, a stylesheet or a component, this checks the work against it: the direction's fonts and colours must appear in
the project's UI files. It also looks for the tells of the template every model reaches for (Tailwind's stock
gradients and section shell, Inter, frosted glass), which is what makes every design look the same.

The findings go back to the agent through the verifier, so the page is fixed in the same turn.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

UI_SUFFIXES = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".jsx", ".tsx", ".astro"}
_SKIP_DIRS = {"node_modules", "vendor", ".git", ".agent_memory", "storage", "dist", "build", ".next", ".nuxt",
              "__pycache__", ".venv", "graphify-out"}
_MAX_FILE = 300_000
_MAX_FILES = 400
# A direction older than this belongs to earlier work in the same folder.
DIRECTION_AGE = 12 * 3600
_COMPILED = re.compile(r"/\*!\s*tailwindcss|/\*! normalize|sourceMappingURL")

TELLS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"bg-gradient-to-\w+\s+from-(indigo|purple|violet|fuchsia|blue|emerald|sky)-\d00"),
     "gradasi Tailwind bawaan (indigo, ungu, biru, emerald)"),
    (re.compile(r"(font-family:\s*['\"]?Inter\b|family=Inter\b|font-\[?['\"]?Inter)"), "font Inter"),
    (re.compile(r"backdrop-blur|backdrop-filter:\s*blur"), "kaca buram (glassmorphism)"),
    (re.compile(r"max-w-7xl mx-auto px-4 sm:px-6 lg:px-8"), "kerangka section Tailwind bawaan yang diulang"),
    (re.compile(r"(from|via|to)-(purple|violet|indigo)-[456]00[^\n]{0,80}(from|via|to)-(blue|pink|fuchsia)-[456]00"),
     "gradasi ungu ke biru"),
    (re.compile(r"rounded-2xl shadow-(lg|xl)[^\n]{0,120}\n[^\n]{0,200}rounded-2xl shadow-(lg|xl)[^\n]{0,120}\n[^\n]{0,200}rounded-2xl shadow-(lg|xl)"),
     "deretan kartu identik"),
)


def is_ui(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in UI_SUFFIXES or name.endswith(".blade.php")


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_FILE:
            return ""
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return "" if _COMPILED.search(text[:4000]) else text


def _project_ui_text(root: Path) -> str:
    """The project's own UI files, where a direction's fonts and colours would be set."""
    parts: list[str] = []
    count = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in names:
            path = Path(current) / name
            if is_ui(path):
                parts.append(_read(path))
                count += 1
                if count >= _MAX_FILES:
                    return "\n".join(parts)
    return "\n".join(parts)


def direction(workspace: str | Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(workspace) / ".agent_memory" / "style.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("at") or 0) > DIRECTION_AGE:
        return None
    return data


def findings(workspace: str | Path, changed: list[Path]) -> list[str]:
    """What to fix in the look of the pages this turn changed. Empty when there is nothing to say."""
    ui = [p for p in changed if is_ui(p)]
    if not ui or not workspace:
        return []
    changed_text = "\n".join(_read(p) for p in ui)
    if not changed_text.strip():
        return []
    out: list[str] = []
    picked = direction(workspace)
    if picked and picked.get("medium") in {"web", "app"}:
        corpus = _project_ui_text(Path(workspace)).lower() or changed_text.lower()
        fonts = [str(f) for f in picked.get("fonts") or []]
        if fonts and not any(f.lower() in corpus for f in fonts):
            out.append(f"Arah desain {picked.get('style')} memakai font {' / '.join(fonts)}, tapi halamannya tidak memuat "
                       "font itu. Pasang fonts_css dari style_pick dan pakai untuk judul dan isi.")
        colours = [str(c).lower() for c in picked.get("colors") or []]
        if colours and not any(c in corpus for c in colours):
            out.append(f"Warna arah desain {picked.get('style')} tidak dipakai. Tempel css_tokens dari style_pick ke :root "
                       "dan pakai variabelnya, bukan warna bawaan framework.")
    tells = [label for pattern, label in TELLS if pattern.search(changed_text)]
    if len(tells) >= 2 or (tells and not picked):
        hint = ("Panggil style_pick dulu (medium 'app' untuk dashboard atau sistem, 'web' untuk halaman) lalu bangun "
                "persis arahnya." if not picked else "Ganti dengan struktur, warna, dan font dari arah yang dipilih.")
        out.append(f"Tampilannya masih template bawaan yang membuat semua desain mirip: {', '.join(tells)}. {hint}")
    return out
