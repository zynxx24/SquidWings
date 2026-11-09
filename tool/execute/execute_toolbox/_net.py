"""Small JSON client for the few public, keyless APIs toolbox tools use.

Only hosts listed here can be reached, so a model cannot point these tools at the router,
a local service, or a metadata endpoint. Responses are cached per session (see
per-session isolation): two chats asking the same question never share a cache entry.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from utils.workspace_context import get_active_session

ALLOWED_HOSTS = frozenset({
    "nominatim.openstreetmap.org", "overpass-api.de", "api.open-meteo.com", "geocoding-api.open-meteo.com",
    "api.frankfurter.app", "api.frankfurter.dev", "date.nager.at", "id.wikipedia.org", "en.wikipedia.org",
    "api.dictionaryapi.dev",
})
_HEADERS = {"User-Agent": "SquidWings/1.0 (self-hosted agent)", "Accept": "application/json"}
_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_CACHE_SECONDS = 900.0
_CACHE_LIMIT = 300
# Nominatim's policy is at most one request per second.
_SLOW_HOSTS = {"nominatim.openstreetmap.org": 1.0}
_last_call: dict[str, float] = {}
_lock = asyncio.Lock()


class NetError(RuntimeError):
    pass


async def get_json(host: str, path: str, params: dict[str, Any] | None = None, *, timeout: float = 15.0,
                   method: str = "GET", data: str | None = None) -> Any:
    if host not in ALLOWED_HOSTS:
        raise NetError(f"{host} is not an allowed host")
    url = f"https://{host}{path}"
    key = (get_active_session() or "default", f"{method} {url} {json.dumps(params or {}, sort_keys=True)} {data or ''}")
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    async with _lock:
        gap = _SLOW_HOSTS.get(host, 0.0) - (time.monotonic() - _last_call.get(host, 0.0))
        if gap > 0:
            await asyncio.sleep(gap)
        _last_call[host] = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, follow_redirects=False) as client:
            if method == "POST":
                response = await client.post(url, data={"data": data or ""})
            else:
                response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise NetError(f"could not reach {host}: {exc.__class__.__name__}. The machine may be offline.") from exc
    if response.status_code == 429:
        raise NetError(f"{host} is rate limiting; wait a minute and try again")
    if response.status_code >= 400:
        raise NetError(f"{host} answered HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise NetError(f"{host} did not return JSON") from exc
    if len(_CACHE) >= _CACHE_LIMIT:
        for old in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _CACHE_LIMIT // 4]:
            _CACHE.pop(old, None)
    _CACHE[key] = (time.monotonic(), payload)
    return payload


def clear_cache() -> None:
    _CACHE.clear()
