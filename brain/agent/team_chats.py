"""Customer chats on Telegram: how often someone may write, what was said before, and the day's budget.

A bot that answers the public is open to anyone who finds its name, so each chat gets a small allowance per minute
and the bot a cap per day. Both are plain counters in memory: they reset when the server restarts, which only ever
errs on the side of answering. What was said is kept in ``chats.json`` next to the teams, a few turns per chat and
only for a week, so a customer who comes back tomorrow is not a stranger.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any

PER_MINUTE = 6
WINDOW_SECONDS = 60
MAX_TURN_CHARS = 1200
MAX_KEPT_TURNS = 20
MAX_CHATS = 500
KEEP_SECONDS = 7 * 24 * 3600


class Limiter:
    """Allow a few messages per chat per minute. ``check`` says "ok", "warn" (the first one too many) or "drop"."""

    def __init__(self, per_minute: int = PER_MINUTE) -> None:
        self.per_minute = per_minute
        self._seen: dict[str, deque[float]] = {}
        self._warned: dict[str, float] = {}
        self._days: dict[tuple[str, str], int] = {}

    def check(self, chat: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        stamps = self._seen.setdefault(chat, deque())
        while stamps and now - stamps[0] > WINDOW_SECONDS:
            stamps.popleft()
        if len(stamps) < self.per_minute:
            stamps.append(now)
            return "ok"
        if now - self._warned.get(chat, 0.0) > WINDOW_SECONDS:
            self._warned[chat] = now
            return "warn"
        return "drop"

    def day_count(self, team: str, today: str | None = None) -> int:
        return self._days.get((team, today or date.today().isoformat()), 0)

    def count_day(self, team: str, today: str | None = None) -> int:
        day = today or date.today().isoformat()
        for key in [k for k in self._days if k[1] != day]:
            del self._days[key]
        self._days[(team, day)] = self._days.get((team, day), 0) + 1
        return self._days[(team, day)]


class ChatLog:
    """Recent turns per (team, chat), saved so they survive a restart."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._chats: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._chats is None:
            data: Any = {}
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(self.path.read_text(encoding="utf-8"))
            self._chats = data if isinstance(data, dict) else {}
        return self._chats

    def _save(self) -> None:
        chats = self._load()
        cutoff = time.time() - KEEP_SECONDS
        for key in [k for k, v in chats.items() if float(v.get("at", 0)) < cutoff]:
            del chats[key]
        for key in sorted(chats, key=lambda k: float(chats[k].get("at", 0)))[: max(0, len(chats) - MAX_CHATS)]:
            del chats[key]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    @staticmethod
    def _key(team: str, chat: str) -> str:
        return f"{team}:{chat}"

    def add(self, team: str, chat: str, who: str, text: str, keep: int = MAX_KEPT_TURNS) -> None:
        clean = " ".join(str(text).split())[:MAX_TURN_CHARS]
        if not clean:
            return
        with self._lock:
            entry = self._load().setdefault(self._key(team, chat), {"turns": [], "at": 0})
            entry["turns"].append({"who": who, "text": clean})
            entry["turns"] = entry["turns"][-max(1, min(keep, MAX_KEPT_TURNS)) * 2:]
            entry["at"] = time.time()
            self._save()

    def render(self, team: str, chat: str, turns: int) -> str:
        """The last ``turns`` exchanges as "Pelanggan: …" and "Toko: …" lines. Empty when there are none."""
        if turns <= 0:
            return ""
        with self._lock:
            entry = self._load().get(self._key(team, chat))
            recent = list(entry["turns"])[-turns * 2:] if entry else []
        return "\n".join(f"{item['who']}: {item['text']}" for item in recent)

    def forget(self, team: str) -> None:
        with self._lock:
            chats = self._load()
            for key in [k for k in chats if k.startswith(f"{team}:")]:
                del chats[key]
            self._save()
