"""Which trading sessions are open at a moment: Sydney, Tokyo, London, New York, and their overlaps.

Each session is 8:00 to 17:00 in its own city (Sydney 7:00 to 16:00), read with the city's time
zone, so daylight saving moves them as it moves the real ones. The forex week closes Friday 17:00
New York time and opens Sunday 17:00; between those the label is "akhir pekan". Crypto trades
then too, so the label is information, not a block.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

# key, label, time zone, opening hour, closing hour (local)
SESSIONS = (
    ("sydney", "Sydney", "Australia/Sydney", 7, 16),
    ("tokyo", "Tokyo", "Asia/Tokyo", 9, 18),
    ("london", "London", "Europe/London", 8, 17),
    ("newyork", "New York", "America/New_York", 8, 17),
)
LABELS = {key: label for key, label, *_ in SESSIONS}
_ZONES = {key: ZoneInfo(zone) for key, _, zone, *_ in SESSIONS}
_NY = _ZONES["newyork"]
WEEKEND = "akhir pekan"
OFF = "di luar sesi utama"


def _local(ts: float, key: str) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, dt.UTC).astimezone(_ZONES[key])


def weekend(ts: float) -> bool:
    """Forex is shut: from Friday 17:00 to Sunday 17:00 in New York."""
    ny = dt.datetime.fromtimestamp(ts, dt.UTC).astimezone(_NY)
    day, hour = ny.weekday(), ny.hour
    return (day == 4 and hour >= 17) or day == 5 or (day == 6 and hour < 17)


def active(ts: float) -> list[str]:
    """The sessions open at ``ts`` (Unix seconds), in the order the day reaches them."""
    if weekend(ts):
        return []
    out = []
    for key, _, _, start, end in SESSIONS:
        local = _local(ts, key)
        if local.weekday() < 5 and start <= local.hour < end:
            out.append(key)
    return out


def key(ts: float) -> str:
    """One stable name per combination, for counting: ``london+newyork``, ``tokyo``, ``off``, ``weekend``."""
    if weekend(ts):
        return "weekend"
    return "+".join(active(ts)) or "off"


def label(ts: float) -> str:
    if weekend(ts):
        return WEEKEND
    names = [LABELS[k] for k in active(ts)]
    return " + ".join(names) if names else OFF


def label_of(name: str) -> str:
    """The label for a ``key()`` value."""
    if name == "weekend":
        return WEEKEND
    if name == "off":
        return OFF
    return " + ".join(LABELS.get(part, part) for part in name.split("+"))


ROLLOVER_HOUR = 17          # New York: the forex day ends here, swaps are booked, spreads widen
_offset_cache: dict[tuple[str, int], float] = {}


def utc_offset(ts: float, key: str = "newyork") -> float:
    """Seconds the city's clock is ahead of UTC at ``ts`` (negative behind). Daylight saving moves it.

    Kept per quarter hour: no zone here changes its offset more finely than that.
    """
    slot = (key, int(ts // 900))
    if slot not in _offset_cache:
        if len(_offset_cache) > 400_000:
            _offset_cache.clear()
        offset = _local(slot[1] * 900.0, key).utcoffset()
        _offset_cache[slot] = offset.total_seconds() if offset is not None else 0.0
    return _offset_cache[slot]


def local_seconds(ts: float, key: str = "newyork") -> float:
    """The city's wall clock as seconds since the epoch: divide by 86400 for its day, take the rest for its hour."""
    return ts + utc_offset(ts, key)


def trading_day(ts: float) -> int:
    """The forex day ``ts`` belongs to, as a day number. A day runs from 17:00 to 17:00 in New York,
    so Sunday's open already counts as Monday, and daylight saving moves the boundary with the market."""
    return int((local_seconds(ts) + (24 - ROLLOVER_HOUR) * 3600) // 86400)


def describe(ts: float) -> dict[str, object]:
    utc = dt.datetime.fromtimestamp(ts, dt.UTC)
    return {"key": key(ts), "label": label(ts), "utc": utc.isoformat(timespec="seconds"),
            "new_york": _local(ts, "newyork").strftime("%a %H:%M"), "london": _local(ts, "london").strftime("%a %H:%M"),
            "tokyo": _local(ts, "tokyo").strftime("%a %H:%M"), "overlap": len(active(ts)) > 1}


__all__ = ["SESSIONS", "LABELS", "active", "key", "label", "label_of", "describe", "weekend", "utc_offset", "local_seconds",
           "trading_day", "ROLLOVER_HOUR"]
