"""Put Google Maps scraper results into the session's vault, so the LLM can recall them.

The LLM learns from these notes by retrieval (vault_recall / vault_search), not by training:
the notes are read at question time, so a fix or a new scrape shows up at once and nothing is
baked into model weights.

Privacy defaults (business listings can still carry personal data):
* Emails and phone numbers are NOT written unless ``keep_contacts=True`` is passed explicitly.
* Only the website's domain is kept, not the full URL with its tracking parameters.
* Reviews text is not imported. Only counts and rating.

Input: the CSV or JSON that gosom/google-maps-scraper writes with ``-results``. Column names
vary between versions, so the mapping below accepts several spellings.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_ALIASES = {
    "name": ("title", "name", "business_name"),
    "category": ("category", "categories", "type"),
    "address": ("address", "full_address"),
    "city": ("city",),
    "website": ("website", "web", "url"),
    "phone": ("phone", "phone_number", "telephone"),
    "emails": ("emails", "email"),
    "rating": ("review_rating", "rating", "stars"),
    "reviews": ("review_count", "reviews", "user_ratings_total"),
    "lat": ("latitude", "lat"),
    "lon": ("longitude", "lon", "lng"),
    "place_id": ("place_id", "cid", "data_id"),
}


def _pick(row: dict[str, Any], key: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k}
    for alias in _ALIASES[key]:
        v = lowered.get(alias)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def read_results(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        return [r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict)]
    return list(csv.DictReader(text.splitlines()))


def normalize(row: dict[str, Any], keep_contacts: bool = False) -> dict[str, Any] | None:
    name = _pick(row, "name")
    if not name:
        return None
    website = _pick(row, "website")
    domain = urlsplit(website if "://" in website else f"https://{website}").hostname if website else None
    out: dict[str, Any] = {
        "name": name,
        "category": _pick(row, "category"),
        "address": _pick(row, "address"),
        "city": _pick(row, "city"),
        "website_domain": (domain or "").removeprefix("www.") or None,
        "rating": _to_float(_pick(row, "rating")),
        "reviews": _to_int(_pick(row, "reviews")),
        "lat": _to_float(_pick(row, "lat")),
        "lon": _to_float(_pick(row, "lon")),
        "place_id": _pick(row, "place_id") or None,
    }
    if keep_contacts:
        out["phone"] = _pick(row, "phone") or None
        out["emails"] = _pick(row, "emails") or None
    return out


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _to_int(s: str) -> int | None:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:80] or "place"


def note_for(place: dict[str, Any], query: str) -> tuple[str, str]:
    """(vault path, markdown body) for one place."""
    path = f"places/{slug(query)}/{slug(place['name'])}"
    lines = [f"# {place['name']}", "", f"Query: {query}", ""]
    for label, key in (("Category", "category"), ("Address", "address"), ("City", "city"),
                       ("Website", "website_domain"), ("Rating", "rating"), ("Reviews", "reviews")):
        if place.get(key) not in (None, ""):
            lines.append(f"- {label}: {place[key]}")
    if "phone" in place and place["phone"]:
        lines.append(f"- Phone: {place['phone']}")
    if "emails" in place and place["emails"]:
        lines.append(f"- Emails: {place['emails']}")
    lines += ["", "Source: Google Maps (scraped with gosom/google-maps-scraper)."]
    return path, "\n".join(lines) + "\n"


async def import_results(path: str | Path, *, query: str, workspace: str, keep_contacts: bool = False,
                         limit: int = 500) -> dict[str, Any]:
    """Write one note per place and one summary note for the query into the session's vault."""
    from tool.execute.execute_local_memory.vault_tool import vault_write

    rows = read_results(path)
    written, skipped = 0, 0
    places: list[dict[str, Any]] = []
    for row in rows[:limit]:
        place = normalize(row, keep_contacts=keep_contacts)
        if place is None:
            skipped += 1
            continue
        places.append(place)
        note_path, body = note_for(place, query)
        res = await vault_write(workspace_path=workspace, path=note_path, body=body, note_type="concept")
        if res.get("ok", True):
            written += 1
        else:
            skipped += 1
    summary = _summary(places, query, keep_contacts)
    await vault_write(workspace_path=workspace, path=f"places/{slug(query)}/_summary", body=summary, note_type="concept")
    return {"ok": True, "written": written, "skipped": skipped, "query": query, "contacts_kept": keep_contacts}


def _summary(places: list[dict[str, Any]], query: str, keep_contacts: bool) -> str:
    rated = sorted([p for p in places if p.get("rating")], key=lambda p: (p["rating"], p.get("reviews") or 0), reverse=True)
    cats: dict[str, int] = {}
    for p in places:
        if p.get("category"):
            cats[p["category"]] = cats.get(p["category"], 0) + 1
    lines = [f"# Scrape summary: {query}", "", f"- Places: {len(places)}",
             f"- Contacts kept: {'yes' if keep_contacts else 'no (removed on import)'}", "", "## Top categories"]
    lines += [f"- {k}: {v}" for k, v in sorted(cats.items(), key=lambda kv: -kv[1])[:10]] or ["- (none)"]
    lines += ["", "## Highest rated (with at least one rating)"]
    lines += [f"- {p['name']}: {p['rating']} ({p.get('reviews') or 0} reviews)" for p in rated[:10]] or ["- (none)"]
    return "\n".join(lines) + "\n"


__all__ = ["read_results", "normalize", "note_for", "import_results", "slug"]
