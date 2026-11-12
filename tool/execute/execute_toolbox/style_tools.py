"""style_pick: a design direction for a web page, slides, a picture, a video or a document.

A model left alone reaches for the same look every time (and the same look as every other
model: purple gradients, centred cards, Inter). This tool hands it one committed direction
from ``brain/styles/styles.yaml`` (palette, type, layout, motifs, motion, image recipe, what
to avoid), chosen at random among the ones that fit the subject, skipping the looks used
lately in any chat (``utils.style_history``). Asking again gives a different one.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml

from utils import style_history

from .catalog import group, param, spec

group("design", "Design directions (style, palette, type) for web, slides, images and video",
      "desain, design, tampilan, gaya, style, tema, theme, landing page, website, poster, logo, banner, slide, "
      "presentasi, ilustrasi, thumbnail, warna, palet, palette, font, tipografi, typography, look, estetika, aesthetic")

STYLES_FILE = Path(__file__).resolve().parents[3] / "brain" / "styles" / "styles.yaml"
MEDIA = ("web", "slides", "image", "video", "document")
_ALIASES = {
    "web": ("web", "website", "situs", "landing", "landing page", "halaman", "page", "ui", "ux", "app", "aplikasi",
            "dashboard", "frontend", "html", "css"),
    "slides": ("slides", "slide", "pptx", "ppt", "powerpoint", "presentasi", "presentation", "deck"),
    "image": ("image", "gambar", "poster", "logo", "ilustrasi", "illustration", "banner", "thumbnail", "flyer",
              "pamflet", "brosur", "brochure", "svg", "png", "icon", "ikon", "foto", "photo", "cover", "sampul"),
    "video": ("video", "animasi", "animation", "reels", "reel", "tiktok", "shorts", "motion", "mp4", "film", "klip"),
    "document": ("document", "dokumen", "docx", "word", "laporan", "report", "makalah", "proposal", "surat", "pdf"),
}
# Mostly a direction that fits the subject; sometimes any fresh one, so the same subject does not
# always land on the same two looks.
_FITTING_SHARE = 0.8


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
    by_id = {s["id"]: s for s in styles()}
    wanted = (style or "").strip().lower()
    if wanted:
        if wanted not in by_id:
            raise ValueError(f"unknown style {wanted}; known: {', '.join(sorted(by_id))}")
        entry = by_id[wanted]
    else:
        entry = choose(kind, subject, avoid=[str(a).strip().lower() for a in avoid or []])
    style_history.remember(kind, entry["id"])
    out = describe(entry, kind)
    others = [s["id"] for s in styles() if kind in s.get("media", []) and s["id"] != entry["id"]]
    out.update(
        ok=True,
        alternatives=random.SystemRandom().sample(others, k=min(3, len(others))),
        rule=("Build exactly this direction; say it in one line to the user (name and why it fits). Use only these "
              "colours and fonts. Change it only if the user asks for another look."),
    )
    return out
