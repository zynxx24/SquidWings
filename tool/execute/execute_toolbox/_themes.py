"""Looks for generated office files, so two documents in a row never come out the same.

Each theme is a palette, a pair of fonts, and the shape of its title and slides. With no
theme asked for, a tool takes one this chat has not used lately, at random, and says which
it took, so files of one set (a report and its slides) can share it on purpose.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from . import _files

RECENT_FILE = ".agent_memory/office_themes.json"
RECENT_KEEP = 6          # the last few looks are skipped when picking a new one


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    primary: str          # titles, header rows, the main shape
    secondary: str        # subheadings, the second shape
    light: str            # banded rows, soft backgrounds
    ink: str              # body text
    heading_font: str
    body_font: str
    title: str            # how a document title sits: band, rule, side, plain
    motif: str            # slide decoration: bar, side, corner, top, frame, dots
    slide_bg: str         # slide background
    dark: bool = False    # light text on a dark slide


THEMES: dict[str, Theme] = {t.name: t for t in (
    Theme("samudra", "Samudra (navy dan toska)", "1F3A5F", "2A9D8F", "E8F4F2", "1D2733",
          "Georgia", "Calibri", "band", "side", "FFFFFF"),
    Theme("senja", "Senja (koral dan ungu)", "E76F51", "6D597A", "FDF0EA", "2E2A33",
          "Trebuchet MS", "Calibri", "rule", "corner", "FFFBF7"),
    Theme("hutan", "Hutan (hijau dan amber)", "2D6A4F", "D4A017", "EEF6F0", "1F2A24",
          "Cambria", "Calibri", "side", "bar", "FBFDF9"),
    Theme("grafit", "Grafit (arang dan kuning)", "2B2D42", "F2C14E", "F1F1F4", "2B2D42",
          "Arial Black", "Arial", "band", "top", "FFFFFF"),
    Theme("lavender", "Lavender (ungu dan merah muda)", "6A4C93", "E56B9F", "F3EEF9", "2D2438",
          "Century Gothic", "Segoe UI", "rule", "dots", "FDFBFF"),
    Theme("terakota", "Terakota (bata dan pasir)", "B5523B", "8C6A43", "F8EFE6", "3A2A22",
          "Palatino Linotype", "Georgia", "side", "frame", "FFFCF8"),
    Theme("kopi", "Kopi (cokelat dan krem)", "5B3A29", "C08552", "F5EDE3", "33241B",
          "Constantia", "Candara", "plain", "bar", "FFFDF9"),
    Theme("malam", "Malam (gelap, sian, dan magenta)", "00B4D8", "F72585", "1F2433", "E9ECF2",
          "Segoe UI Semibold", "Segoe UI", "band", "corner", "11151F", dark=True),
    Theme("batik", "Batik (nila dan emas)", "283593", "C9A227", "ECEEF8", "1C2140",
          "Garamond", "Calibri", "rule", "frame", "FFFFFF"),
    Theme("mint", "Mint (toska muda dan oranye)", "1B998B", "FF9F1C", "E7F7F5", "16302D",
          "Verdana", "Tahoma", "side", "top", "FAFFFE"),
    Theme("sakura", "Sakura (merah muda dan abu)", "C2185B", "607D8B", "FCEAF1", "2F2A2D",
          "Corbel", "Calibri", "plain", "dots", "FFFAFC"),
    Theme("klasik", "Klasik (hitam dan marun)", "1A1A1A", "7B1E3A", "F2F2F2", "1A1A1A",
          "Times New Roman", "Times New Roman", "rule", "bar", "FFFFFF"),
)}
# For school and campus papers: black headings, no colour, Times New Roman.
FORMAL = Theme("formal", "Formal akademik (hitam putih)", "000000", "333333", "F2F2F2", "000000",
               "Times New Roman", "Times New Roman", "plain", "bar", "FFFFFF")
ALL = {**THEMES, FORMAL.name: FORMAL}
RANDOM_WORDS = {"", "acak", "random", "auto", "otomatis", "beda", "baru"}


def names() -> list[str]:
    return sorted(ALL)


def _recent_path(workspace_path: str | None) -> Path | None:
    try:
        return _files.workspace(workspace_path) / RECENT_FILE
    except Exception:  # noqa: BLE001 - no workspace: no memory, still a random theme
        return None


def _recent(workspace_path: str | None) -> list[str]:
    path = _recent_path(workspace_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else []
    except (OSError, ValueError):
        return []
    return [str(n) for n in data if isinstance(n, str)] if isinstance(data, list) else []


def remember(name: str, workspace_path: str | None) -> None:
    path = _recent_path(workspace_path)
    if not path:
        return
    recent = [n for n in _recent(workspace_path) if n != name] + [name]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(recent[-RECENT_KEEP:]), encoding="utf-8")
    except OSError:
        pass


def pick(requested: str | None, workspace_path: str | None, *, formal_hint: bool = False,
         rng: random.Random | None = None) -> Theme:
    """The theme asked for, or a fresh one this chat has not used lately.

    ``formal_hint``: the call asked for Times New Roman, so an academic paper is likely.
    """
    key = (requested or "").strip().lower()
    if key in ALL:
        theme = ALL[key]
    elif key in RANDOM_WORDS and formal_hint:
        theme = FORMAL
    else:
        recent = set(_recent(workspace_path))
        fresh = [t for t in THEMES.values() if t.name not in recent] or list(THEMES.values())
        theme = (rng or random.SystemRandom()).choice(fresh)
    remember(theme.name, workspace_path)
    return theme


def note(theme: Theme) -> str:
    return (f"Tema '{theme.name}' ({theme.label}). Untuk file lain dari set yang sama pakai theme='{theme.name}'; "
            "tanpa theme, file berikutnya otomatis memakai tema lain.")
