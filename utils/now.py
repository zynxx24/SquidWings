"""The current date and time for the system prompt.

Models only know the date their training ended, so "berita hari ini" or "umur saya kalau
lahir 2008" go wrong without it. The line uses this machine's local time zone.
"""

from __future__ import annotations

from datetime import datetime

_DAYS = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
_MONTHS = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
           "Oktober", "November", "Desember")
# Indonesian zone names read better than the +07 offset the C library prints.
_ZONES = {7: "WIB", 8: "WITA", 9: "WIT"}


def now_line(moment: datetime | None = None) -> str:
    """'[NOW] Kamis, 20 November 2026, 18:45 WIB (2026-11-20)'."""
    current = (moment or datetime.now()).astimezone()
    offset = current.utcoffset()
    hours = int(offset.total_seconds() // 3600) if offset is not None else 0
    zone = _ZONES.get(hours) or current.tzname() or f"UTC{hours:+d}"
    return (
        f"[NOW] {_DAYS[current.weekday()]}, {current.day} {_MONTHS[current.month - 1]} {current.year}, "
        f"{current:%H:%M} {zone} ({current:%Y-%m-%d}). Use this for 'hari ini', ages, deadlines and "
        "how recent a source is; your training data is older than this date."
    )
