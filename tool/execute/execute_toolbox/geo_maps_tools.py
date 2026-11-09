"""Maps and places: Google Maps scraper results, distances, coordinates, map links, and OpenStreetMap lookups.

Google Maps results (from gosom/google-maps-scraper, or any CSV/JSON export) are imported
once per session with ``gmaps_import``. They land in two places:

* ``.agent_memory/places/<dataset>.json`` in the session workspace, for exact filtering
  (``places_search``, ``places_stats``, ``places_export``), and
* the session vault, so ``vault_recall`` finds them in later turns.

Contacts (phones, emails) are dropped unless the user explicitly asks to keep them.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from knowledge import gmaps_import

from . import _files, _net
from .catalog import group, param, spec

group("maps", "Maps, places and locations",
      "google maps, gmaps, maps, peta, map, lokasi, location, tempat, place, places, alamat, address, koordinat, "
      "coordinates, latitude, longitude, lat, lon, jarak, distance, terdekat, nearest, nearby, sekitar, around, rute, "
      "route, arah, directions, navigasi, geocode, scrape maps, scraper, leads, bisnis lokal, local business, toko, "
      "restoran, kafe, cafe, klinik, hotel, km dari")

_EARTH_KM = 6371.0088
_DATASET_DIR = Path(".agent_memory") / "places"


# ── Coordinates ───────────────────────────────────────────────────────────────
def parse_point(value: Any) -> tuple[float, float]:
    """'-6.2, 106.8', [lat, lon], {lat, lon}, or DMS like 6°10'S 106°49'E."""
    if isinstance(value, dict):
        lat, lon = value.get("lat", value.get("latitude")), value.get("lon", value.get("lng", value.get("longitude")))
        if lat is None or lon is None:
            raise ValueError("a point object needs lat and lon")
        return _check(float(lat), float(lon))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return _check(float(value[0]), float(value[1]))
    text = str(value).strip()
    if "°" in text or re.search(r"\d\s*[NSEWUTBLnsewutbl]\b", text):
        found = re.findall(r"(-?\d+(?:\.\d+)?)\s*°\s*(?:(\d+(?:\.\d+)?)\s*['′]\s*)?(?:(\d+(?:\.\d+)?)\s*[\"″]\s*)?([NSEWUTBL])?", text, re.I)
        if len(found) >= 2:
            values = []
            for deg, mins, secs, hemi in found[:2]:
                v = abs(float(deg)) + float(mins or 0) / 60 + float(secs or 0) / 3600
                negative = deg.startswith("-") or hemi.upper() in ("S", "W", "B", "L")
                values.append(-v if negative else v)
            return _check(values[0], values[1])
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    if len(numbers) >= 2:
        return _check(float(numbers[0]), float(numbers[1]))
    raise ValueError(f"cannot read coordinates from '{value}'; use 'lat, lon'")


def _check(lat: float, lon: float) -> tuple[float, float]:
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        if -90 <= lon <= 90 and -180 <= lat <= 180:
            raise ValueError(f"({lat}, {lon}) looks like (lon, lat); give latitude first")
        raise ValueError(f"({lat}, {lon}) is outside valid coordinates")
    return lat, lon


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(h))


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
_ARAH = ["utara", "utara-timur laut", "timur laut", "timur-timur laut", "timur", "timur-tenggara", "tenggara",
         "selatan-tenggara", "selatan", "selatan-barat daya", "barat daya", "barat-barat daya", "barat",
         "barat-barat laut", "barat laut", "utara-barat laut"]


@spec("geo_distance", "Great-circle distance between two or more coordinates (a route through the points in order), with "
      "bearing/direction and a rough travel time by walking, motorbike, car and plane. Straight-line, not road distance.",
      "maps", [param("points", "list", "Coordinates: ['-6.2,106.8', '-6.9,107.6'] or [[lat, lon], ...].", items="str"),
               param("road_factor", "num", "Multiply by this to estimate road distance (1.3 is typical in cities).",
                 required=False, default=1.0)],
      keywords="jarak, distance, berapa km, how far, seberapa jauh, haversine, jarak lurus", lead=True)
def geo_distance(points: list[Any], road_factor: float = 1.0) -> dict[str, Any]:
    pts = [parse_point(p) for p in points]
    if len(pts) < 2:
        raise ValueError("give at least two points")
    legs, total = [], 0.0
    for a, b in zip(pts, pts[1:], strict=False):
        d = haversine(a, b)
        total += d
        brg = bearing(a, b)
        legs.append({"from": a, "to": b, "km": round(d, 3), "bearing_deg": round(brg, 1),
                     "direction": _COMPASS[round(brg / 22.5) % 16], "arah": _ARAH[round(brg / 22.5) % 16]})
    est = total * float(road_factor)
    speeds = {"walk": 5, "bicycle": 15, "motorbike_city": 30, "car_city": 25, "car_highway": 80, "plane": 750}
    return {"total_km": round(total, 3), "estimated_road_km": round(est, 2), "legs": legs,
            "rough_hours": {mode: round(est / v, 2) for mode, v in speeds.items()},
            "note": "straight-line distance; real roads and traffic take longer"}


@spec("coord_convert", "Convert coordinates between decimal degrees and degrees-minutes-seconds (DMS), and give a Google "
      "Maps link and plus-code-free OpenStreetMap link for them.",
      "maps", [param("value", description="'-6.1754, 106.8272' or '6°10\\'31.4\"S 106°49\\'37.9\"E'.")],
      keywords="koordinat, coordinates, dms, derajat menit detik, decimal degrees, lintang, bujur, latitude, longitude")
def coord_convert(value: str) -> dict[str, Any]:
    lat, lon = parse_point(value)

    def dms(v: float, pos: str, neg: str) -> str:
        hemi = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = (v - d - m / 60) * 3600
        return f"{d}°{m:02d}'{s:04.1f}\"{hemi}"

    return {"decimal": f"{lat:.6f}, {lon:.6f}", "dms": f"{dms(lat, 'N', 'S')} {dms(lon, 'E', 'W')}",
            "lat": lat, "lon": lon, "google_maps": f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}",
            "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}"}


@spec("geo_area", "Midpoint of several coordinates, their bounding box, and a bounding box of a radius around one point "
      "(useful for searching an area).",
      "maps", [param("points", "list", items="str"), param("radius_km", "num", "For one point: box of this radius.", required=False)],
      keywords="titik tengah, midpoint, bounding box, bbox, area pencarian, radius, tengah-tengah, meet in the middle")
def geo_area(points: list[Any], radius_km: float | None = None) -> dict[str, Any]:
    pts = [parse_point(p) for p in points]
    if not pts:
        raise ValueError("give at least one point")
    x = y = z = 0.0
    for lat, lon in pts:
        la, lo = math.radians(lat), math.radians(lon)
        x += math.cos(la) * math.cos(lo)
        y += math.cos(la) * math.sin(lo)
        z += math.sin(la)
    n = len(pts)
    mid = (math.degrees(math.atan2(z / n, math.hypot(x / n, y / n))), math.degrees(math.atan2(y / n, x / n)))
    out: dict[str, Any] = {"midpoint": [round(mid[0], 6), round(mid[1], 6)],
                           "bbox": {"south": min(p[0] for p in pts), "north": max(p[0] for p in pts),
                                    "west": min(p[1] for p in pts), "east": max(p[1] for p in pts)}}
    if radius_km:
        dlat = float(radius_km) / 110.574
        dlon = float(radius_km) / (111.320 * max(math.cos(math.radians(pts[0][0])), 1e-6))
        out["radius_bbox"] = {"south": pts[0][0] - dlat, "north": pts[0][0] + dlat, "west": pts[0][1] - dlon, "east": pts[0][1] + dlon}
    if n > 1:
        out["farthest_from_midpoint_km"] = round(max(haversine(mid, p) for p in pts), 3)
    return out


@spec("maps_link", "Build Google Maps links: search a place, open coordinates, open a place id, or directions from A to B "
      "(with travel mode and stops). Links only; nothing is fetched.",
      "maps", [param("query", description="Place or address to search, or the destination.", required=False),
               param("origin", description="Start (address or 'lat,lon') for directions.", required=False),
               param("waypoints", "list", "Stops between origin and destination.", required=False, items="str"),
               param("mode", enum=["driving", "walking", "bicycling", "transit", "two-wheeler"], required=False, default="driving"),
               param("place_id", required=False), param("lat", "num", required=False), param("lon", "num", required=False)],
      keywords="link maps, link google maps, share lokasi, kirim lokasi, petunjuk arah, rute, directions, navigasi, shareloc", lead=True)
def maps_link(query: str | None = None, origin: str | None = None, waypoints: list[str] | None = None, mode: str = "driving",
              place_id: str | None = None, lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
    base = "https://www.google.com/maps"
    if origin and query:
        url = f"{base}/dir/?api=1&origin={quote_plus(origin)}&destination={quote_plus(query)}&travelmode={mode}"
        if waypoints:
            url += "&waypoints=" + quote_plus("|".join(waypoints))
        return {"directions": url}
    if lat is not None and lon is not None:
        lat_, lon_ = _check(float(lat), float(lon))
        return {"pin": f"{base}/search/?api=1&query={lat_},{lon_}"}
    if place_id:
        return {"place": f"{base}/search/?api=1&query={quote_plus(query or 'place')}&query_place_id={quote_plus(place_id)}"}
    if query:
        return {"search": f"{base}/search/?api=1&query={quote_plus(query)}"}
    raise ValueError("give query, origin+query, lat+lon, or place_id")


# ── Places datasets (Google Maps scrapes) ─────────────────────────────────────
def _dataset_dir(workspace_path: str | None) -> Path:
    return _files.workspace(workspace_path) / _DATASET_DIR


def _save_dataset(workspace_path: str | None, name: str, query: str, places: list[dict[str, Any]], source: str) -> Path:
    folder = _dataset_dir(workspace_path)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{gmaps_import.slug(name)}.json"
    payload = {"name": gmaps_import.slug(name), "query": query, "source": source, "imported_at": int(time.time()),
               "count": len(places), "places": places}
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target


def _datasets(workspace_path: str | None) -> list[dict[str, Any]]:
    folder = _dataset_dir(workspace_path)
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _load(dataset: str | None, workspace_path: str | None) -> list[dict[str, Any]]:
    sets = _datasets(workspace_path)
    if not sets:
        raise ValueError("no places imported in this session yet; call gmaps_import with the scraper's CSV/JSON first")
    if not dataset or dataset in ("all", "*"):
        merged: list[dict[str, Any]] = []
        for s in sets:
            merged.extend({**p, "dataset": s["name"]} for p in s["places"])
        return gmaps_import.dedupe(merged)
    wanted = gmaps_import.slug(dataset)
    for s in sets:
        if s["name"] == wanted or gmaps_import.slug(s.get("query", "")) == wanted:
            return [{**p, "dataset": s["name"]} for p in s["places"]]
    raise ValueError(f"no dataset '{dataset}'; have {', '.join(s['name'] for s in sets)}")


@spec("gmaps_import", "Import Google Maps results (CSV, JSON, or JSON Lines from gosom/google-maps-scraper or any export) "
      "from the workspace into this session: cleans and de-duplicates places, saves a searchable dataset, and writes vault "
      "notes so later answers can recall them. Phones and emails are dropped unless keep_contacts is true and the user asked.",
      "maps", [param("path", description="The results file in the workspace."),
               param("query", description="What was searched, e.g. 'klinik gigi bandung'. Names the dataset."),
               param("keep_contacts", "bool", "Keep phone numbers and emails (only if the user explicitly asks).", required=False, default=False),
               param("to_vault", "bool", "Also write one vault note per place.", required=False, default=True),
               param("vault_limit", "int", "Most notes to write.", required=False, default=200)],
      keywords="import maps, import google maps, hasil scrape, scrape result, hasil scraper, csv google maps, gmaps csv, "
               "masukkan data maps, import tempat", lead=True, workspace=True, read_only=False)
async def gmaps_import_tool(path: str, query: str, keep_contacts: bool = False, to_vault: bool = True, vault_limit: int = 200,
                            workspace_path: str | None = None) -> dict[str, Any]:
    source = _files.source(path, workspace_path)
    root = str(_files.workspace(workspace_path))
    if to_vault:
        res = await gmaps_import.import_results(source, query=query, workspace=root, keep_contacts=keep_contacts,
                                                limit=max(0, min(int(vault_limit), 1000)))
        places = res.pop("places")
    else:
        rows = await asyncio.to_thread(gmaps_import.read_results, source)
        normalised = [p for p in (gmaps_import.normalize(r, keep_contacts) for r in rows) if p]
        places = gmaps_import.dedupe(normalised)
        res = {"written": 0, "skipped": len(rows) - len(normalised), "duplicates": len(normalised) - len(places)}
    target = _save_dataset(workspace_path, query, query, places, _files.relative(source, workspace_path))
    with_coords = sum(1 for p in places if p.get("lat") is not None and p.get("lon") is not None)
    return {"dataset": target.stem, "places": len(places), "with_coordinates": with_coords,
            "vault_notes": res.get("written", 0), "skipped_rows": res.get("skipped", 0),
            "duplicates_removed": res.get("duplicates", 0), "contacts_kept": keep_contacts,
            "next": "places_search to filter, places_stats for a summary, places_export to save a list"}


def _filter(places: list[dict[str, Any]], text: str | None, category: str | None, city: str | None,
            min_rating: float | None, min_reviews: int | None, has_website: bool | None, open_only: bool) -> list[dict[str, Any]]:
    words = [w for w in re.findall(r"\w+", (text or "").lower()) if len(w) > 1]
    out = []
    for p in places:
        hay = " ".join(str(p.get(k) or "") for k in ("name", "category", "address", "city", "website_domain")).lower()
        if words and not all(w in hay for w in words):
            continue
        if category and category.lower() not in str(p.get("category") or "").lower():
            continue
        if city and city.lower() not in f"{p.get('city') or ''} {p.get('address') or ''}".lower():
            continue
        if min_rating is not None and (p.get("rating") or 0) < float(min_rating):
            continue
        if min_reviews is not None and (p.get("reviews") or 0) < int(min_reviews):
            continue
        if has_website is not None and bool(p.get("website_domain")) != bool(has_website):
            continue
        if open_only and str(p.get("status") or "").lower() in ("closed", "permanently closed", "tutup permanen", "closed_permanently"):
            continue
        out.append(p)
    return out


async def _near_point(near: str | None) -> tuple[float, float] | None:
    if not near:
        return None
    try:
        return parse_point(near)
    except ValueError:
        found = await _geocode(near, 1)
        if not found:
            raise ValueError(f"could not find '{near}' on the map; give coordinates instead") from None
        return found[0]["lat"], found[0]["lon"]


@spec("places_search", "Search imported Google Maps places in this session: by words, category, city, minimum rating or "
      "review count, website or not, and distance from a point or address. Sort by rating, reviews, distance, or a score "
      "that balances rating with review count.",
      "maps", [param("text", description="Words that must appear (name, category, address).", required=False),
               param("dataset", description="Dataset name (default: all imported).", required=False),
               param("category", required=False), param("city", required=False),
               param("min_rating", "num", required=False), param("min_reviews", "int", required=False),
               param("has_website", "bool", "true: only with a website; false: only without (leads for web design).", required=False),
               param("near", description="'lat,lon' or an address/place name to measure distance from.", required=False),
               param("radius_km", "num", "Only places within this distance of near.", required=False),
               param("sort", enum=["score", "rating", "reviews", "distance", "name"], required=False, default="score"),
               param("open_only", "bool", "Skip permanently closed places.", required=False, default=True),
               param("limit", "int", required=False, default=20)],
      keywords="cari tempat, find places, terdekat, nearest, terbaik, best rated, rating tertinggi, top rated, paling ramai, "
               "rekomendasi tempat, belum punya website, tanpa website, no website, leads", lead=True, workspace=True)
async def places_search(text: str | None = None, dataset: str | None = None, category: str | None = None,
                        city: str | None = None, min_rating: float | None = None, min_reviews: int | None = None,
                        has_website: bool | None = None, near: str | None = None, radius_km: float | None = None,
                        sort: str = "score", open_only: bool = True, limit: int = 20,
                        workspace_path: str | None = None) -> dict[str, Any]:
    places = _filter(_load(dataset, workspace_path), text, category, city, min_rating, min_reviews, has_website, open_only)
    point = await _near_point(near)
    if point:
        for p in places:
            p["distance_km"] = round(haversine(point, (p["lat"], p["lon"])), 3) if p.get("lat") is not None and p.get("lon") is not None else None
        if radius_km is not None:
            places = [p for p in places if p.get("distance_km") is not None and p["distance_km"] <= float(radius_km)]
    for p in places:
        p["score"] = round(_score(p), 3)
    key = {"rating": lambda p: (-(p.get("rating") or 0), -(p.get("reviews") or 0)),
           "reviews": lambda p: -(p.get("reviews") or 0),
           "distance": lambda p: p.get("distance_km") if p.get("distance_km") is not None else 1e9,
           "name": lambda p: p["name"].lower(),
           "score": lambda p: -p["score"]}[sort if sort != "distance" or point else "score"]
    places.sort(key=key)
    shown = places[: max(1, min(int(limit), 200))]
    return {"matched": len(places), "places": [_brief(p) for p in shown],
            **({"from": list(point)} if point else {}),
            **({"note": "distance sort needs near"} if sort == "distance" and not point else {})}


def _score(p: dict[str, Any]) -> float:
    """Bayesian average: a 5.0 with 3 reviews should not beat a 4.7 with 900."""
    rating, count = p.get("rating") or 0.0, p.get("reviews") or 0
    prior, weight = 4.0, 25
    return (rating * count + prior * weight) / (count + weight) if rating else 0.0


def _brief(p: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "category", "address", "city", "rating", "reviews", "website_domain", "distance_km", "score",
            "lat", "lon", "maps_url", "phone", "emails", "dataset", "price_range", "status")
    return {k: p[k] for k in keys if p.get(k) not in (None, "")}


@spec("places_stats", "Summary of imported places: count, categories, cities, rating distribution, share with a website, "
      "and the best and worst rated, for market research or lead lists.",
      "maps", [param("dataset", required=False), param("text", required=False), param("city", required=False), param("category", required=False)],
      keywords="statistik tempat, ringkasan data maps, rekap tempat, analisis pasar, market research, kompetitor, competitor",
      workspace=True)
def places_stats(dataset: str | None = None, text: str | None = None, city: str | None = None, category: str | None = None,
                 workspace_path: str | None = None) -> dict[str, Any]:
    places = _filter(_load(dataset, workspace_path), text, category, city, None, None, None, False)
    if not places:
        return {"count": 0}

    def tally(key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in places:
            for v in str(p.get(key) or "unknown").split(","):
                v = v.strip() or "unknown"
                counts[v] = counts.get(v, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:15])

    rated = [p for p in places if p.get("rating")]
    bands = {"4.5+": 0, "4.0-4.4": 0, "3.0-3.9": 0, "<3.0": 0}
    for p in rated:
        r = p["rating"]
        bands["4.5+" if r >= 4.5 else "4.0-4.4" if r >= 4 else "3.0-3.9" if r >= 3 else "<3.0"] += 1
    ranked = sorted(rated, key=_score, reverse=True)
    return {"count": len(places), "categories": tally("category"), "cities": tally("city"), "rating_bands": bands,
            "average_rating": round(sum(p["rating"] for p in rated) / len(rated), 2) if rated else None,
            "total_reviews": sum(p.get("reviews") or 0 for p in places),
            "with_website_percent": round(100 * sum(1 for p in places if p.get("website_domain")) / len(places), 1),
            "best": [_brief(p) for p in ranked[:5]], "lowest": [_brief(p) for p in ranked[-3:]] if len(ranked) > 5 else []}


@spec("places_export", "Save a filtered list of imported places as CSV, Excel, JSON, Markdown, or GeoJSON (for maps) in the "
      "workspace, ready to share with share_file.",
      "maps", [param("output", description="e.g. 'hasil/klinik-bandung.xlsx'. Extension picks the format (.csv .xlsx .json .md .geojson)."),
               param("dataset", required=False), param("text", required=False), param("category", required=False), param("city", required=False),
               param("min_rating", "num", required=False), param("has_website", "bool", required=False),
               param("open_only", "bool", "Skip permanently closed places.", required=False, default=True)],
      keywords="export tempat, simpan daftar tempat, download hasil maps, excel tempat, geojson, daftar leads", workspace=True,
      read_only=False)
def places_export(output: str, dataset: str | None = None, text: str | None = None, category: str | None = None,
                  city: str | None = None, min_rating: float | None = None, has_website: bool | None = None,
                  open_only: bool = True, workspace_path: str | None = None) -> dict[str, Any]:
    places = _filter(_load(dataset, workspace_path), text, category, city, min_rating, None, has_website, open_only)
    places.sort(key=_score, reverse=True)
    dest = _files.destination(output, workspace_path)
    ext = dest.suffix.lower()
    rows = [_brief({**p, "score": round(_score(p), 3)}) for p in places]
    if ext == ".geojson":
        features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                     "properties": {k: v for k, v in _brief(p).items() if k not in ("lat", "lon")}}
                    for p in places if p.get("lat") is not None and p.get("lon") is not None]
        dest.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
    else:
        import pandas as pd

        from .data_tools import _write

        _write(pd.DataFrame(rows), dest if ext in (".csv", ".xlsx", ".json", ".md", ".html") else dest.with_suffix(".csv"))
    return {"saved": _files.relative(dest, workspace_path), "places": len(rows)}


@spec("places_datasets", "List the Google Maps datasets imported in this session, with their query, size and source file.",
      "maps", [], keywords="dataset maps, data tempat apa saja, imported places", workspace=True)
def places_datasets(workspace_path: str | None = None) -> dict[str, Any]:
    sets = _datasets(workspace_path)
    return {"datasets": [{"name": s["name"], "query": s.get("query"), "places": s.get("count"), "source": s.get("source"),
                          "imported_at": s.get("imported_at")} for s in sets]}


def _scraper() -> str | None:
    configured = os.getenv("GMAPS_SCRAPER_BIN", "").strip()
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser())
    return shutil.which("google-maps-scraper") or shutil.which("gmaps-scraper")


@spec("gmaps_scrape", "Run gosom/google-maps-scraper (if installed) for one or more searches like 'klinik gigi di Bandung', "
      "then import the results into this session. Takes minutes; tell the user before starting. Without the scraper it "
      "explains how to install it or how to upload an export instead.",
      "maps", [param("queries", "list", "Search phrases, one per line in Google Maps.", items="str"),
               param("depth", "int", "Result pages to scroll per query (1-10).", required=False, default=1),
               param("language", required=False, default="id"),
               param("max_minutes", "int", "Stop after this long.", required=False, default=6),
               param("keep_contacts", "bool", required=False, default=False)],
      keywords="scrape google maps, scraping maps, ambil data google maps, cari leads, kumpulkan data tempat, gmaps scraper",
      lead=True, workspace=True, network=True, read_only=False)
async def gmaps_scrape(queries: list[str], depth: int = 1, language: str = "id", max_minutes: int = 6,
                       keep_contacts: bool = False, workspace_path: str | None = None) -> dict[str, Any]:
    binary = _scraper()
    if not binary:
        return {"ok": False, "error": "google-maps-scraper is not installed on this machine",
                "install": ["go install github.com/gosom/google-maps-scraper@latest   (needs Go and a Chromium/Playwright)",
                            "or download a release binary and set GMAPS_SCRAPER_BIN=/path/to/google-maps-scraper"],
                "alternative": "upload a CSV/JSON export from any Google Maps scraper and call gmaps_import"}
    qs = [q.strip() for q in (queries if isinstance(queries, list) else [queries]) if str(q).strip()][:20]
    if not qs:
        raise ValueError("give at least one query")
    root = _files.workspace(workspace_path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = root / "maps"
    folder.mkdir(exist_ok=True)
    input_file = folder / f"queries-{stamp}.txt"
    results = folder / f"results-{stamp}.csv"
    input_file.write_text("\n".join(qs) + "\n", encoding="utf-8")
    args = [binary, "-input", str(input_file), "-results", str(results), "-depth", str(max(1, min(int(depth), 10))),
            "-lang", language, "-exit-monitor", "1m"]
    if keep_contacts:
        args.append("-email")
    process = await asyncio.create_subprocess_exec(*args, cwd=str(folder), stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.STDOUT)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=max(1, min(int(max_minutes), 30)) * 60)
        timed_out = False
    except TimeoutError:
        process.kill()
        output, _ = await process.communicate()
        timed_out = True
    if not results.exists() or results.stat().st_size == 0:
        return {"ok": False, "error": "the scraper produced no results", "exit_code": process.returncode,
                "log_tail": (output or b"").decode("utf-8", "replace")[-1200:]}
    imported = await gmaps_import_tool(path=str(results.relative_to(root)), query=" + ".join(qs),
                                       keep_contacts=keep_contacts, workspace_path=str(root))
    return {**imported, "results_file": str(results.relative_to(root)), "stopped_at_time_limit": timed_out}


# ── OpenStreetMap (live) ──────────────────────────────────────────────────────
async def _geocode(query: str, limit: int, country: str = "") -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query, "format": "jsonv2", "limit": max(1, min(int(limit), 10)), "addressdetails": 1,
                              "accept-language": "id,en"}
    if country:
        params["countrycodes"] = country.lower()
    data = await _net.get_json("nominatim.openstreetmap.org", "/search", params)
    return [{"name": d.get("display_name"), "lat": float(d["lat"]), "lon": float(d["lon"]), "type": d.get("type"),
             "category": d.get("category"), "address": d.get("address", {})} for d in data]


@spec("geocode", "Find the coordinates of an address or place name with OpenStreetMap (free, no key), with the full "
      "address breakdown. Needs internet.",
      "maps", [param("query", description="Address or place, e.g. 'Monas, Jakarta'."), param("limit", "int", required=False, default=3),
               param("country", description="Two-letter code to restrict, e.g. 'id'.", required=False, default="")],
      keywords="geocode, koordinat alamat, koordinat dari alamat, cari koordinat, letak, dimana letak, where is, lokasi alamat",
      lead=True, network=True)
async def geocode(query: str, limit: int = 3, country: str = "") -> dict[str, Any]:
    found = await _geocode(query, limit, country)
    for f in found:
        f["google_maps"] = f"https://www.google.com/maps?q={f['lat']:.6f},{f['lon']:.6f}"
    return {"results": found, "count": len(found), "source": "OpenStreetMap Nominatim"}


@spec("reverse_geocode", "The address at given coordinates (OpenStreetMap, free). Needs internet.",
      "maps", [param("point", description="'lat, lon'.")],
      keywords="reverse geocode, alamat dari koordinat, address from coordinates, ini dimana, lokasi apa ini", network=True)
async def reverse_geocode(point: str) -> dict[str, Any]:
    lat, lon = parse_point(point)
    d = await _net.get_json("nominatim.openstreetmap.org", "/reverse",
                            {"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1, "accept-language": "id,en"})
    if "error" in d:
        raise ValueError(str(d["error"]))
    return {"address": d.get("display_name"), "parts": d.get("address", {}), "lat": lat, "lon": lon}


_OSM_KINDS = {
    "restaurant": '["amenity"="restaurant"]', "cafe": '["amenity"="cafe"]', "fast_food": '["amenity"="fast_food"]',
    "hospital": '["amenity"="hospital"]', "clinic": '["amenity"~"clinic|doctors"]', "dentist": '["amenity"="dentist"]',
    "pharmacy": '["amenity"="pharmacy"]', "school": '["amenity"="school"]', "university": '["amenity"="university"]',
    "mosque": '["amenity"="place_of_worship"]["religion"="muslim"]', "church": '["amenity"="place_of_worship"]["religion"="christian"]',
    "atm": '["amenity"="atm"]', "bank": '["amenity"="bank"]', "fuel": '["amenity"="fuel"]', "parking": '["amenity"="parking"]',
    "police": '["amenity"="police"]', "hotel": '["tourism"~"hotel|guest_house|hostel"]', "supermarket": '["shop"~"supermarket|convenience"]',
    "market": '["amenity"="marketplace"]', "park": '["leisure"="park"]', "toilets": '["amenity"="toilets"]',
    "bus_stop": '["highway"="bus_stop"]', "train_station": '["railway"="station"]', "gym": '["leisure"="fitness_centre"]',
    "laundry": '["shop"="laundry"]', "barber": '["shop"~"hairdresser|barber"]', "car_repair": '["shop"~"car_repair|motorcycle_repair"]',
}
_OSM_ALIASES = {"restoran": "restaurant", "rumah makan": "restaurant", "kafe": "cafe", "kopi": "cafe", "rumah sakit": "hospital",
                "rs": "hospital", "klinik": "clinic", "puskesmas": "clinic", "dokter gigi": "dentist", "apotek": "pharmacy",
                "sekolah": "school", "kampus": "university", "masjid": "mosque", "gereja": "church", "spbu": "fuel",
                "pom bensin": "fuel", "parkir": "parking", "polisi": "police", "penginapan": "hotel", "minimarket": "supermarket",
                "pasar": "market", "taman": "park", "toilet": "toilets", "halte": "bus_stop", "stasiun": "train_station",
                "bengkel": "car_repair", "pangkas rambut": "barber", "salon": "barber"}


@spec("osm_nearby", "Find places of a kind near a point or address, live from OpenStreetMap (free, no key): restaurants, cafes, "
      "hospitals, clinics, pharmacies (apotek), ATMs, SPBU, mosques, schools, hotels, minimarkets, bengkel, and more. "
      "Sorted by distance. Use when no Google Maps scrape is available. Needs internet.",
      "maps", [param("kind", description=f"One of: {', '.join(sorted(_OSM_KINDS))} (Indonesian names work too)."),
               param("near", description="'lat,lon' or an address."), param("radius_km", "num", required=False, default=2),
               param("limit", "int", required=False, default=15)],
      keywords="terdekat, nearest, nearby, sekitar sini, dekat sini, near me, apotek terdekat, atm terdekat, spbu terdekat, "
               "masjid terdekat, rumah sakit terdekat", lead=True, network=True)
async def osm_nearby(kind: str, near: str, radius_km: float = 2, limit: int = 15) -> dict[str, Any]:
    k = _OSM_ALIASES.get(kind.strip().lower(), kind.strip().lower())
    if k not in _OSM_KINDS:
        raise ValueError(f"unknown kind '{kind}'; use one of {', '.join(sorted(_OSM_KINDS))}")
    point = await _near_point(near)
    if point is None:
        raise ValueError("near is required")
    radius = int(max(0.1, min(float(radius_km), 25)) * 1000)
    query = f"[out:json][timeout:25];nwr{_OSM_KINDS[k]}(around:{radius},{point[0]},{point[1]});out center 200;"
    data = await _net.get_json("overpass-api.de", "/api/interpreter", method="POST", data=query, timeout=30)
    found = []
    for el in data.get("elements", []):
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        found.append({"name": tags.get("name") or tags.get("brand") or f"({k}, unnamed)",
                      "distance_km": round(haversine(point, (lat, lon)), 3), "lat": lat, "lon": lon,
                      "address": " ".join(filter(None, [tags.get("addr:street"), tags.get("addr:housenumber"), tags.get("addr:city")])) or None,
                      "opening_hours": tags.get("opening_hours"),
                      "google_maps": f"https://www.google.com/maps?q={lat},{lon}"})
    found.sort(key=lambda f: f["distance_km"])
    return {"kind": k, "from": list(point), "radius_km": radius / 1000, "count": len(found),
            "places": found[: max(1, min(int(limit), 100))], "source": "OpenStreetMap (coverage varies by area)"}
