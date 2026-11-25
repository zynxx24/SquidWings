"""One place for the harness's caches: bounded, timed, measurable, and cleared per chat.

Ad-hoc ``dict`` caches grow without limit and ``lru_cache`` never notices that a file changed.
Every cache here is named, holds at most ``max_entries`` (least recently used goes first), can
expire after ``ttl`` seconds, and counts its hits and misses, so ``python main.py cache`` shows
what pays off. Keys that start with a session id can be dropped when that chat is deleted
(``clear_session``), which keeps per-session isolation.

``FileWatch`` answers "did any of these files change?" cheaply (one ``stat`` per file, at most
every ``interval`` seconds), so caches built from files on disk (skills, adapters, catalogs)
refresh themselves when a file is edited or a skill repo is pulled, without a restart.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")
_MISSING = object()


class Cache:
    """A thread-safe LRU cache with an optional time limit per entry."""

    def __init__(self, name: str, max_entries: int = 256, ttl: float | None = None) -> None:
        self.name = name
        self.max_entries = max(1, int(max_entries))
        self.ttl = ttl
        self._data: OrderedDict[Hashable, tuple[float | None, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = self.misses = self.evictions = 0

    def get(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            item = self._data.get(key, _MISSING)
            if item is _MISSING:
                self.misses += 1
                return default
            expires: float | None = item[0]  # type: ignore[index]
            value: Any = item[1]  # type: ignore[index]
            if expires is not None and expires < time.monotonic():
                del self._data[key]
                self.misses += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        life = self.ttl if ttl is None else ttl
        with self._lock:
            self._data[key] = (time.monotonic() + life if life else None, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                self.evictions += 1

    def get_or_set(self, key: Hashable, factory: Callable[[], T], ttl: float | None = None) -> T:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            value = factory()
            self.set(key, value, ttl)
        return value  # type: ignore[no-any-return]

    def pop(self, key: Hashable) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            return count

    def clear_where(self, predicate: Callable[[Hashable], bool]) -> int:
        with self._lock:
            doomed = [key for key in self._data if predicate(key)]
            for key in doomed:
                del self._data[key]
            return len(doomed)

    def __len__(self) -> int:
        return len(self._data)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {"name": self.name, "entries": len(self._data), "max": self.max_entries, "ttl": self.ttl,
                "hits": self.hits, "misses": self.misses, "evictions": self.evictions,
                "hit_rate": round(self.hits / total, 3) if total else None}


_caches: dict[str, Cache] = {}
_registry_lock = threading.Lock()


def cache(name: str, max_entries: int = 256, ttl: float | None = None) -> Cache:
    """The named cache, created on first use. Later calls return the same one."""
    with _registry_lock:
        found = _caches.get(name)
        if found is None:
            found = _caches[name] = Cache(name, max_entries, ttl)
        return found


def stats() -> list[dict[str, Any]]:
    return [c.stats() for c in sorted(_caches.values(), key=lambda c: c.name)]


def clear(name: str | None = None) -> int:
    """Empty one cache, or all of them. Returns how many entries went."""
    targets = [_caches[name]] if name and name in _caches else ([] if name else list(_caches.values()))
    return sum(c.clear() for c in targets)


def clear_session(session_id: str) -> int:
    """Drop every entry whose key is ``(session_id, ...)``: a deleted chat leaves nothing behind."""
    if not session_id:
        return 0
    return sum(
        c.clear_where(lambda key: isinstance(key, tuple) and bool(key) and key[0] == session_id)
        for c in list(_caches.values())
    )


class FileWatch:
    """Cheap change detection for a set of files and folders.

    ``changed()`` stats them at most every ``interval`` seconds and says whether anything
    differs from the last look (a file edited, added, or removed). The first call is False.
    """

    def __init__(self, paths: Callable[[], Iterable[Path]], interval: float = 2.0) -> None:
        self._paths = paths
        self.interval = interval
        self._last_check = 0.0
        self._fingerprint: tuple[Any, ...] | None = None
        self._lock = threading.Lock()

    def _take(self) -> tuple[Any, ...]:
        prints: list[tuple[str, int | None, int | None]] = []
        for path in self._paths():
            try:
                st = path.stat()
                prints.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                prints.append((str(path), None, None))
        return tuple(sorted(prints, key=lambda item: item[0]))

    def changed(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._fingerprint is not None and now - self._last_check < self.interval:
                return False
            self._last_check = now
            current = self._take()
            if self._fingerprint is None:
                self._fingerprint = current
                return False
            if current != self._fingerprint:
                self._fingerprint = current
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self._fingerprint = None
            self._last_check = 0.0


__all__ = ["Cache", "FileWatch", "cache", "clear", "clear_session", "stats"]
