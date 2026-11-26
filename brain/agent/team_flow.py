"""Rules for flow nodes: triggers, conditions, and validating what a node is set to.

Everything here is plain logic with no I/O, so a schedule or a gate can be
checked without a model, a server, or a clock that really reaches 07:00.

A gate is n8n's IF: up to five conditions joined by AND or OR, out on A when
they hold and B when they don't. A switch is n8n's Switch: the first rule that
holds picks A, B or C, and D catches the rest. A merge joins paths, waiting for
any of them or all of them.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

NODE_KINDS = ("agent", "delay", "gate", "switch", "merge", "set", "http", "telegram", "database", "datatable")
# "flow": runs exactly along the connections drawn, like n8n. "team": the older lead and reviewer model.
FLOW_STYLES = ("flow", "team")
TRIGGER_ID = "__trigger"
TELEGRAM_STYLES = ("auto", "plain")
# "web" opens the full content in Telegram's in-app window, "link" opens any address.
TELEGRAM_BUTTONS = ("web", "link")
DATATABLE_OPERATIONS = ("get", "insert", "update", "upsert", "delete")
DATATABLE_OPS = ("=", "!=", ">", ">=", "<", "<=", "contains", "startswith")
BRANCHING_KINDS = ("gate", "switch")
GATE_MODES = ("contains", "not_contains", "equals", "regex", "empty", "time", "ai")
GATE_COMBINES = ("and", "or")
MERGE_MODES = ("any", "all")
HTTP_METHODS = ("GET", "POST")
SCHEDULE_MODES = ("off", "interval", "daily", "cron", "once", "telegram")
AUDIENCES = ("owner", "public")
MAX_DATA_GRANTS = 50
MAX_HIDDEN_COLUMNS = 60
DEFAULT_HISTORY_TURNS = 6
MAX_HISTORY_TURNS = 20
DEFAULT_DAILY_CAP = 300
MAX_DAILY_CAP = 5000
BRANCHES = ("A", "B", "C", "D")
MAX_CONDITIONS = 20
MAX_SWITCH_RULES = 3
MAX_DAILY_TIMES = 48
MAX_TEMPLATE_CHARS = 8000
MAX_DELAY_SECONDS = 3600
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 7 * 24 * 60
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]

_TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_MOMENT_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]([01]\d|2[0-3]):[0-5]\d")
_KEYWORD_RE = re.compile(r"/?[\w-]{1,32}")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")
_DAY_NAMES = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
_CHAT_ID_RE = re.compile(r"-?\d{1,20}|@\w{1,64}")
_TABLE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_YES_RE = re.compile(r"\W*(ya|iya|yes|benar|true)\b", re.IGNORECASE)
_NO_RE = re.compile(r"\W*(tidak|bukan|no|false)\b", re.IGNORECASE)


def _clock(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _TIME_RE.fullmatch(text):
        raise ValueError(f"{label} harus berformat JJ:MM, misalnya 07:00.")
    return text


def _days(value: Any) -> list[int]:
    days = sorted({int(day) for day in value}) if value else list(ALL_DAYS)
    if not days or any(day not in ALL_DAYS for day in days):
        raise ValueError("Hari harus di antara 0 (Senin) dan 6 (Minggu).")
    return days


def _times(schedule: dict[str, Any]) -> list[str]:
    """Daily times, from a list or one comma-separated string like "05:00, 12:00"."""
    raw = schedule.get("times") or schedule.get("time") or ""
    items = raw if isinstance(raw, list) else str(raw).split(",")
    times = sorted({_clock(item, "Jam jadwal") for item in items if str(item).strip()})
    if not times:
        raise ValueError("Jam jadwal harus berformat JJ:MM, misalnya 07:00.")
    if len(times) > MAX_DAILY_TIMES:
        raise ValueError(f"Maksimal {MAX_DAILY_TIMES} jam dalam sehari.")
    return times


def _bounded(value: Any, default: int, low: int, high: int, label: str) -> int:
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} harus berupa angka.") from None
    if not low <= number <= high:
        raise ValueError(f"{label} harus {low} sampai {high}.")
    return number


def normalize_schedule(schedule: dict[str, Any] | None) -> dict[str, Any]:
    """Check a team's trigger. Raises ValueError with a message for the user."""
    schedule = schedule or {}
    mode = str(schedule.get("mode") or "off")
    if mode not in SCHEDULE_MODES:
        raise ValueError(f"Jadwal tidak dikenal: {mode}.")
    if mode == "off":
        return {"mode": "off"}
    task = str(schedule.get("task") or "").strip()[:4000]
    if mode == "telegram":
        # The message itself is the task, so a fixed task is only extra context.
        keyword = str(schedule.get("keyword") or "").strip()
        if keyword and not _KEYWORD_RE.fullmatch(keyword):
            raise ValueError("Kata pemicu Telegram satu kata saja, misalnya /jadwal.")
        audience = str(schedule.get("audience") or "owner")
        if audience not in AUDIENCES:
            raise ValueError("Penerima pesan Telegram hanya kamu atau siapa saja (publik).")
        # Every Telegram flow is a chat: it remembers the last turns of each chat, the owner's too.
        history = _bounded(schedule.get("history"), DEFAULT_HISTORY_TURNS, 0, MAX_HISTORY_TURNS, "Ingatan obrolan")
        if audience == "owner":
            return {"mode": mode, "keyword": keyword, "task": task, "history": history}
        return {
            "mode": mode, "keyword": keyword, "task": task, "audience": audience, "history": history,
            "daily_cap": _bounded(schedule.get("daily_cap"), DEFAULT_DAILY_CAP, 1, MAX_DAILY_CAP, "Batas balasan per hari"),
        }
    if not task:
        raise ValueError("Jadwal butuh tugas yang akan dijalankan.")
    if mode == "interval":
        minutes = int(schedule.get("minutes") or 0)
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            raise ValueError(f"Interval harus {MIN_INTERVAL_MINUTES} sampai {MAX_INTERVAL_MINUTES} menit.")
        return {"mode": mode, "minutes": minutes, "task": task}
    if mode == "cron":
        expression = " ".join(str(schedule.get("cron") or "").split())
        parse_cron(expression)
        return {"mode": mode, "cron": expression, "task": task}
    if mode == "once":
        moment = str(schedule.get("at") or "").strip()
        if not _MOMENT_RE.fullmatch(moment):
            raise ValueError("Waktu sekali jalan harus berformat TTTT-BB-HH JJ:MM.")
        try:
            datetime.fromisoformat(moment.replace(" ", "T"))
        except ValueError:
            raise ValueError("Tanggal sekali jalan tidak ada di kalender.") from None
        return {"mode": mode, "at": moment.replace(" ", "T"), "task": task}
    times = _times(schedule)
    return {"mode": mode, "time": times[0], "times": times, "days": _days(schedule.get("days")), "task": task}


# ── Cron ──────────────────────────────────────────────────────────────────────
_CRON_FIELDS = (("menit", 0, 59), ("jam", 0, 23), ("tanggal", 1, 31), ("bulan", 1, 12), ("hari", 0, 7))


def _cron_field(text: str, label: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        body, _, step_text = part.partition("/")
        step = int(step_text) if step_text.isdigit() else (1 if not step_text else 0)
        if step < 1:
            raise ValueError(f"Cron: langkah {label} tidak valid.")
        if body == "*":
            start, end = low, high
        elif "-" in body:
            first, _, last = body.partition("-")
            if not (first.isdigit() and last.isdigit()):
                raise ValueError(f"Cron: rentang {label} tidak valid.")
            start, end = int(first), int(last)
        elif body.isdigit():
            start = int(body)
            end = high if step_text else start
        else:
            raise ValueError(f"Cron: nilai {label} tidak valid: {body or '(kosong)'}.")
        if not low <= start <= end <= high:
            raise ValueError(f"Cron: {label} harus {low} sampai {high}.")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expression: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    """Read a five-field cron expression: minute hour day-of-month month day-of-week.

    Day of week counts from Sunday (0 or 7), as in crontab. Returns the sets plus
    whether day of month and day of week were restricted, because crontab fires
    on either of them when both are.
    """
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("Cron butuh 5 bagian: menit jam tanggal bulan hari, misalnya 0 7 * * 1-5.")
    minutes, hours, dom, months, dow = (
        _cron_field(part, label, low, high) for part, (label, low, high) in zip(parts, _CRON_FIELDS, strict=True)
    )
    if 7 in dow:
        dow = (dow - {7}) | {0}
    return minutes, hours, dom, months, dow, parts[2] != "*", parts[4] != "*"


def _cron_next(expression: str, after: float) -> float | None:
    minutes, hours, dom, months, dow, dom_set, dow_set = parse_cron(expression)
    start = datetime.fromtimestamp(after).replace(second=0, microsecond=0)
    for offset in range(367):
        day = start + timedelta(days=offset)
        if day.month not in months:
            continue
        on_dom = day.day in dom
        on_dow = (day.weekday() + 1) % 7 in dow
        if dom_set and dow_set:
            if not (on_dom or on_dow):
                continue
        elif not (on_dom and on_dow):
            continue
        for hour in sorted(hours):
            for minute in sorted(minutes):
                candidate = day.replace(hour=hour, minute=minute)
                if candidate.timestamp() > after:
                    return candidate.timestamp()
    return None


def next_run_after(schedule: dict[str, Any], after: float) -> float | None:
    """When a schedule next fires after a moment, or None if it never does."""
    mode = schedule.get("mode")
    if mode == "interval":
        return after + int(schedule["minutes"]) * 60
    if mode == "cron":
        return _cron_next(str(schedule["cron"]), after)
    if mode == "once":
        moment = datetime.fromisoformat(str(schedule["at"])).timestamp()
        return moment if moment > after else None
    if mode != "daily":
        return None
    start = datetime.fromtimestamp(after)
    times = schedule.get("times") or [schedule["time"]]
    for offset in range(8):
        day = start + timedelta(days=offset)
        if day.weekday() not in schedule.get("days", ALL_DAYS):
            continue
        for clock in sorted(times):
            hour, minute = (int(part) for part in str(clock).split(":"))
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.timestamp() > after:
                return candidate.timestamp()
    return None


def _condition(config: dict[str, Any]) -> dict[str, Any]:
    """Check one condition of a gate or one rule of a switch."""
    mode = str(config.get("mode") or "contains")
    if mode not in GATE_MODES:
        raise ValueError(f"Jenis syarat tidak dikenal: {mode}.")
    if mode == "time":
        return {
            "mode": mode,
            "start": _clock(config.get("start"), "Jam mulai syarat"),
            "end": _clock(config.get("end"), "Jam selesai syarat"),
            "days": _days(config.get("days")),
        }
    if mode == "empty":
        return {"mode": mode}
    value = str(config.get("value") or "").strip()
    if not value:
        raise ValueError("Syarat butuh kata kunci, pola, atau pertanyaan.")
    if mode == "regex":
        if len(value) > 200:
            raise ValueError("Pola regex maksimal 200 karakter.")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"Pola regex tidak valid: {exc}") from None
    return {"mode": mode, "value": value[:1000]}


def _conditions(config: dict[str, Any]) -> list[dict[str, Any]]:
    # Gates saved before AND/OR existed hold one condition in the node itself.
    raw = config.get("conditions") or ([config] if config.get("mode") else [])
    if not raw:
        raise ValueError("Gate butuh minimal satu syarat.")
    if len(raw) > MAX_CONDITIONS:
        raise ValueError(f"Maksimal {MAX_CONDITIONS} syarat dalam satu gate.")
    return [_condition(item) for item in raw]


def uses_ai(config: dict[str, Any]) -> bool:
    """Whether a node asks a model, and so needs one chosen."""
    items = config.get("conditions") or config.get("rules") or []
    return config.get("mode") == "ai" or any(item.get("mode") == "ai" for item in items)


def _data_grants(raw: Any) -> list[dict[str, Any]]:
    """The data sources an agent may use: [{source, access, hide}]. Access is one of the rights in brain.utils.datastore."""
    from brain.utils.datastore.access import RIGHTS

    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_DATA_GRANTS:
        raise ValueError(f"Akses data maksimal {MAX_DATA_GRANTS} sumber per agent.")
    grants: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Akses data tiap sumber berbentuk {source, access}.")
        source = str(item.get("source") or "").strip().lower()
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError("Nama sumber data hanya huruf kecil, angka, - dan _.")
        access = str(item.get("access") or "read")
        if access not in RIGHTS:
            raise ValueError(f"Hak akses data tidak dikenal: {access}. Pilih {', '.join(RIGHTS)}.")
        hide = item.get("hide") or []
        if isinstance(hide, str):
            hide = hide.split(",")
        names = list(dict.fromkeys(str(h).strip()[:60] for h in hide if str(h).strip()))
        if len(names) > MAX_HIDDEN_COLUMNS:
            raise ValueError(f"Kolom yang disembunyikan maksimal {MAX_HIDDEN_COLUMNS} per sumber.")
        grants[source] = {"source": source, "access": access, "hide": names}
    return list(grants.values())


def normalize_node_config(kind: str, config: dict[str, Any] | None, database_ids: tuple[str, ...]) -> dict[str, Any]:
    """Keep only the settings a kind of node uses, and check them."""
    config = config or {}
    if kind == "agent":
        # Only what differs from the default is kept, so teams saved before these settings stay as they were.
        kept: dict[str, Any] = {}
        if config.get("direct"):
            kept["direct"] = True
        if config.get("notify_owner"):
            kept["notify_owner"] = True
        if config.get("report"):
            kept["report"] = True
        grants = _data_grants(config.get("data"))
        if grants:
            kept["data"] = grants
        return kept
    if kind == "delay":
        seconds = int(config.get("seconds") or 0)
        if not 1 <= seconds <= MAX_DELAY_SECONDS:
            raise ValueError(f"Delay harus 1 sampai {MAX_DELAY_SECONDS} detik.")
        return {"seconds": seconds}
    if kind == "gate":
        combine = str(config.get("combine") or "and").lower()
        if combine not in GATE_COMBINES:
            raise ValueError("Gate hanya bisa AND atau OR.")
        return {"combine": combine, "conditions": _conditions(config)}
    if kind == "switch":
        rules = config.get("rules") or []
        if not 1 <= len(rules) <= MAX_SWITCH_RULES:
            raise ValueError(f"Switch butuh 1 sampai {MAX_SWITCH_RULES} aturan.")
        return {"rules": [_condition(rule) for rule in rules]}
    if kind == "merge":
        mode = str(config.get("mode") or "any")
        if mode not in MERGE_MODES:
            raise ValueError("Merge hanya bisa menunggu salah satu atau semua jalur.")
        return {"mode": mode}
    if kind == "set":
        text = str(config.get("text") or "").strip()
        if not text:
            raise ValueError("Node teks butuh isi.")
        return {"text": text[:MAX_TEMPLATE_CHARS]}
    if kind == "http":
        method = str(config.get("method") or "GET").upper()
        if method not in HTTP_METHODS:
            raise ValueError("HTTP hanya GET atau POST.")
        url = str(config.get("url") or "").strip()
        if not re.match(r"https?://[^\s/]+", url) or len(url) > 2000:
            raise ValueError("Alamat HTTP harus diawali http:// atau https://.")
        return {"method": method, "url": url, "body": str(config.get("body") or "")[:MAX_TEMPLATE_CHARS]}
    if kind == "telegram":
        return _telegram_config(config)
    if kind == "database":
        database = str(config.get("database") or "sqlite")
        if database not in database_ids:
            raise ValueError(f"Database tidak dikenal: {database}.")
        table = str(config.get("table") or "hasil_tim").strip()
        if not _TABLE_RE.fullmatch(table):
            raise ValueError("Nama tabel hanya boleh huruf, angka, dan garis bawah.")
        return {"database": database, "table": table}
    if kind == "datatable":
        return _datatable_config(config)
    return {}


def _telegram_config(config: dict[str, Any]) -> dict[str, Any]:
    """A Telegram node: where to, what text, how it looks, and buttons. Only what differs from the default is kept."""
    chat_id = str(config.get("chat_id") or "").strip()
    if chat_id and not _CHAT_ID_RE.fullmatch(chat_id):
        raise ValueError("Chat ID Telegram harus angka atau @nama_channel.")
    kept: dict[str, Any] = {"chat_id": chat_id}
    style = str(config.get("style") or "auto")
    if style not in TELEGRAM_STYLES:
        raise ValueError("Tampilan pesan hanya rapi otomatis atau teks biasa.")
    if style != "auto":
        kept["style"] = style
    text = str(config.get("text") or "").strip()
    if text:
        kept["text"] = text[:MAX_TEMPLATE_CHARS]
    buttons = []
    for item in config.get("buttons") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        kind = str(item.get("kind") or "web")
        url = str(item.get("url") or "").strip()
        if not label and not url:
            continue
        if not label:
            raise ValueError("Tiap tombol Telegram butuh tulisan.")
        if kind not in TELEGRAM_BUTTONS:
            raise ValueError(f"Jenis tombol tidak dikenal: {kind}.")
        if kind == "link" and not re.match(r"(https?://|tg://|\{\{)", url):
            raise ValueError(f"Tombol \"{label}\": tautan harus diawali https:// atau http://.")
        button = {"label": label[:64], "kind": kind}
        if kind == "link":
            button["url"] = url[:2000]
        buttons.append(button)
    if buttons:
        kept["buttons"] = buttons
    title = str(config.get("page_title") or "").strip()
    if title:
        kept["page_title"] = title[:120]
    if config.get("silent"):
        kept["silent"] = True
    if config.get("no_summary"):
        kept["no_summary"] = True
    return kept


def _datatable_column(value: Any, what: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        raise ValueError(f"{what} belum dipilih.")
    return text


def _datatable_config(config: dict[str, Any]) -> dict[str, Any]:
    """A Data table node, like n8n's: one table of a data source, one operation, its conditions and column values."""
    source = str(config.get("source") or "").strip().lower()
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError("Pilih tabel data dulu: sumber datanya belum dipilih.")
    table = str(config.get("table") or "").strip()
    if len(table) > 120:
        raise ValueError("Nama tabel terlalu panjang.")
    operation = str(config.get("operation") or "get")
    if operation not in DATATABLE_OPERATIONS:
        raise ValueError(f"Operasi tabel tidak dikenal: {operation}.")
    combine = str(config.get("combine") or "and").lower()
    if combine not in GATE_COMBINES:
        raise ValueError("Syarat tabel hanya bisa AND atau OR.")
    where = []
    for item in config.get("where") or []:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "=")
        if op not in DATATABLE_OPS:
            raise ValueError(f"Operator syarat tidak dikenal: {op}.")
        if not str(item.get("column") or "").strip():
            continue
        where.append({"column": _datatable_column(item.get("column"), "Kolom syarat"), "op": op,
                      "value": str(item.get("value") or "")[:MAX_TEMPLATE_CHARS]})
    values = []
    for item in config.get("values") or []:
        if isinstance(item, dict) and str(item.get("column") or "").strip():
            values.append({"column": _datatable_column(item.get("column"), "Kolom"),
                           "value": str(item.get("value") or "")[:MAX_TEMPLATE_CHARS]})
    if operation in ("update", "delete") and not where:
        raise ValueError("Ubah dan hapus baris butuh minimal satu syarat, supaya tidak mengenai semua baris.")
    if operation in ("insert", "update", "upsert") and not values:
        raise ValueError("Isi minimal satu kolom untuk baris ini.")
    match = [str(c).strip() for c in config.get("match") or [] if str(c).strip()]
    if operation == "upsert":
        match = [c for c in match if c in {v["column"] for v in values}]
        if not match:
            raise ValueError("Upsert butuh kolom pencocok yang juga diisi nilainya, misalnya kode atau email.")
    kept: dict[str, Any] = {"source": source, "table": table, "operation": operation}
    if where:
        kept["where"] = where
        kept["combine"] = combine
    if values:
        kept["values"] = values
    if match:
        kept["match"] = match
    if operation == "get":
        try:
            limit = int(config.get("limit") or 0)
        except (TypeError, ValueError):
            raise ValueError("Batas baris harus angka.") from None
        if limit < 0:
            raise ValueError("Batas baris tidak boleh negatif.")
        if limit:
            kept["limit"] = limit
        sort = str(config.get("sort") or "").strip()
        if sort:
            kept["sort"] = sort[:64]
            kept["desc"] = bool(config.get("desc"))
        include = [str(r).strip() for r in config.get("include") or [] if str(r).strip()]
        if include:
            kept["include"] = list(dict.fromkeys(include))
    return kept


def json_values(text: str) -> dict[str, str]:
    """``{{json.kolom}}`` for a node whose input is JSON: the fields of an object, or of a list's first object.

    ``{{json}}`` is the whole input again, and ``{{json.0.kolom}}`` reaches into a list by position.
    """
    import json

    body = text.strip()
    # A model often wraps its JSON in a ```json block.
    fenced = re.fullmatch(r"```[a-zA-Z]*\s*(.*?)\s*```", body, re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body) if body[:1] in ("{", "[") else None
    except ValueError:
        data = None
    if data is None:
        return {}
    out: dict[str, str] = {"json": text}

    def show(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def walk(into: dict[str, str], prefix: str, value: Any, depth: int) -> None:
        if depth > 3:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                into[f"{prefix}.{key}"] = show(item)
                walk(into, f"{prefix}.{key}", item, depth + 1)
        elif isinstance(value, list):
            for index, item in enumerate(value[:50]):
                into[f"{prefix}.{index}"] = show(item)
                walk(into, f"{prefix}.{index}", item, depth + 1)

    walk(out, "json", data, 0)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        # A list of rows reads like its first row: {{json.nama}}, {{json.produk.nama}}.
        first: dict[str, str] = {}
        walk(first, "json", data[0], 0)
        for key, item in first.items():
            out.setdefault(key, item)
    out["json.count"] = str(len(data)) if isinstance(data, list) else "1"
    return out


def to_flow(members: list[dict[str, Any]], lead_id: str | None, reviewer_id: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    """A lead-and-reviewer team as a plain flow where every connection is drawn.

    The lead becomes an agent fed by every worker that nobody else reads, the reviewer an agent after the lead that
    fixes the answer itself, and an output left unconnected is fed the final answer. Returns the members and the ids
    the trigger starts.
    """
    import copy

    members = copy.deepcopy(members)
    by_id = {m["id"]: m for m in members}
    for m in members:
        m["depends_on"] = [d for d in m.get("depends_on") or [] if d in by_id]
    lead = lead_id if lead_id in by_id else None
    reviewer = reviewer_id if reviewer_id in by_id and reviewer_id != lead else None
    outputs = {"telegram", "database"}
    after = {m["id"] for m in members if m.get("kind", "agent") in outputs and not m["depends_on"]}
    anchors = {x for x in (lead, reviewer) if x}
    changed = True
    while changed:
        changed = False
        for m in members:
            if m["id"] in after or m["id"] in anchors:
                continue
            if any(d in after or d in anchors for d in m["depends_on"]):
                after.add(m["id"])
                changed = True
    depended = {d for m in members for d in m["depends_on"]}
    workers = [m for m in members if m["id"] not in after and m["id"] not in anchors]
    sinks = [m["id"] for m in workers if m["id"] not in depended]
    final = reviewer or lead
    if lead:
        by_id[lead]["depends_on"] = list(dict.fromkeys([*by_id[lead]["depends_on"], *sinks]))
    if reviewer:
        node = by_id[reviewer]
        node["depends_on"] = [lead] if lead else []
        fix = ("Periksa jawaban dari langkah sebelumnya terhadap tugas. Perbaiki sendiri setiap masalah, lalu kembalikan "
               "jawaban final yang lengkap, bukan daftar masalah.")
        node["instructions"] = f"{node.get('instructions', '').strip()}\n\n{fix}".strip()
        for m in members:
            if m["id"] not in (reviewer, lead) and lead in m["depends_on"]:
                m["depends_on"] = [reviewer if d == lead else d for d in m["depends_on"]]
    ends = [final] if final else sinks
    for m in members:
        if m.get("kind", "agent") in outputs and not m["depends_on"] and m["id"] in after:
            m["depends_on"] = [e for e in ends if e != m["id"]]
    starts = [m["id"] for m in members if not m["depends_on"]]
    return members, starts


def check_condition(condition: dict[str, Any], text: str, now: datetime) -> bool | None:
    """Whether a condition holds. None means only a model can say (mode "ai")."""
    mode = condition["mode"]
    if mode == "contains":
        return text_matches(condition["value"], text)
    if mode == "not_contains":
        return not text_matches(condition["value"], text)
    if mode == "equals":
        return text.strip().lower() == str(condition["value"]).strip().lower()
    if mode == "regex":
        # Capped, because a bad pattern on a long text can run for a very long time.
        return re.search(condition["value"], text[:20_000], re.IGNORECASE) is not None
    if mode == "empty":
        return not text.strip()
    if mode == "time":
        return in_time_window(condition, now)
    return None


def render_template(template: str, values: dict[str, str]) -> str:
    """Fill {{name}} placeholders. Unknown names stay as written, so typos show."""
    return _PLACEHOLDER_RE.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def unfilled(text: str) -> list[str]:
    """The {{placeholders}} a template left as written because nothing had that name."""
    return list(dict.fromkeys(match.group(0) for match in _PLACEHOLDER_RE.finditer(text)))


def clock_values(now: datetime) -> dict[str, str]:
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "day": _DAY_NAMES[now.weekday()],
    }


def telegram_message(schedule: dict[str, Any], text: str) -> str | None:
    """The message a flow works on (the trigger word taken off), or None if it is not for this flow."""
    keyword = str(schedule.get("keyword") or "")
    message = text.strip()
    if keyword:
        first, _, rest = message.partition(" ")
        # Commands in groups arrive as /jadwal@nama_bot.
        if first.split("@")[0].lower() != keyword.lower():
            return None
        message = rest.strip() or first
    return message or None


def telegram_task(schedule: dict[str, Any], text: str, history: str = "", sender: str = "") -> str | None:
    """The task a Telegram message starts, or None if it is not for this team.

    ``history`` is what was said before in this chat, so the answer follows the conversation. ``sender`` is set for
    someone who is not the owner: who writes, so the agent knows it is a stranger.
    """
    message = telegram_message(schedule, text)
    if message is None:
        return None
    context = str(schedule.get("task") or "")
    if sender or history:
        parts = [context] if context else []
        if sender:
            parts.append(f"[PENGIRIM]\n{sender}")
        if history:
            parts.append(f"[PERCAKAPAN SEBELUMNYA]\n{history}")
        parts.append(f"[PESAN BARU]\n{message}")
        return "\n\n".join(parts)
    return f"{context}\n\nPesan: {message}" if context else message


def is_valid_table(name: str) -> bool:
    return bool(_TABLE_RE.fullmatch(name))


def in_time_window(config: dict[str, Any], now: datetime) -> bool:
    """Whether a moment falls in a gate's window. A window may cross midnight."""
    start, end = str(config["start"]), str(config["end"])
    clock = now.strftime("%H:%M")
    if start <= end:
        return now.weekday() in config["days"] and start <= clock <= end
    # 22:00-06:00: the small hours belong to the day the window opened.
    if clock >= start:
        return now.weekday() in config["days"]
    return clock <= end and (now.weekday() - 1) % 7 in config["days"]


def text_matches(keywords: str, text: str) -> bool:
    """Whether a text has any of the comma-separated keywords."""
    lowered = text.lower()
    return any(word.strip().lower() in lowered for word in keywords.split(",") if word.strip())


def parse_decision(reply: str) -> bool | None:
    """Read a model's yes or no. None means it answered neither."""
    if _YES_RE.match(reply):
        return True
    if _NO_RE.match(reply):
        return False
    return None
