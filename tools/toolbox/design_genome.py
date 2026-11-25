"""Design genome: a new, coherent design direction every time, out of millions of combinations.

``style_pick`` used to choose among a few dozen hand-written directions, so after a while the
same looks came back. A genome is assembled instead, from the parts in
``input/styles/genome.yaml``:

* a **personality** (editorial, warm craft, bold poster, luxury, tech, ...), chosen to fit the
  subject, which biases every other choice toward parts that belong together;
* a **palette** generated in OKLCH from a hue, a colour scheme (monochrome, analogous,
  complementary, split, triad accent) and a mode (light, paper, tinted, dark, bold). Every role
  is then pushed until it passes WCAG: body text 7:1, muted text 4.5:1, accent 3:1, text on the
  accent 4.5:1. A palette that cannot pass is not returned;
* a **font pair**, a **layout** for the medium, a **shape language**, two **motifs**, an
  **image treatment**, a **motion** style, a **density** and a **type scale**.

Parts that share tags with the personality are about eight times as likely as the rest, so
looks stay coherent but can still surprise. Recently used fonts, layouts and hue families
(across chats, see ``brain.utils.style_history``) are avoided.

A genome has an id like ``g03-5a1f9c`` (personality 3, seed 0x5a1f9c). The same id gives the
same palette, fonts, shapes and motifs in any medium, so a poster, its slides and its landing
page can share one look on purpose; only the layout follows the medium.
"""

from __future__ import annotations

import html
import math
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml

from brain.utils import style_history

GENOME_FILE = Path(__file__).resolve().parents[2] / "input" / "styles" / "genome.yaml"
ID_RE = re.compile(r"^g(\d{2})-([0-9a-f]{6})$")
_MATCH_WEIGHT = 3.0
_STRAY_WEIGHT = 0.35
_REROLLS = 10
# Purple-to-blue is the default look of AI-made pages. Not banned, just made rare unless the
# personality asks for that range on purpose.
_SLOP_HUES = (255.0, 295.0)
_SCHEMES = (("mono", 2), ("analogous", 3), ("complementary", 3), ("split", 2), ("triad", 1))
_SLOP_BASE_AVOID = (
    "purple-to-blue gradients, glassmorphism, Inter or Poppins by default, emoji or sparkle icons, three identical "
    "feature cards, every section the same height, 'Get started'/'Learn more' twins, fake stats or testimonials"
)


# ── data ───────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def data() -> dict[str, Any]:
    loaded = yaml.safe_load(GENOME_FILE.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def personalities() -> list[dict[str, Any]]:
    return list(data().get("personalities") or [])


def combinations(medium: str = "web") -> int:
    """How many distinct non-palette combinations there are for a medium (palettes add far more)."""
    d = data()
    layouts = len((d.get("layouts") or {}).get(medium) or [])
    motifs = len(d.get("motifs") or [])
    return (len(d.get("fonts") or []) * layouts * len(d.get("shapes") or []) * motifs * max(1, motifs - 1)
            * len(d.get("imagery") or []) * len(d.get("motion") or []) * len(d.get("density") or [])
            * len(d.get("scales") or []))


# ── colour: OKLCH to sRGB, contrast ──────────────────────────────────────────────
def _oklch_to_linear(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-1e-4 <= c <= 1 + 1e-4 for c in rgb)


def _encode(channel: float) -> int:
    channel = min(1.0, max(0.0, channel))
    value = 12.92 * channel if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055
    return round(value * 255)


def oklch_hex(lightness: float, chroma: float, hue: float) -> str:
    """A displayable colour: chroma is reduced until the colour fits in sRGB, keeping hue and lightness."""
    lightness = min(1.0, max(0.0, lightness))
    low, high = 0.0, max(0.0, chroma)
    if not _in_gamut(_oklch_to_linear(lightness, high, hue)):
        for _ in range(24):
            mid = (low + high) / 2
            if _in_gamut(_oklch_to_linear(lightness, mid, hue)):
                low = mid
            else:
                high = mid
        high = low
    r, g, b = (_encode(c) for c in _oklch_to_linear(lightness, high, hue))
    return f"#{r:02X}{g:02X}{b:02X}"


def luminance(hex_colour: str) -> float:
    text = hex_colour.lstrip("#")
    channels = [int(text[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _reach(target_ratio: float, against: str, lightness: float, chroma: float, hue: float, darker: bool) -> tuple[str, float]:
    """Move lightness toward black or white until the colour reaches the ratio against ``against``."""
    step = -0.01 if darker else 0.01
    value = lightness
    colour = oklch_hex(value, chroma, hue)
    while contrast(colour, against) < target_ratio and 0.0 < value < 1.0:
        value = min(1.0, max(0.0, value + step))
        colour = oklch_hex(value, chroma, hue)
    return colour, value


# ── palette ─────────────────────────────────────────────────────────────────────
def _hue(rng: random.Random, personality: dict[str, Any]) -> float:
    ranges = personality.get("hues") or [[0, 360]]
    widths = [max(1.0, float(b) - float(a)) for a, b in ranges]
    for _ in range(3):
        low, high = rng.choices(ranges, weights=widths, k=1)[0]
        hue = rng.uniform(float(low), float(high)) % 360
        in_slop = _SLOP_HUES[0] <= hue <= _SLOP_HUES[1]
        asked = any(float(a) >= _SLOP_HUES[0] - 10 and float(b) <= _SLOP_HUES[1] + 10 for a, b in ranges)
        if not in_slop or asked or rng.random() < 0.25:
            return hue
    return hue


def palette(rng: random.Random, personality: dict[str, Any]) -> dict[str, Any]:
    """Colour roles as hex, every pair a page needs readable."""
    hue = _hue(rng, personality)
    chroma = rng.uniform(*[float(x) for x in personality.get("chroma") or [0.06, 0.14]])
    mode = rng.choice(list(personality.get("modes") or ["light"]))
    scheme = rng.choices([s for s, _ in _SCHEMES], weights=[w for _, w in _SCHEMES], k=1)[0]
    second = {
        "mono": hue, "analogous": hue + rng.choice((-35, 35)), "complementary": hue + 180,
        "split": hue + rng.choice((150, 210)), "triad": hue + rng.choice((120, 240)),
    }[scheme] % 360

    dark = mode == "dark"
    if mode == "paper":
        bg_h = rng.uniform(65, 95)
        bg = oklch_hex(rng.uniform(0.945, 0.965), rng.uniform(0.018, 0.032), bg_h)
        surface = oklch_hex(0.985, 0.012, bg_h)
    elif mode == "tinted":
        bg_h = rng.choice((hue, second))
        bg = oklch_hex(rng.uniform(0.9, 0.94), rng.uniform(0.03, 0.06), bg_h)
        surface = oklch_hex(0.975, 0.015, bg_h)
    elif dark:
        bg_h = hue
        bg = oklch_hex(rng.uniform(0.16, 0.22), rng.uniform(0.01, 0.03), bg_h)
        surface = oklch_hex(0.25, 0.025, bg_h)
    elif mode == "bold":
        # The page is the colour: deep and vivid under white text, or bright and vivid under dark text.
        bg_h = hue
        deep = rng.random() < 0.5
        bg = oklch_hex(rng.uniform(0.36, 0.46) if deep else rng.uniform(0.8, 0.9), max(chroma, 0.14), bg_h)
        surface = oklch_hex(0.97, 0.015, bg_h)
    else:
        bg_h = hue
        bg = oklch_hex(rng.uniform(0.975, 0.99), rng.uniform(0.003, 0.012), bg_h)
        surface = oklch_hex(0.955, 0.012, bg_h)

    on_dark = dark or (mode == "bold" and luminance(bg) < 0.18)
    # A vivid page reads like a poster: AA (4.5:1) is the bar there, AAA (7:1) everywhere else.
    body_target = 4.5 if mode == "bold" else 7.0
    text, _ = _reach(7.0, bg, 0.93 if on_dark else 0.25, 0.02, bg_h, darker=not on_dark)
    muted, _ = _reach(4.5, bg, 0.72 if on_dark else 0.5, 0.025, bg_h, darker=not on_dark)
    line = oklch_hex(0.32 if on_dark else 0.88, 0.015, bg_h)

    if mode == "bold":
        # The page itself is the colour; the accent is a contrasting second colour.
        accent, _ = _reach(3.0, bg, 0.92 if on_dark else 0.3, chroma * 0.6, second, darker=not on_dark)
    else:
        accent, _ = _reach(3.0, bg, 0.72 if on_dark else 0.56, chroma, hue, darker=not on_dark)
    accent_text, _ = _reach(4.5, bg, 0.75 if on_dark else 0.5, chroma, hue if mode != "bold" else second,
                            darker=not on_dark)
    accent_ink = max(("#FFFFFF", text, "#111111"), key=lambda c: contrast(c, accent))
    if contrast(accent_ink, accent) < 4.5:
        accent, _ = _reach(4.5, accent_ink, 0.5, chroma, hue, darker=accent_ink.upper() == "#FFFFFF")
    accent2 = oklch_hex(0.7 if on_dark else 0.6, chroma * 0.8, second)

    roles = {"bg": bg, "surface": surface, "text": text, "muted": muted, "line": line, "accent": accent,
             "accent_ink": accent_ink, "accent_text": accent_text, "accent2": accent2}
    checks = {
        "text_on_bg": round(contrast(text, bg), 2), "muted_on_bg": round(contrast(muted, bg), 2),
        "accent_on_bg": round(contrast(accent, bg), 2), "ink_on_accent": round(contrast(accent_ink, accent), 2),
        "link_on_bg": round(contrast(accent_text, bg), 2), "text_on_surface": round(contrast(text, surface), 2),
    }
    return {"mode": mode, "scheme": scheme, "hue": round(hue), "roles": roles, "contrast": checks,
            "passes": checks["text_on_bg"] >= body_target and checks["muted_on_bg"] >= 4.5 and checks["accent_on_bg"] >= 3
            and checks["ink_on_accent"] >= 4.5 and checks["link_on_bg"] >= 4.5 and checks["text_on_surface"] >= 4.5}


# ── assembly ────────────────────────────────────────────────────────────────────
def _weighted(rng: random.Random, options: list[dict[str, Any]], tags: set[str],
              recent: set[str] | frozenset[str] = frozenset(), key: str = "id") -> dict[str, Any]:
    pool = [o for o in options if str(o.get(key)) not in recent] or options
    weights = [_MATCH_WEIGHT * (1 + len(tags & set(o.get("tags") or []))) if tags & set(o.get("tags") or [])
               else _STRAY_WEIGHT for o in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def personality_for(subject: str, rng: random.Random) -> int:
    """Index of a personality that fits the subject, weighted by how many of its words appear."""
    people = personalities()
    lowered = (subject or "").lower()
    scores = [sum(1 for w in p.get("fits") or [] if re.search(rf"(?<![a-z0-9]){re.escape(str(w).lower())}", lowered))
              for p in people]
    if any(scores) and rng.random() < 0.85:
        return rng.choices(range(len(people)), weights=[s * 3 if s else 0.2 for s in scores], k=1)[0]
    return rng.randrange(len(people))


def new_id(subject: str = "", rng: random.Random | None = None) -> str:
    rng = rng or random.SystemRandom()
    return f"g{personality_for(subject, rng):02d}-{rng.randrange(1 << 24):06x}"


def is_genome_id(value: str) -> bool:
    return bool(ID_RE.match((value or "").strip().lower()))


def fonts_css(heading: dict[str, Any] | str, body_weights: str = "", heading_weights: str = "",
              body: str = "") -> str:
    """Google Fonts link asking only for weights each family has."""
    if isinstance(heading, dict):
        pair = heading
        families = [(pair["heading"], str(pair.get("hw") or "400")), (pair["body"], str(pair.get("bw") or "400"))]
    else:
        families = [(heading, heading_weights or "400"), (body, body_weights or "400")]
    merged: dict[str, set[int]] = {}
    for family, weights in families:
        merged.setdefault(family, set()).update(int(w) for w in weights.split(";") if w.strip().isdigit())
    query = "&".join(f"family={quote_plus(f)}:wght@{';'.join(str(w) for w in sorted(ws))}" for f, ws in merged.items())
    return f"https://fonts.googleapis.com/css2?{query}&display=swap"


# How many recent values of each axis to steer away from: enough to feel new, not so many that
# a small axis (12 hue families) runs out of choices.
_RECENT = {"font": 6, "hue": 3, "layout": 4}


def _recent_sets(medium: str) -> dict[str, set[str]]:
    return {
        "font": set(style_history.recent("genome-font")[-_RECENT["font"]:]),
        "hue": set(style_history.recent("genome-hue")[-_RECENT["hue"]:]),
        "layout": set(style_history.recent(f"genome-layout-{medium}")[-_RECENT["layout"]:]),
    }


def build(genome_id: str, medium: str = "web", avoid_recent: bool = True) -> dict[str, Any]:
    """The full direction for a genome id in a medium. Raises ValueError for an unknown id."""
    match = ID_RE.match(genome_id.strip().lower())
    if not match:
        raise ValueError(f"not a genome id: {genome_id}")
    index, seed = int(match.group(1)), int(match.group(2), 16)
    people = personalities()
    if index >= len(people):
        raise ValueError(f"no personality {index}")
    person = people[index]
    tags = set(person.get("tags") or [])
    d = data()
    recent = _recent_sets(medium) if avoid_recent else {"font": set(), "hue": set(), "layout": set()}

    rng = random.Random(seed)
    colours = palette(rng, person)
    for attempt in range(_REROLLS * 3):
        fresh_hue = str(colours["hue"] // 30) not in recent["hue"] or attempt >= _REROLLS
        if colours["passes"] and fresh_hue:
            break
        colours = palette(rng, person)
    if not colours["passes"]:
        raise ValueError(f"{genome_id}: no readable palette found")
    fonts = _weighted(rng, list(d.get("fonts") or []), tags, recent["font"], key="heading")
    shape = _weighted(rng, list(d.get("shapes") or []), tags)
    motif_pool = list(d.get("motifs") or [])
    motif = _weighted(rng, motif_pool, tags)
    second_motif = _weighted(rng, [m for m in motif_pool if m["id"] != motif["id"]], tags)
    imagery = _weighted(rng, list(d.get("imagery") or []), tags)
    motion = _weighted(rng, list(d.get("motion") or []), tags)
    density = _weighted(rng, list(d.get("density") or []), tags)
    scale = rng.choice(list(d.get("scales") or [{"ratio": 1.25, "name": "Major third"}]))
    # The layout comes from its own stream, so the same genome keeps its look in every medium.
    layouts = list((d.get("layouts") or {}).get(medium) or (d.get("layouts") or {}).get("web") or [])
    layout = _weighted(random.Random(f"{seed}-{medium}"), layouts, tags, recent["layout"])

    energy = int(motion.get("energy") or 2)
    rhythm = 3 if layout["id"] in {"asymmetric-grid", "sticker-collage", "magazine-spread", "card-stack",
                                   "horizontal-strip", "poster-type", "collage", "diagonal-cut"} else (
        1 if layout["id"] in {"editorial-column", "letter", "terminal-doc", "memo-plain", "index-list"} else 2)
    roles = colours["roles"]
    out: dict[str, Any] = {
        "style": genome_id,
        "source": "genome",
        "name": f"{person['name']} · {fonts['heading']} / {fonts['body']} · {layout['name']}",
        "medium": medium,
        "personality": person["name"],
        "palette": roles,
        "palette_notes": f"{colours['mode']} mode, {colours['scheme']} scheme around hue {colours['hue']}°",
        "contrast": colours["contrast"],
        "css_tokens": ":root{" + ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in roles.items()) + "}",
        "type": {"heading": fonts["heading"], "body": fonts["body"], "heading_weights": fonts.get("hw"),
                 "scale": scale["name"], "scale_ratio": scale["ratio"]},
        "layout": f"{layout['name']}: {layout['recipe']}",
        "shape": f"{shape['name']}: radius {shape['radius']}; border {shape['border']}; shadow {shape['shadow']}",
        "motifs": f"{motif['text']}; {second_motif['text'].lower()}",
        "imagery": imagery["text"],
        "density": density["text"],
        "design_read": f"ENERGY {min(3, max(1, energy))} / RHYTHM {rhythm} / MOTION {min(3, max(1, energy))}",
        "avoid": _SLOP_BASE_AVOID,
        "_axes": {"font": fonts["heading"], "hue": str(colours["hue"] // 30), "layout": layout["id"]},
    }
    if medium in ("web", "video", "image"):
        out["fonts_css"] = fonts_css(fonts)
    if medium in ("web", "video"):
        out["motion"] = motion["text"]
    if medium == "image":
        out["image_recipe"] = (
            f"{layout['recipe']} {imagery['text']}. Palette only: {roles['bg']} background, {roles['text']} type, "
            f"{roles['accent']} accent, {roles['accent2']} second colour. {motif['text']}. One natural imperfection."
        )
    if medium in ("slides", "document"):
        theme = nearest_office_theme(roles["accent"], colours["mode"] == "dark")
        out["office_theme"] = theme
        out["accent_color"] = roles["accent"]
        out["how"] = (f"pass theme='{theme}' and accent_color='{roles['accent']}' to slides_create/doc_create; "
                      "follow the layout notes for structure")
    return out


def remember(direction: dict[str, Any]) -> None:
    """Note the axes used, so the next genomes in any chat differ from this one."""
    axes = direction.get("_axes") or {}
    medium = direction.get("medium", "web")
    if axes.get("font"):
        style_history.remember("genome-font", axes["font"])
    if axes.get("hue"):
        style_history.remember("genome-hue", axes["hue"])
    if axes.get("layout"):
        style_history.remember(f"genome-layout-{medium}", axes["layout"])


def public(direction: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in direction.items() if not k.startswith("_")}


def nearest_office_theme(accent: str, dark: bool) -> str:
    """The office theme whose main colour is closest in hue to the accent (dark only for dark looks)."""
    from . import _themes

    def hue_of(hex_colour: str) -> float:
        r, g, b = (int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        high, low = max(r, g, b), min(r, g, b)
        if high == low:
            return 0.0
        if high == r:
            h = (g - b) / (high - low) % 6
        elif high == g:
            h = (b - r) / (high - low) + 2
        else:
            h = (r - g) / (high - low) + 4
        return h * 60

    target = hue_of(accent)
    candidates = [t for t in _themes.THEMES.values() if t.dark == dark] or list(_themes.THEMES.values())
    return min(candidates, key=lambda t: min(abs(hue_of(t.primary) - target), 360 - abs(hue_of(t.primary) - target))).name


# ── reference sheet ─────────────────────────────────────────────────────────────
def gallery_html(directions: list[dict[str, Any]], subject: str) -> str:
    """One page showing each direction as a small live specimen, to choose from."""
    esc = html.escape
    cards = []
    links = []
    for d in directions:
        r = d["palette"]
        heading, body = d["type"]["heading"], d["type"]["body"]
        if d.get("fonts_css"):
            links.append(f'<link rel="stylesheet" href="{esc(d["fonts_css"])}">')
        else:
            links.append(f'<link rel="stylesheet" href="{esc(fonts_css({"heading": heading, "body": body, "hw": d["type"].get("heading_weights") or "400", "bw": "400"}))}">')
        radius = "999px" if "round" in d["shape"].lower() else ("0" if "radius 0" in d["shape"] else "8px")
        swatches = "".join(
            f'<span class="sw" style="background:{v}" title="{k} {v}"><i>{esc(k)}<br>{v}</i></span>'
            for k, v in r.items() if k in ("bg", "surface", "text", "accent", "accent2", "muted"))
        cards.append(f"""
<article class="card" style="--bg:{r['bg']};--surface:{r['surface']};--text:{r['text']};--muted:{r['muted']};--line:{r['line']};--accent:{r['accent']};--ink:{r['accent_ink']};--link:{r['accent_text']};--accent2:{r['accent2']};--r:{radius};--hf:'{esc(heading)}';--bf:'{esc(body)}'">
  <div class="specimen">
    <p class="kicker">{esc(d['personality'])}</p>
    <h2>{esc(subject or 'Judul yang jelas dan spesifik')}</h2>
    <p class="body">Teks isi memakai {esc(body)}. Ukuran nyaman dibaca, kontras teks {d['contrast']['text_on_bg']}:1 terhadap latar.</p>
    <p class="row"><a class="btn" href="#">Aksi utama</a><a class="link" href="#">tautan teks</a></p>
  </div>
  <div class="swatches">{swatches}</div>
  <dl class="meta">
    <dt>Kode</dt><dd><code>{esc(d['style'])}</code></dd>
    <dt>Font</dt><dd>{esc(heading)} / {esc(body)} · {esc(d['type']['scale'])}</dd>
    <dt>Tata letak</dt><dd>{esc(d['layout'])}</dd>
    <dt>Bentuk</dt><dd>{esc(d['shape'])}</dd>
    <dt>Motif</dt><dd>{esc(d['motifs'])}</dd>
    <dt>Gambar</dt><dd>{esc(d['imagery'])}</dd>
    <dt>Dial</dt><dd>{esc(d['design_read'])} · {esc(d['palette_notes'])}</dd>
  </dl>
</article>""")
    return f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Referensi desain: {esc(subject or 'arah desain')}</title>
{''.join(dict.fromkeys(links))}
<style>
body{{margin:0;padding:24px 16px 48px;background:#f3f2ee;color:#1d1c1a;font:15px/1.5 system-ui,sans-serif}}
header{{max-width:1200px;margin:0 auto 20px}} header h1{{font-size:22px;margin:0 0 4px}} header p{{margin:0;color:#57544e}}
.grid{{max-width:1200px;margin:0 auto;display:grid;gap:18px;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr))}}
.card{{background:#fff;border:1px solid #dedad2;border-radius:12px;overflow:hidden;display:flex;flex-direction:column}}
.specimen{{background:var(--bg);color:var(--text);padding:22px 20px;font-family:var(--bf),sans-serif}}
.kicker{{margin:0 0 6px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}}
.specimen h2{{font-family:var(--hf),serif;font-size:30px;line-height:1.1;margin:0 0 10px;overflow-wrap:anywhere}}
.body{{margin:0 0 14px;color:var(--text)}} .row{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:0}}
.btn{{background:var(--accent);color:var(--ink);padding:9px 16px;border-radius:var(--r);text-decoration:none;font-weight:600}}
.link{{color:var(--link);text-decoration:underline;text-underline-offset:3px}}
.swatches{{display:flex;height:46px;border-top:1px solid #dedad2}} .sw{{flex:1;position:relative}}
.sw i{{position:absolute;inset:auto 0 0 0;font:10px/1.2 ui-monospace,monospace;font-style:normal;padding:3px;background:rgba(255,255,255,.85);color:#111;opacity:0;transition:opacity .15s}}
.sw:hover i,.sw:focus-within i{{opacity:1}}
.meta{{margin:0;padding:14px 16px;display:grid;grid-template-columns:84px 1fr;gap:6px 10px;font-size:13px}}
.meta dt{{color:#6b675f}} .meta dd{{margin:0}} code{{font:12px ui-monospace,monospace;background:#f0eee8;padding:1px 5px;border-radius:4px}}
@media (prefers-reduced-motion:reduce){{.sw i{{transition:none}}}}
</style></head>
<body><header><h1>{len(directions)} arah desain untuk: {esc(subject or 'tanpa subjek')}</h1>
<p>Pilih satu dengan menyebut kodenya, misalnya <code>{esc(directions[0]['style']) if directions else ''}</code>. Setiap palet sudah lolos kontras WCAG.</p></header>
<main class="grid">{''.join(cards)}</main></body></html>"""


__all__ = ["build", "combinations", "contrast", "data", "fonts_css", "gallery_html", "is_genome_id", "new_id",
           "oklch_hex", "palette", "personalities", "public", "remember"]
