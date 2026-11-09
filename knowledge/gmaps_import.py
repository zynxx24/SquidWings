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
    "name": ("title", "name", "business_name", "place_name"),
    "category": ("category", "categories", "type", "main_category"),
    "address": ("address", "full_address", "complete_address"),
    "city": ("city", "kota", "municipality"),
    "website": ("website", "web", "site", "url"),
    "phone": ("phone", "phone_number", "telephone", "international_phone_number"),
    "emails": ("emails", "email"),
    "rating": ("review_rating", "rating", "stars", "total_score"),
    "reviews": ("review_count", "reviews", "user_ratings_total", "reviews_count"),
    "lat": ("latitude", "lat"),
    # gosom/google-maps-scraper spells this column "longtitude".
    "lon": ("longitude", "longtitude", "lon", "lng"),
    "place_id": ("place_id", "cid", "data_id", "google_id"),
    "maps_url": ("link", "google_maps_url", "maps_url", "place_url"),
    "status": ("status", "business_status"),
    "price_range": ("price_range", "price", "price_level"),
    "plus_code": ("plus_code",),
}


def _flat(v: Any) -> str:
    """JSON results hold lists and objects (categories, complete_address); flatten them to text."""
    if isinstance(v, list):
        return ", ".join(_flat(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return ", ".join(_flat(x) for x in v.values() if x not in (None, ""))
    return str(v).strip()


def _pick(row: dict[str, Any], key: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k}
    for alias in _ALIASES[key]:
        v = lowered.get(alias)
        if v not in (None, "", [], {}):
            text = _flat(v)
            if text:
                return text
    return ""


def read_results(path: str | Path) -> list[dict[str, Any]]:
    """Rows of a CSV, a JSON array, or JSON Lines (gosom writes one object per line with -json)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if p.suffix.lower() in (".json", ".jsonl", ".ndjson") or stripped.startswith(("[", "{")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        return [r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict)]
    # Excel-exported CSVs in Indonesia often use ';'.
    first = text.splitlines()[0] if text else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


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
        "maps_url": _pick(row, "maps_url") or None,
        "status": _pick(row, "status") or None,
        "price_range": _pick(row, "price_range") or None,
    }
    if out["city"] == "" and out["address"]:
        out["city"] = _city_from_address(out["address"])
    if keep_contacts:
        out["phone"] = _pick(row, "phone") or None
        out["emails"] = _pick(row, "emails") or None
    return out


def _city_from_address(address: str) -> str:
    """Best guess at the city in an Indonesian address: 'Kota X' / 'Kabupaten X', else the part before the province."""
    m = re.search(r"\b(?:Kota|Kabupaten|Kab\.)\s+([A-Z][\w .'-]+?)(?:,|\s\d{5}|$)", address)
    if m:
        return m.group(1).strip()
    parts = [p.strip() for p in address.split(",") if p.strip()]
    for part in reversed(parts[:-1] if len(parts) > 2 else parts):
        cleaned = re.sub(r"\b\d{5}\b", "", part).strip()
        if cleaned and not re.search(r"\d", cleaned) and len(cleaned) < 40:
            return cleaned
    return ""


def _to_float(s: str) -> float | None:
    try:
        # "4,7" from a localised export.
        return float(str(s).replace(",", ".")) if str(s).count(",") == 1 and "." not in str(s) else float(s)
    except (TypeError, ValueError):
        return None


def _to_int(s: str) -> int | None:
    try:
        return int(float(str(s).replace(".", "").replace(",", ""))) if re.fullmatch(r"\d{1,3}([.,]\d{3})+", str(s)) else int(float(s))
    except (TypeError, ValueError):
        return None


def dedupe(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per place: by place_id, else by name and address. A scraper that pages twice repeats rows."""
    seen: set[str] = set()
    out = []
    for place in places:
        key = place.get("place_id") or f"{place['name'].lower()}|{(place.get('address') or '').lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(place)
    return out


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:80] or "place"


def note_for(place: dict[str, Any], query: str, taken: set[str] | None = None) -> tuple[str, str]:
    """(vault path, markdown body) for one place. Branches with the same name get distinct notes."""
    base = f"places/{slug(query)}/{slug(place['name'])}"
    path = base
    if taken is not None:
        suffix = slug(place.get("address") or "")[:24] or None
        if path in taken and suffix:
            path = f"{base}-{suffix}"
        n = 2
        while path in taken:
            path = f"{base}-{n}"
            n += 1
        taken.add(path)
    lines = [f"# {place['name']}", "", f"Query: {query}", ""]
    for label, key in (("Category", "category"), ("Address", "address"), ("City", "city"),
                       ("Website", "website_domain"), ("Rating", "rating"), ("Reviews", "reviews"),
                       ("Price", "price_range"), ("Status", "status"), ("Google Maps", "maps_url")):
        if place.get(key) not in (None, ""):
            lines.append(f"- {label}: {place[key]}")
    if place.get("lat") is not None and place.get("lon") is not None:
        lines.append(f"- Coordinates: {place['lat']}, {place['lon']}")
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
    for row in rows:
        place = normalize(row, keep_contacts=keep_contacts)
        if place is None:
            skipped += 1
            continue
        places.append(place)
    before = len(places)
    places = dedupe(places)
    duplicates = before - len(places)
    taken: set[str] = set()
    for place in places[:limit]:
        note_path, body = note_for(place, query, taken)
        res = await vault_write(workspace_path=workspace, path=note_path, body=body, note_type="concept")
        if res.get("ok", True):
            written += 1
        else:
            skipped += 1
    summary = _summary(places, query, keep_contacts)
    await vault_write(workspace_path=workspace, path=f"places/{slug(query)}/_summary", body=summary, note_type="concept")
    return {"ok": True, "written": written, "skipped": skipped, "duplicates": duplicates, "query": query,
            "contacts_kept": keep_contacts, "places": places}


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


__all__ = ["dedupe", "import_results", "normalize", "note_for", "read_results", "slug"]
