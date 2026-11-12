"""The daily brief: what happened in yesterday's chats, what was made, and what is still hanging.

Built from the saved chats only, with plain rules and no model call, so it costs nothing, works offline, and
says the same thing twice. One file per day in ``.agent_memory/briefs/`` (``YYYY-MM-DD.md`` for reading,
``.json`` for the web page); a day's brief is written once, and ``state.json`` remembers which day was shown.

* *Window*: from the start of yesterday (local time) to now, so a brief written at 08:00 covers yesterday
  and the early morning.
* *Made*: files the tools wrote or changed (read from each answer's tool trace; a failed step does not count).
* *Hanging*: a chat whose last message is yours and unanswered; an answer that ends by asking you something;
  an answer whose tools failed; an answer that says what is left to do.

``BRIEF_TIME`` (default ``08:00``) is when the web server writes it; the terminal writes it on the first start of the
day. ``DAILY_BRIEF=false`` turns the automatic brief off; ``/brief`` and ``GET /api/brief`` still work.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_CHATS = 8
MAX_FILES = 6
MAX_HANGING = 6

# Tools whose use means a file was made or changed.
_WRITERS = frozenset({
    "file_write", "file_patch", "file_replace_all", "file_move", "file_copy", "doc_create", "sheet_create",
    "slides_create", "archive", "media_convert", "voice_speak", "share_file",
})
_LEFT = re.compile(r"\b(?:belum selesai|belum dikerjakan|sisanya|selanjutnya|langkah berikutnya|next steps?|todo|"
                   r"masih perlu|tinggal|lanjutkan|lanjut(?:kan)? (?:nanti|besok))\b", re.IGNORECASE)
_TRACE_PART = re.compile(r"^(?P<tool>[a-z_0-9]+)\s*(?P<what>.*?)(?P<failed>\s*\(failed\))?$")


def enabled() -> bool:
    return os.getenv("DAILY_BRIEF", "true").strip().lower() not in ("0", "false", "no", "off")


def brief_time() -> dt.time:
    """When the web server writes the brief (``BRIEF_TIME=HH:MM``; a bad value means 08:00)."""
    raw = os.getenv("BRIEF_TIME", "").strip() or "08:00"
    try:
        hour, minute = (int(p) for p in raw.split(":", 1))
        return dt.time(hour, minute)
    except ValueError:
        return dt.time(8, 0)


def default_dir() -> Path:
    from config.settings import get_settings

    return get_settings().project_root / ".agent_memory" / "briefs"


# ── collecting ──────────────────────────────────────────────────────────────
@dataclass
class ChatDigest:
    session_id: str
    label: str
    messages: int
    asked: str
    answered: str
    files: list[str] = field(default_factory=list)
    hanging: list[str] = field(default_factory=list)
    tokens: int = 0
    models: dict[str, int] = field(default_factory=dict)
    last_at: float = 0.0


def _gist(text: str, limit: int = 110) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def _trace_files(trace: str) -> tuple[list[str], list[str]]:
    """(files made or changed, steps that failed) from one answer's tool trace."""
    made: list[str] = []
    failed: list[str] = []
    for part in (trace or "").split(";"):
        match = _TRACE_PART.match(part.strip())
        if not match:
            continue
        tool, what = match["tool"], match["what"].strip()
        if match["failed"]:
            failed.append(f"{tool} {what}".strip())
        elif tool in _WRITERS and what:
            made.append(what)
    return made, failed


def _last_paragraph(text: str) -> str:
    paragraphs = [p.strip() for p in (text or "").strip().split("\n\n") if p.strip()]
    return paragraphs[-1] if paragraphs else ""


def _digest(session_id: str, label: str, messages: list[Any], since: float, until: float) -> ChatDigest | None:
    window = [m for m in messages if since <= m.timestamp <= until and m.role in ("user", "assistant")]
    if not window:
        return None
    users = [m for m in window if m.role == "user" and m.content.strip()]
    answers = [m for m in window if m.role == "assistant" and m.content.strip()]
    digest = ChatDigest(
        session_id=session_id, label=label, messages=len(window),
        asked=_gist(users[0].content) if users else "", answered=_gist(answers[-1].content, 140) if answers else "",
        last_at=window[-1].timestamp,
    )
    seen: set[str] = set()
    failures: list[str] = []
    for message in answers:
        digest.tokens += int(message.token_count or 0)
        if message.model_used:
            digest.models[message.model_used] = digest.models.get(message.model_used, 0) + 1
        made, failed = _trace_files(message.tool_trace)
        for name in made:
            if name not in seen:
                seen.add(name)
                digest.files.append(name)
        failures = failed  # only the newest answer's failures are still open
    last = window[-1]
    if last.role == "user":
        digest.hanging.append(f"belum dijawab: {_gist(last.content, 90)}")
    elif answers:
        tail = _last_paragraph(answers[-1].content)
        if tail.rstrip().endswith("?"):
            digest.hanging.append(f"menunggu jawabanmu: {_gist(tail, 100)}")
        elif _LEFT.search(tail):
            digest.hanging.append(f"masih ada lanjutan: {_gist(tail, 100)}")
        if failures:
            digest.hanging.append(f"langkah gagal: {', '.join(failures[:3])}")
    return digest


def window_start(now: dt.datetime) -> dt.datetime:
    """Midnight at the start of yesterday, in the local time of ``now``."""
    return (now - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def build(store: Any, now: dt.datetime | None = None) -> dict[str, Any]:
    """The brief as data. ``store`` is a ConversationStore (anything with list_sessions, get_history and label)."""
    now = now or dt.datetime.now().astimezone()
    since, until = window_start(now), now
    digests: list[ChatDigest] = []
    for entry in store.list_sessions():
        if entry["updated_at"] < since.timestamp():
            continue
        found = _digest(entry["session_id"], entry["title"], store.get_history(entry["session_id"]),
                        since.timestamp(), until.timestamp())
        if found:
            digests.append(found)
    digests.sort(key=lambda d: d.last_at, reverse=True)

    files: list[str] = []
    hanging: list[dict[str, str]] = []
    for d in digests:
        files += [f for f in d.files if f not in files]
        hanging += [{"chat": d.label, "text": text} for text in d.hanging]
    models: dict[str, int] = {}
    for d in digests:
        for model, count in d.models.items():
            models[model] = models.get(model, 0) + count
    return {
        "day": now.strftime("%Y-%m-%d"),
        "weekday": ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")[now.weekday()],
        "generated_at": now.isoformat(timespec="seconds"),
        "window_from": since.isoformat(timespec="minutes"),
        "empty": not digests,
        "chats": [
            {"session_id": d.session_id, "label": d.label, "messages": d.messages, "asked": d.asked,
             "answered": d.answered, "files": d.files[:MAX_FILES], "hanging": d.hanging}
            for d in digests[:MAX_CHATS]
        ],
        "chat_count": len(digests),
        "files": files[: MAX_FILES * 2],
        "file_count": len(files),
        "hanging": hanging[:MAX_HANGING],
        "hanging_count": len(hanging),
        "tokens": sum(d.tokens for d in digests),
        "top_model": max(models, key=lambda m: models[m]) if models else "",
    }


# ── rendering ───────────────────────────────────────────────────────────────
def render(brief: dict[str, Any]) -> str:
    """Markdown for reading in a file or the terminal."""
    lines = [f"# Brief {brief['weekday']}, {brief['day']}", ""]
    if brief["empty"]:
        lines.append("Kemarin dan pagi ini belum ada obrolan. Mulai saja dari mana pun kamu mau.")
        return "\n".join(lines) + "\n"
    lines.append(f"{brief['chat_count']} chat aktif sejak kemarin, {brief['file_count']} file dibuat atau diubah"
                 + (f", model terbanyak {brief['top_model']}" if brief["top_model"] else "") + ".")
    if brief["hanging"]:
        lines += ["", "## Masih menggantung"]
        lines += [f"- **{h['chat']}**: {h['text']}" for h in brief["hanging"]]
        if brief["hanging_count"] > len(brief["hanging"]):
            lines.append(f"- …dan {brief['hanging_count'] - len(brief['hanging'])} lagi")
    if brief["files"]:
        lines += ["", "## Hasil kerja"]
        lines += [f"- `{f}`" for f in brief["files"][:MAX_FILES]]
        if brief["file_count"] > MAX_FILES:
            lines.append(f"- …dan {brief['file_count'] - MAX_FILES} file lain")
    lines += ["", "## Chat"]
    for chat in brief["chats"]:
        lines.append(f"- **{chat['label']}** ({chat['messages']} pesan): {chat['asked']}")
        if chat["answered"]:
            lines.append(f"  - terakhir: {chat['answered']}")
    if brief["chat_count"] > len(brief["chats"]):
        lines.append(f"- …dan {brief['chat_count'] - len(brief['chats'])} chat lain (/chats)")
    return "\n".join(lines) + "\n"


# ── one brief per day, and remembering it was shown ─────────────────────────
def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as tmp:
        tmp.write(text)
    os.replace(tmp.name, path)


def _state(directory: Path) -> dict[str, Any]:
    try:
        data = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ensure_today(store: Any, directory: Path | None = None, now: dt.datetime | None = None, *, force: bool = False) -> dict[str, Any]:
    """Today's brief: the saved one, or built now and saved. ``force`` rebuilds it (the day's chats may have grown)."""
    folder = directory or default_dir()
    now = now or dt.datetime.now().astimezone()
    path = folder / f"{now.strftime('%Y-%m-%d')}.json"
    if not force:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and "chats" in saved:
                return saved
        except (OSError, ValueError):
            pass
    brief = build(store, now)
    _write(path, json.dumps(brief, ensure_ascii=False, indent=2))
    _write(path.with_suffix(".md"), render(brief))
    prune(folder)
    return brief


def seen(directory: Path | None = None, channel: str = "terminal") -> str:
    """The day (YYYY-MM-DD) whose brief was last shown in ``channel`` ("terminal" or "web"), or an empty string.
    Each place keeps its own mark, so reading it in the terminal does not hide it from the web page."""
    return str(_state(directory or default_dir()).get(f"shown_{channel}", ""))


def mark_seen(day: str, directory: Path | None = None, channel: str = "terminal") -> None:
    folder = directory or default_dir()
    _write(folder / "state.json", json.dumps({**_state(folder), f"shown_{channel}": day}))


def due(directory: Path | None = None, now: dt.datetime | None = None, channel: str = "terminal") -> bool:
    """True when today's brief has not been shown in ``channel`` yet."""
    now = now or dt.datetime.now().astimezone()
    return seen(directory, channel) != now.strftime("%Y-%m-%d")


def prune(directory: Path | None = None, keep: int = 30) -> None:
    """Keep the newest ``keep`` days; a brief is a snapshot, not an archive."""
    folder = directory or default_dir()
    days = sorted(p for p in folder.glob("????-??-??.json"))
    for old in days[:-keep]:
        for suffix in (".json", ".md"):
            old.with_suffix(suffix).unlink(missing_ok=True)


def seconds_until(at: dt.time, now: dt.datetime | None = None) -> float:
    """Seconds from ``now`` to the next ``at`` (today if it is still ahead, else tomorrow)."""
    now = now or dt.datetime.now().astimezone()
    target = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()
