"""Unit conversion, dates, time zones, calendars, and cron."""

from __future__ import annotations

import calendar
import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catalog import group, param, spec

group("units", "Unit conversion",
      "konversi, convert, ubah, satuan, unit, meter, km, kilometer, mil, mile, inch, inci, kaki, feet, kg, gram, ons, "
      "pon, pound, liter, ml, galon, gallon, celsius, fahrenheit, kelvin, derajat, km/jam, mph, knot, byte, kb, mb, gb, tb, "
      "hektar, hectare, are, kalori, joule, kwh, psi, bar, atm")
group("time", "Dates, time zones and calendars",
      "tanggal, date, hari, day, minggu, week, bulan, month, tahun, year, jam, hour, menit, minute, detik, second, "
      "zona waktu, timezone, wib, wita, wit, utc, gmt, umur, usia, age, kalender, calendar, deadline, tenggat, "
      "hari kerja, workday, cron, jadwal, schedule, hijriah, hijri, unix, timestamp, epoch, sekarang, now")

# Every unit as (dimension, factor to the base unit). Temperature is handled apart.
_U: dict[str, tuple[str, float]] = {}


def _add(dimension: str, factor: float, *names: str) -> None:
    for n in names:
        _U[n.lower()] = (dimension, factor)


for _names, _f in ((("m", "meter", "metre", "meters"), 1), (("km", "kilometer", "kilometre"), 1000),
                   (("cm", "centimeter", "sentimeter"), 0.01), (("mm", "millimeter", "milimeter"), 0.001),
                   (("um", "micrometer", "mikrometer"), 1e-6), (("nm", "nanometer"), 1e-9),
                   (("dm", "decimeter", "desimeter"), 0.1), (("dam", "dekameter"), 10), (("hm", "hektometer"), 100),
                   (("in", "inch", "inci", "inches"), 0.0254), (("ft", "foot", "feet", "kaki"), 0.3048),
                   (("yd", "yard"), 0.9144), (("mi", "mile", "mil", "miles"), 1609.344),
                   (("nmi", "nautical_mile", "mil_laut"), 1852), (("ly", "light_year", "tahun_cahaya"), 9.4607e15),
                   (("au",), 1.495978707e11)):
    _add("length", _f, *_names)
for _names, _f in ((("kg", "kilogram"), 1), (("g", "gram"), 0.001), (("mg", "milligram", "miligram"), 1e-6),
                   (("t", "ton", "tonne"), 1000), (("kuintal", "quintal"), 100), (("ons", "ons_id"), 0.1),
                   (("hg", "hektogram"), 0.1), (("dag", "dekagram"), 0.01), (("lb", "pound", "pon", "lbs"), 0.45359237),
                   (("oz", "ounce"), 0.028349523125), (("st", "stone"), 6.35029318), (("ct", "carat", "karat"), 0.0002)):
    _add("mass", _f, *_names)
for _names, _f in ((("l", "liter", "litre"), 1), (("ml", "milliliter", "mililiter", "cc"), 0.001),
                   (("m3", "cubic_meter", "meter_kubik"), 1000), (("cm3",), 0.001), (("dm3",), 1),
                   (("gal", "gallon", "galon"), 3.785411784), (("qt", "quart"), 0.946352946), (("pt", "pint"), 0.473176473),
                   (("cup", "gelas"), 0.24), (("floz", "fl_oz"), 0.0295735295625), (("tbsp", "sdm"), 0.015),
                   (("tsp", "sdt"), 0.005), (("barrel", "bbl"), 158.987294928)):
    _add("volume", _f, *_names)
for _names, _f in ((("m2", "square_meter", "meter_persegi"), 1), (("km2",), 1e6), (("cm2",), 1e-4),
                   (("ha", "hectare", "hektar"), 1e4), (("are", "a"), 100), (("acre", "ekar"), 4046.8564224),
                   (("ft2", "sqft"), 0.09290304), (("mi2",), 2589988.110336)):
    _add("area", _f, *_names)
for _names, _f in ((("m/s", "mps"), 1), (("km/h", "kmh", "km/jam", "kph"), 1 / 3.6), (("mph",), 0.44704),
                   (("knot", "kn", "knots"), 0.514444), (("ft/s",), 0.3048), (("mach",), 343)):
    _add("speed", _f, *_names)
for _names, _f in ((("s", "sec", "second", "detik"), 1), (("ms", "millisecond"), 0.001), (("min", "minute", "menit"), 60),
                   (("h", "hr", "hour", "jam"), 3600), (("day", "hari", "d"), 86400), (("week", "minggu", "wk"), 604800),
                   (("month", "bulan"), 2629746), (("year", "tahun", "yr"), 31556952)):
    _add("time", _f, *_names)
for _names, _f in ((("b", "byte", "bytes"), 1), (("bit", "bits"), 0.125), (("kb", "kilobyte"), 1e3), (("mb", "megabyte"), 1e6),
                   (("gb", "gigabyte"), 1e9), (("tb", "terabyte"), 1e12), (("kib",), 1024), (("mib",), 1024 ** 2),
                   (("gib",), 1024 ** 3), (("tib",), 1024 ** 4)):
    _add("data", _f, *_names)
for _names, _f in ((("j", "joule"), 1), (("kj", "kilojoule"), 1e3), (("cal", "kalori_kecil"), 4.184),
                   (("kcal", "kalori", "calorie", "kkal"), 4184), (("wh",), 3600), (("kwh",), 3.6e6), (("ev",), 1.602176634e-19),
                   (("btu",), 1055.05585)):
    _add("energy", _f, *_names)
for _names, _f in ((("pa", "pascal"), 1), (("kpa",), 1e3), (("mpa",), 1e6), (("bar",), 1e5), (("mbar",), 100),
                   (("atm",), 101325), (("psi",), 6894.757293168), (("mmhg", "torr"), 133.322387415)):
    _add("pressure", _f, *_names)
for _names, _f in ((("w", "watt"), 1), (("kw", "kilowatt"), 1e3), (("mw", "megawatt"), 1e6), (("hp", "pk", "horsepower"), 745.7)):
    _add("power", _f, *_names)
for _names, _f in ((("rad", "radian"), 1), (("deg", "degree", "derajat"), math.pi / 180), (("grad",), math.pi / 200),
                   (("turn", "putaran"), 2 * math.pi)):
    _add("angle", _f, *_names)
for _names, _f in ((("km/l", "kmpl"), 1), (("mpg",), 0.425143707)):
    _add("fuel", _f, *_names)

_TEMPS = {"c": "c", "celsius": "c", "°c": "c", "f": "f", "fahrenheit": "f", "°f": "f", "k": "k", "kelvin": "k",
          "r": "r", "reamur": "r", "réaumur": "r"}


def _to_c(v: float, u: str) -> float:
    return {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15, "r": v * 5 / 4}[u]


def _from_c(v: float, u: str) -> float:
    return {"c": v, "f": v * 9 / 5 + 32, "k": v + 273.15, "r": v * 4 / 5}[u]


def _unit(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("²", "2").replace("³", "3")


@spec("unit_convert", "Convert a value between units: length, mass, volume, area, speed, time, data size, energy, pressure, "
      "power, angle, fuel economy, and temperature (C, F, K, Reamur). Indonesian units included (ons, kuintal, sdm, hektar).",
      "units", [param("value", "num"), param("from_unit", description="e.g. 'km', 'lb', 'celsius', 'km/jam'."),
                param("to_unit", description="e.g. 'mi', 'kg', 'fahrenheit', 'm/s'.")],
      keywords="konversi, convert, ubah satuan, unit", lead=True)
def unit_convert(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    a, b = _unit(from_unit), _unit(to_unit)
    if a in _TEMPS and b in _TEMPS:
        result = _from_c(_to_c(float(value), _TEMPS[a]), _TEMPS[b])
        return {"result": round(result, 6), "from": from_unit, "to": to_unit, "dimension": "temperature"}
    if a not in _U or b not in _U:
        unknown = a if a not in _U else b
        close = [u for u in _U if u.startswith(unknown[:2])][:8]
        raise ValueError(f"unknown unit '{unknown}'" + (f"; did you mean {', '.join(close)}" if close else ""))
    (da, fa), (db, fb) = _U[a], _U[b]
    if da != db:
        raise ValueError(f"cannot convert {da} ({from_unit}) to {db} ({to_unit})")
    result = float(value) * fa / fb
    return {"result": float(f"{result:.10g}"), "from": from_unit, "to": to_unit, "dimension": da}


@spec("unit_list", "List the units unit_convert knows, grouped by dimension.", "units",
      [param("dimension", description="Optional: length, mass, volume, area, speed, time, data, energy, pressure, power, angle.",
         required=False, default="")])
def unit_list(dimension: str = "") -> dict[str, Any]:
    by: dict[str, list[str]] = {}
    for name, (dim, _) in _U.items():
        by.setdefault(dim, []).append(name)
    by["temperature"] = sorted(set(_TEMPS))
    return {"units": {dimension: by.get(dimension, [])} if dimension else by}


# ── Time ──────────────────────────────────────────────────────────────────────
_TZ_ALIASES = {"wib": "Asia/Jakarta", "wita": "Asia/Makassar", "wit": "Asia/Jayapura", "jakarta": "Asia/Jakarta",
               "utc": "UTC", "gmt": "UTC", "z": "UTC", "jst": "Asia/Tokyo", "sgt": "Asia/Singapore", "myt": "Asia/Kuala_Lumpur",
               "est": "America/New_York", "edt": "America/New_York", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
               "cet": "Europe/Paris", "bst": "Europe/London", "ist": "Asia/Kolkata", "aest": "Australia/Sydney",
               "kst": "Asia/Seoul", "hkt": "Asia/Hong_Kong", "local": "Asia/Jakarta"}
_DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
              "November", "Desember"]


def zone(name: str) -> ZoneInfo:
    key = (name or "local").strip()
    try:
        return ZoneInfo(_TZ_ALIASES.get(key.lower(), key))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone '{name}'; use an IANA name like Asia/Jakarta or WIB/WITA/WIT/UTC") from exc


_ID_MONTH_MAP = {m.lower(): i + 1 for i, m in enumerate(_MONTHS_ID)} | {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "agu": 8, "agt": 8, "ags": 8, "agust": 8, "sep": 9,
    "sept": 9, "okt": 10, "nov": 11, "des": 12, "nop": 11, "pebruari": 2, "nopember": 11}


def parse_date(text: str, tz: ZoneInfo | None = None) -> datetime:
    """ISO dates, '17 Agustus 2026', '17/08/2026' (day first), 'today', 'besok', '+3 days'."""
    from dateutil import parser as dparser

    raw = str(text).strip()
    low = raw.lower()
    now = datetime.now(tz or zone("local"))
    words = {"today": 0, "hari ini": 0, "now": 0, "sekarang": 0, "tomorrow": 1, "besok": 1, "yesterday": -1,
             "kemarin": -1, "lusa": 2}
    if low in words:
        return now + timedelta(days=words[low])
    m = re.fullmatch(r"([+-]\d+)\s*(d|day|days|hari|w|week|weeks|minggu)", low)
    if m:
        n = int(m.group(1)) * (7 if m.group(2)[0] in "wm" else 1)
        return now + timedelta(days=n)
    for name, number in sorted(_ID_MONTH_MAP.items(), key=lambda kv: -len(kv[0])):
        low = re.sub(rf"(?<![a-z]){name}(?![a-z])", f" {number:02d} ", low)
    low = re.sub(r"\b(senin|selasa|rabu|kamis|jumat|jum'at|sabtu|minggu|pukul|jam|tanggal|tgl)\b", " ", low)
    low = re.sub(r"(\d{1,2})\s+(\d{2})\s+(\d{4})", r"\1/\2/\3", low.strip())
    parsed: datetime
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", low):
        parsed = dparser.isoparse(low.upper().replace(" ", "T", 1)) if "t" in low or " " in low else dparser.isoparse(low)
    else:
        parsed = dparser.parse(low, dayfirst=True)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz or zone("local"))


def describe(dt: datetime) -> dict[str, Any]:
    return {"iso": dt.isoformat(), "date": dt.date().isoformat(), "time": dt.strftime("%H:%M:%S"),
            "weekday": dt.strftime("%A"), "hari": _DAYS_ID[dt.weekday()],
            "tanggal": f"{_DAYS_ID[dt.weekday()]}, {dt.day} {_MONTHS_ID[dt.month - 1]} {dt.year}",
            "tz": str(dt.tzinfo), "iso_week": dt.isocalendar().week, "day_of_year": dt.timetuple().tm_yday}


@spec("date_info", "Everything about one date: weekday (English and Indonesian), ISO week, day of year, leap year, "
      "days until or since today. Accepts '2026-08-17', '17 Agustus 2026', '17/08/2026', 'besok', '+10 hari'.",
      "time", [param("value", description="The date."), param("timezone", required=False, default="WIB")],
      keywords="hari apa, what day, tanggal berapa, weekday, minggu ke, week number", lead=True)
def date_info(value: str, timezone: str = "WIB") -> dict[str, Any]:
    tz = zone(timezone)
    dt = parse_date(value, tz)
    today = datetime.now(tz).date()
    delta = (dt.date() - today).days
    return {**describe(dt), "leap_year": calendar.isleap(dt.year), "days_from_today": delta,
            "relative": "today" if delta == 0 else (f"in {delta} days" if delta > 0 else f"{-delta} days ago")}


@spec("date_diff", "Exact difference between two dates or datetimes: days, weeks, months, years, hours, and a "
      "'X years Y months Z days' breakdown. Optionally counts working days (Mon-Fri).",
      "time", [param("start"), param("end", required=False, default="today"),
               param("timezone", required=False, default="WIB")],
      keywords="selisih, selisih hari, berapa hari, berapa lama, how many days, difference, jarak tanggal, between", lead=True)
def date_diff(start: str, end: str = "today", timezone: str = "WIB") -> dict[str, Any]:
    from dateutil.relativedelta import relativedelta

    tz = zone(timezone)
    a, b = parse_date(start, tz), parse_date(end, tz)
    sign = 1 if b >= a else -1
    lo, hi = (a, b) if sign == 1 else (b, a)
    rd = relativedelta(hi, lo)
    seconds = (hi - lo).total_seconds()
    return {"start": a.isoformat(), "end": b.isoformat(), "direction": "forward" if sign == 1 else "backward",
            "days": (hi.date() - lo.date()).days, "weeks": round((hi.date() - lo.date()).days / 7, 2),
            "hours": round(seconds / 3600, 2), "total_months": rd.years * 12 + rd.months,
            "breakdown": f"{rd.years} years {rd.months} months {rd.days} days",
            "working_days": _workdays(lo.date(), hi.date())}


def _workdays(a: date, b: date, holidays: set[date] | None = None) -> int:
    days = (b - a).days
    full, rest = divmod(days, 7)
    count = full * 5
    for i in range(rest):
        if (a + timedelta(days=full * 7 + i)).weekday() < 5:
            count += 1
    if holidays:
        count -= sum(1 for h in holidays if a <= h < b and h.weekday() < 5)
    return count


@spec("date_add", "Add or subtract time from a date: days, weeks, months, years, hours, minutes, or working days "
      "(skipping Saturday, Sunday, and any holidays given).",
      "time", [param("start", required=False, default="today"), param("amount", "num", "Negative to go back."),
               param("unit", enum=["days", "weeks", "months", "years", "hours", "minutes", "workdays"], required=False, default="days"),
               param("holidays", "list", "Dates to skip for workdays (YYYY-MM-DD).", required=False),
               param("timezone", required=False, default="WIB")],
      keywords="tambah hari, add days, setelah, after, sebelum, before, kemudian, later, deadline, jatuh tempo, due date")
def date_add(amount: float, start: str = "today", unit: str = "days", holidays: list[str] | None = None,
             timezone: str = "WIB") -> dict[str, Any]:
    from dateutil.relativedelta import relativedelta

    tz = zone(timezone)
    dt = parse_date(start, tz)
    if unit == "workdays":
        skip = {parse_date(h, tz).date() for h in holidays or []}
        step = 1 if amount >= 0 else -1
        left = abs(int(amount))
        while left:
            dt += timedelta(days=step)
            if dt.weekday() < 5 and dt.date() not in skip:
                left -= 1
    elif unit in ("months", "years"):
        n = int(amount)
        dt = dt + (relativedelta(months=n) if unit == "months" else relativedelta(years=n))
    else:
        dt = dt + timedelta(**{unit: float(amount)})
    return describe(dt)


@spec("timezone_convert", "Convert a time between time zones (WIB, WITA, WIT, UTC, or IANA names like Europe/London). "
      "Also shows the same moment in several zones.",
      "time", [param("value", description="e.g. '2026-11-09 19:30' or 'now'."), param("from_tz", required=False, default="WIB"),
               param("to_tz", "list", "Target zones.", required=False, items="str")],
      keywords="zona waktu, timezone, time zone, wib, wita, wit, utc, gmt, jam di, time in, selisih waktu", lead=True)
def timezone_convert(value: str, from_tz: str = "WIB", to_tz: list[str] | None = None) -> dict[str, Any]:
    src = zone(from_tz)
    dt = parse_date(value, src) if value.strip().lower() not in ("now", "sekarang") else datetime.now(src)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=src)
    targets = to_tz if isinstance(to_tz, list) and to_tz else ([to_tz] if isinstance(to_tz, str) and to_tz else
                                                               ["UTC", "WIB", "WITA", "WIT"])
    out = []
    for name in targets:
        local = dt.astimezone(zone(name))
        offset = local.utcoffset() or timedelta()
        hours = offset.total_seconds() / 3600
        out.append({"zone": name, "time": local.strftime("%Y-%m-%d %H:%M"), "weekday": local.strftime("%A"),
                    "utc_offset": f"UTC{'+' if hours >= 0 else '-'}{abs(hours):g}"})
    return {"source": dt.isoformat(), "converted": out}


@spec("unix_time", "Convert between Unix timestamps (seconds or milliseconds) and readable dates.",
      "time", [param("value", description="A timestamp like 1762700000 (or ms), or a date to turn into one."),
               param("timezone", required=False, default="WIB")],
      keywords="unix, timestamp, epoch, unixtime")
def unix_time(value: str, timezone: str = "WIB") -> dict[str, Any]:
    tz = zone(timezone)
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        n = float(text)
        if abs(n) > 1e11:
            n /= 1000
        dt = datetime.fromtimestamp(n, tz)
        return {"timestamp": n, **describe(dt), "utc": datetime.fromtimestamp(n, UTC).isoformat()}
    dt = parse_date(text, tz)
    return {"timestamp": int(dt.timestamp()), "timestamp_ms": int(dt.timestamp() * 1000), **describe(dt)}


@spec("age_calc", "Exact age from a birth date: years, months, days, total days, next birthday and weekday of birth.",
      "time", [param("birth_date"), param("on_date", required=False, default="today")],
      keywords="umur, usia, age, lahir, born, ulang tahun, birthday")
def age_calc(birth_date: str, on_date: str = "today") -> dict[str, Any]:
    from dateutil.relativedelta import relativedelta

    tz = zone("WIB")
    born, at = parse_date(birth_date, tz).date(), parse_date(on_date, tz).date()
    if born > at:
        raise ValueError("birth date is after the reference date")
    rd = relativedelta(at, born)
    next_bd = born.replace(year=at.year) if not (born.month == 2 and born.day == 29) else date(at.year, 3, 1)
    if next_bd <= at:
        next_bd = next_bd.replace(year=at.year + 1)
    return {"years": rd.years, "months": rd.months, "days": rd.days, "total_days": (at - born).days,
            "born_on": _DAYS_ID[born.weekday()], "next_birthday": next_bd.isoformat(),
            "days_to_next_birthday": (next_bd - at).days, "turning": rd.years + 1}


@spec("calendar_month", "A month's calendar as text (Monday first), with Indonesian day names, and the weekday of the 1st.",
      "time", [param("year", "int"), param("month", "int", "1-12")], keywords="kalender, calendar, bulan ini, month view")
def calendar_month(year: int, month: int) -> dict[str, Any]:
    if not 1 <= int(month) <= 12:
        raise ValueError("month 1..12")
    cal = calendar.TextCalendar(calendar.MONDAY).formatmonth(int(year), int(month))
    weeks = calendar.Calendar(calendar.MONDAY).monthdayscalendar(int(year), int(month))
    return {"text": cal, "weeks": weeks, "days_in_month": calendar.monthrange(int(year), int(month))[1],
            "first_day": _DAYS_ID[calendar.weekday(int(year), int(month), 1)],
            "month_name": _MONTHS_ID[int(month) - 1]}


def _gregorian_to_jd(y: int, m: int, d: int) -> int:
    a = (14 - m) // 12
    y2, m2 = y + 4800 - a, m + 12 * a - 3
    return d + (153 * m2 + 2) // 5 + 365 * y2 + y2 // 4 - y2 // 100 + y2 // 400 - 32045


_HIJRI_MONTHS = ["Muharram", "Safar", "Rabiul Awal", "Rabiul Akhir", "Jumadil Awal", "Jumadil Akhir", "Rajab",
                 "Syaban", "Ramadan", "Syawal", "Dzulkaidah", "Dzulhijjah"]


@spec("hijri_date", "Convert a Gregorian date to the Islamic (Hijri) calendar using the arithmetical (tabular) calendar. "
      "The official date in Indonesia (rukyat/hisab) can differ by a day.",
      "time", [param("value", required=False, default="today")], keywords="hijriah, hijri, islam, ramadan, puasa, lebaran, muharram")
def hijri_date(value: str = "today") -> dict[str, Any]:
    d = parse_date(value, zone("WIB")).date()
    jd = _gregorian_to_jd(d.year, d.month, d.day)
    l_ = jd - 1948440 + 10632
    n = (l_ - 1) // 10631
    l_ = l_ - 10631 * n + 354
    j = ((10985 - l_) // 5316) * ((50 * l_) // 17719) + (l_ // 5670) * ((43 * l_) // 15238)
    l_ = l_ - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    month = (24 * l_) // 709
    day = l_ - (709 * month) // 24
    year = 30 * n + j - 30
    return {"gregorian": d.isoformat(), "hijri": f"{day} {_HIJRI_MONTHS[month - 1]} {year} H",
            "day": day, "month": month, "month_name": _HIJRI_MONTHS[month - 1], "year": year,
            "note": "tabular calculation; official dates may differ by one day"}


_PASARAN = ["Legi", "Pahing", "Pon", "Wage", "Kliwon"]
_NEPTU_HARI = {"Minggu": 5, "Senin": 4, "Selasa": 3, "Rabu": 7, "Kamis": 8, "Jumat": 6, "Sabtu": 9}
_NEPTU_PASARAN = {"Legi": 5, "Pahing": 9, "Pon": 7, "Wage": 4, "Kliwon": 8}


@spec("weton", "Javanese weton of a date: weekday plus pasaran (Legi, Pahing, Pon, Wage, Kliwon) and neptu.",
      "time", [param("value")], keywords="weton, pasaran, neptu, jawa, legi, pahing, pon, wage, kliwon")
def weton(value: str) -> dict[str, Any]:
    d = parse_date(value, zone("WIB")).date()
    # Julian Day mod 5 gives the pasaran: 17 Aug 1945 is Jumat Legi, 8 Jun 1921 Rabu Kliwon.
    pasaran = _PASARAN[_gregorian_to_jd(d.year, d.month, d.day) % 5]
    hari = _DAYS_ID[d.weekday()]
    return {"date": d.isoformat(), "weton": f"{hari} {pasaran}",
            "neptu": _NEPTU_HARI[hari] + _NEPTU_PASARAN[pasaran]}


# ── Cron ──────────────────────────────────────────────────────────────────────
_CRON_NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
               "nov": 11, "dec": 12, "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_CRON_MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
                "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}


def _cron_field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in text.lower().split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ValueError("cron step must be >= 1")
        for name, number in _CRON_NAMES.items():
            part = part.replace(name, str(number))
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = int(part)
            b = hi if step > 1 else a
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron value {part} outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def cron_parse(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    text = _CRON_MACROS.get(expr.strip().lower(), expr.strip())
    fields = text.split()
    if len(fields) != 5:
        raise ValueError("a cron expression has 5 fields: minute hour day month weekday")
    minute, hour, dom, month, dow = fields
    dows = {d % 7 for d in _cron_field(dow, 0, 7)}
    return (_cron_field(minute, 0, 59), _cron_field(hour, 0, 23), _cron_field(dom, 1, 31), _cron_field(month, 1, 12),
            dows, dom != "*", dow != "*")


@spec("cron_next", "Explain a 5-field cron expression and list its next run times (Vixie cron rules: when both day-of-month "
      "and weekday are set, either may match).",
      "time", [param("expression", description="e.g. '*/15 9-17 * * mon-fri' or '@daily'."),
               param("count", "int", required=False, default=5), param("timezone", required=False, default="WIB"),
               param("start", required=False, default="now")],
      keywords="cron, crontab, jadwal, schedule, setiap, every, berkala, recurring")
def cron_next(expression: str, count: int = 5, timezone: str = "WIB", start: str = "now") -> dict[str, Any]:
    minutes, hours, doms, months, dows, dom_set, dow_set = cron_parse(expression)
    tz = zone(timezone)
    t = (datetime.now(tz) if start in ("now", "sekarang") else parse_date(start, tz)).replace(second=0, microsecond=0)
    t += timedelta(minutes=1)
    runs: list[str] = []
    limit = t + timedelta(days=366 * 5)
    while len(runs) < min(int(count), 50) and t < limit:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        day_ok_dom = t.day in doms
        day_ok_dow = ((t.weekday() + 1) % 7) in dows
        day_ok = (day_ok_dom or day_ok_dow) if (dom_set and dow_set) else (day_ok_dom and day_ok_dow)
        if not day_ok:
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
            continue
        if t.minute in minutes:
            runs.append(t.strftime("%Y-%m-%d %H:%M %a"))
        t += timedelta(minutes=1)
    return {"expression": expression, "next_runs": runs, "explain": _cron_explain(minutes, hours, doms, months, dows)}


def _cron_explain(minutes: set[int], hours: set[int], doms: set[int], months: set[int], dows: set[int]) -> str:
    def show(values: set[int], full: int) -> str:
        return "every" if len(values) == full else ",".join(str(v) for v in sorted(values))[:60]

    return (f"minute {show(minutes, 60)}; hour {show(hours, 24)}; day {show(doms, 31)}; month {show(months, 12)}; "
            f"weekday {show(dows, 7)} (0=Sunday)")


@spec("countdown", "Time left until a moment (or since it), in days, hours and minutes.",
      "time", [param("target"), param("timezone", required=False, default="WIB")],
      keywords="countdown, hitung mundur, berapa lama lagi, how long until, sisa waktu, tinggal berapa hari")
def countdown(target: str, timezone: str = "WIB") -> dict[str, Any]:
    tz = zone(timezone)
    goal = parse_date(target, tz)
    left = goal - datetime.now(tz)
    seconds = int(left.total_seconds())
    past = seconds < 0
    seconds = abs(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return {"target": goal.isoformat(), "past": past, "days": d, "hours": h, "minutes": m,
            "text": f"{'since' if past else 'in'} {d} days {h} hours {m} minutes"}
