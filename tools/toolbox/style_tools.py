"""style_pick: a design direction for a web page, slides, a picture, a video or a document.

A model left alone reaches for the same look every time (and the same look as every other
model: purple gradients, centred cards, Inter). This tool hands it one committed direction
(palette, type, layout, motifs, motion, image recipe, what to avoid), skipping looks used
lately in any chat (``brain.utils.style_history``). Asking again gives a different one.

Most directions are generated (``design_genome``: billions of combinations, every palette
checked for contrast, ids like ``g03-5a1f9c``); some come from the hand-written classics in
``input/styles/styles.yaml``. ``design_explore`` lays many generated options side by side on
one page for the user to choose from.
"""

from __future__ import annotations

import json
import random
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml

from brain.utils import style_history

from . import _files, design_genome
from .catalog import group, param, spec

group("design", "Design directions (style, palette, type) for web, slides, images and video",
      "desain, design, tampilan, gaya, style, tema, theme, landing page, website, poster, logo, banner, slide, "
      "presentasi, ilustrasi, thumbnail, warna, palet, palette, font, tipografi, typography, look, estetika, aesthetic")

STYLES_FILE = Path(__file__).resolve().parents[2] / "input" / "styles" / "styles.yaml"
MEDIA = ("web", "app", "slides", "image", "video", "document")
# An app (dashboard, admin, a school or shop system) is checked before a web page: it needs a working screen, not a
# landing page.
_ALIASES = {
    "app": ("app", "aplikasi", "dashboard", "admin", "panel", "sistem", "system", "crud", "laravel", "inventory",
            "inventaris", "kasir", "pos", "absensi", "ppdb", "erp", "crm", "manajemen", "management", "portal",
            "back office", "backoffice", "monitoring", "tracker"),
    "web": ("web", "website", "situs", "landing", "landing page", "halaman", "page", "ui", "ux", "frontend", "html",
            "css"),
    "slides": ("slides", "slide", "pptx", "ppt", "powerpoint", "presentasi", "presentation", "deck"),
    "image": ("image", "gambar", "poster", "logo", "ilustrasi", "illustration", "banner", "thumbnail", "flyer",
              "pamflet", "brosur", "brochure", "svg", "png", "icon", "ikon", "foto", "photo", "cover", "sampul"),
    "video": ("video", "animasi", "animation", "reels", "reel", "tiktok", "shorts", "motion", "mp4", "film", "klip"),
    "document": ("document", "dokumen", "docx", "word", "laporan", "report", "makalah", "proposal", "surat", "pdf"),
}
# Mostly a direction that fits the subject; sometimes any fresh one, so the same subject does not
# always land on the same two looks.
_FITTING_SHARE = 0.8
# Share of picks that are generated rather than hand-written. Office files keep more classics,
# since their themes are fixed palettes.
_GENOME_SHARE = {"web": 0.85, "app": 1.0, "image": 0.85, "video": 0.85, "slides": 0.5, "document": 0.5}
_EXPLORE_MAX = 30


@lru_cache(maxsize=1)
def styles() -> tuple[dict[str, Any], ...]:
    data = yaml.safe_load(STYLES_FILE.read_text(encoding="utf-8")) or []
    return tuple(s for s in data if isinstance(s, dict) and s.get("id"))


def medium_of(text: str) -> str:
    """'landing page' -> web, 'pptx' -> slides, ... ; unknown -> web."""
    lowered = (text or "").strip().lower()
    if lowered in MEDIA:
        return lowered
    for medium, words in _ALIASES.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", lowered) for w in words):
            return medium
    return "web"


def _fit(entry: dict[str, Any], subject: str) -> int:
    lowered = subject.lower()
    return sum(1 for tag in entry.get("fits", []) if re.search(rf"(?<![a-z0-9]){re.escape(str(tag).lower())}", lowered))


def fonts_url(families: list[str]) -> str:
    unique = list(dict.fromkeys(f for f in families if f))
    query = "&".join(f"family={quote_plus(f)}:wght@400;600;700" for f in unique)
    return f"https://fonts.googleapis.com/css2?{query}&display=swap"


def choose(medium: str, subject: str = "", avoid: list[str] | None = None,
           rng: random.Random | None = None) -> dict[str, Any]:
    """One fitting style for the medium that recent chats have not used, at random, weighted by fit."""
    pool = [s for s in styles() if medium in s.get("media", [])]
    if not pool:
        raise ValueError(f"no style for medium {medium}")
    skip = set(style_history.recent(medium)) | set(avoid or [])
    fresh = [s for s in pool if s["id"] not in skip] or [s for s in pool if s["id"] not in set(avoid or [])] or pool
    rng = rng or random.SystemRandom()
    fitting = [s for s in fresh if _fit(s, subject)]
    if fitting and rng.random() < _FITTING_SHARE:
        return rng.choices(fitting, weights=[_fit(s, subject) for s in fitting], k=1)[0]
    return rng.choice(fresh)


def describe(entry: dict[str, Any], medium: str, rng: random.Random | None = None) -> dict[str, Any]:
    palettes = entry.get("palettes") or [{}]
    index = (rng or random.SystemRandom()).randrange(len(palettes))
    palette, kind = palettes[index], entry.get("type") or {}
    out: dict[str, Any] = {
        "style": entry["id"],
        "name": entry.get("name", entry["id"]),
        "medium": medium,
        "palette": palette,
        "palette_variant": index + 1,
        "type": kind,
        "layout": entry.get("layout", ""),
        "motifs": entry.get("motifs", ""),
        "avoid": entry.get("avoid", ""),
    }
    if medium in ("web", "video", "image"):
        out["fonts_css"] = fonts_url([kind.get("heading", ""), kind.get("body", ""), kind.get("ui", "")])
    if medium in ("web", "video"):
        out["motion"] = entry.get("motion", "")
    if medium == "image":
        out["image_recipe"] = entry.get("image", "")
    if medium in ("slides", "document") and entry.get("office_theme"):
        out["office_theme"] = entry["office_theme"]
        out["how"] = f"pass theme='{entry['office_theme']}' to slides_create/doc_create; follow the layout notes for structure"
    return out


@spec("style_pick", "Get one committed design direction for a web page, slides, a picture (poster, logo, illustration, "
      "thumbnail) or a video: palette with hex roles, Google Fonts (with a CSS link), layout, motifs, motion, an image "
      "recipe, and what to avoid. It skips looks used lately in this and other chats, so call it at the start of any "
      "visual work and build exactly that; call it again (or pass avoid) for a different one. Pass style to reuse one "
      "on purpose, e.g. slides that match an earlier poster.",
      "design", [param("medium", description="web, slides, image, video or document (words like 'landing page', "
                                              "'pptx', 'poster' work too)."),
                 param("subject", description="What it is for, e.g. 'warung kopi di Bandung', 'seminar AI kampus'. "
                                              "Picks a fitting direction.", required=False),
                 param("style", description="A style id to use as is (from an earlier pick).", required=False),
                 param("avoid", "list", "Style ids not to use this time.", required=False)],
      keywords="pilih gaya, pilih style, style desain, arah desain, design direction, palet warna, color palette, "
               "pilih font, tema desain, biar beda, jangan sama, variasi desain",
      lead=True)
def style_pick(medium: str, subject: str = "", style: str = "", avoid: list[str] | None = None) -> dict[str, Any]:
    kind = medium_of(medium)
    if kind == "web" and subject and medium_of(subject) == "app":
        # "web" for a dashboard or a school system: give it an app's layout, not a landing page's.
        kind = "app"
    by_id = {s["id"]: s for s in styles()}
    wanted = (style or "").strip().lower()
    avoided = {str(a).strip().lower() for a in avoid or []}
    generated = design_genome.is_genome_id(wanted) or (
        not wanted and random.SystemRandom().random() < _GENOME_SHARE.get(kind, 0.85))
    if generated:
        gid = wanted
        while not gid or gid in avoided:
            gid = design_genome.new_id(subject)
        direction = design_genome.build(gid, kind, subject=subject)
        design_genome.remember(direction)
        style_history.remember(kind, gid)
        _keep_for_check(direction)
        return {
            **design_genome.public(direction),
            "ok": True,
            "alternatives": [design_genome.new_id(subject) for _ in range(3)],
            "rule": ("Build exactly this direction; say it in one line to the user (its name and why it fits). Use only "
                     "these colours (css_tokens) and fonts (fonts_css), the layout, shape and motifs, and state the "
                     "design_read dials. Reuse the same style id for other files of the same set. Change it only if the "
                     "user asks for another look."),
        }
    if wanted:
        if wanted not in by_id:
            raise ValueError(f"unknown style {wanted}; known: {', '.join(sorted(by_id))}")
        entry = by_id[wanted]
    else:
        entry = choose(kind, subject, avoid=sorted(avoided))
    style_history.remember(kind, entry["id"])
    out = describe(entry, kind)
    _keep_for_check({"style": entry["id"], "medium": kind, "type": out.get("type"), "palette": out.get("palette"),
                     "layout": out.get("layout", "")})
    others = [s["id"] for s in styles() if kind in s.get("media", []) and s["id"] != entry["id"]]
    out.update(
        ok=True,
        alternatives=random.SystemRandom().sample(others, k=min(3, len(others))),
        rule=("Build exactly this direction; say it in one line to the user (name and why it fits). Use only these "
              "colours and fonts. Change it only if the user asks for another look."),
    )
    return out


def _keep_for_check(direction: dict[str, Any]) -> None:
    """Note the direction in this chat's folder, so the check after the turn can see whether the page uses it."""
    from brain.utils.workspace_context import get_active_workspace

    workspace = get_active_workspace()
    if not workspace:
        return
    kind = direction.get("type") or {}
    palette = direction.get("palette") or {}
    record = {
        "style": direction.get("style", ""), "medium": direction.get("medium", ""),
        "fonts": [f for f in (kind.get("heading"), kind.get("body")) if f],
        "colors": [v for v in palette.values() if isinstance(v, str) and v.startswith("#")],
        "layout": str(direction.get("layout", "")).split(":", 1)[0], "at": time.time(),
    }
    try:
        path = Path(workspace) / ".agent_memory" / "style.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lower()
    named = {"white": "ffffff", "black": "000000"}
    text = named.get(text, text).lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if not re.fullmatch(r"[0-9a-f]{6}", text):
        raise ValueError(f"expected a hex colour like #1a2b3c, got {value!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@spec("design_explore", "Generate many different design directions at once (palette, fonts, layout, shapes, motifs, "
      "motion) and lay them out as live specimens on one HTML page the user can open and choose from. Use when the "
      "user wants options, references, inspiration, or 'beberapa pilihan desain'. Share the page with share_file; "
      "when the user picks a code, call style_pick(style=<code>).",
      "design", [param("medium", description="web, slides, image, video or document.", required=False, default="web"),
                 param("subject", description="What it is for, e.g. 'warung kopi di Bandung'.", required=False),
                 param("count", "int", f"How many options (1-{_EXPLORE_MAX}).", required=False, default=12)],
      keywords="pilihan desain, referensi desain, inspirasi desain, moodboard, banyak desain, variasi desain, contoh desain, "
               "design options, design references, explore designs, palet warna, kombinasi font",
      lead=True, workspace=True, read_only=False)
def design_explore(medium: str = "web", subject: str = "", count: int = 12,
                   workspace_path: str | None = None) -> dict[str, Any]:
    kind = medium_of(medium)
    wanted = max(1, min(int(count or 12), _EXPLORE_MAX))
    directions: list[dict[str, Any]] = []
    fonts_used: set[str] = set()
    layouts_used: set[str] = set()
    hues: dict[str, int] = {}
    people: dict[str, int] = {}
    # One personality fills at most a third of the sheet, one hue family two cards.
    per_person, per_hue = max(1, -(-wanted // 3)), 2
    for attempt in range(wanted * 12):
        if len(directions) >= wanted:
            break
        strict = attempt < wanted * 8
        direction = design_genome.build(design_genome.new_id(subject), kind)
        axes = direction["_axes"]
        # Within one sheet every option should look different at a glance.
        if axes["font"] in fonts_used or (strict and (
                (axes["layout"] in layouts_used and len(layouts_used) < 8)
                or hues.get(axes["hue"], 0) >= per_hue
                or people.get(direction["personality"], 0) >= per_person)):
            continue
        fonts_used.add(axes["font"])
        layouts_used.add(axes["layout"])
        hues[axes["hue"]] = hues.get(axes["hue"], 0) + 1
        people[direction["personality"]] = people.get(direction["personality"], 0) + 1
        directions.append(direction)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = _files.destination(f"desain/referensi-{stamp}.html", workspace_path)
    target.write_text(design_genome.gallery_html(directions, subject), encoding="utf-8")
    return {
        "ok": True,
        "file": _files.relative(target, workspace_path),
        "count": len(directions),
        "options": [{"style": d["style"], "name": d["name"], "palette": d["palette_notes"], "dials": d["design_read"]}
                    for d in directions],
        "how": "Share the file with share_file. When the user names a code, call style_pick(style=<code>, medium=...).",
    }


@spec("contrast_check", "WCAG 2.x contrast ratio of text colour on a background colour, with PASS/FAIL for normal text "
      "(4.5:1) and large text (3:1, 18px+ or 14px bold). Check every text/background pair of a page or slide before "
      "delivering it.",
      "design", [param("foreground", description="Text colour, hex like #333 or #1a2b3c."),
                 param("background", description="Background colour, hex.")],
      keywords="kontras, contrast, wcag, aksesibilitas, accessibility, keterbacaan, readable, warna teks", lead=True)
def contrast_check(foreground: str, background: str) -> dict[str, Any]:
    lighter, darker = sorted((_luminance(_rgb(foreground)), _luminance(_rgb(background))), reverse=True)
    ratio = (lighter + 0.05) / (darker + 0.05)
    # WCAG compares the unrounded ratio: 4.48 fails even though it prints as 4.5.
    return {"ratio": round(ratio, 2), "normal_text": "PASS" if ratio >= 4.5 else "FAIL",
            "large_text": "PASS" if ratio >= 3.0 else "FAIL",
            **({"fix": "darken the text or lighten the background (or the reverse) until normal text passes"}
               if ratio < 4.5 else {})}
