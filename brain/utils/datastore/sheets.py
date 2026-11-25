"""Google Sheets, the online data source of Google Workspace.

The connection is a *service account*: a robot account with its own email address and a key file. The key
(JSON) is pasted on the web page under Tim agent > Kredensial > Google Workspace; nothing is read from ``.env``.
Each spreadsheet is shared with the robot's email, as Editor, exactly like sharing with a person.

Rows are found by position (first data row = 1), like in a file. The header is row 1. Values are written as
plain values (a product code ``007`` stays ``007``); a value that starts with ``=`` is a formula.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from .base import (
    MAX_PAGE,
    MAX_ROWS,
    MAX_SCAN,
    Backend,
    DataError,
    Page,
    TableInfo,
    clean_columns,
    coerce,
    resolve_column,
)

API = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_ID_IN_URL = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_BARE_ID = re.compile(r"[A-Za-z0-9_-]{25,}")
_GID = re.compile(r"[#&?]gid=(\d+)")


def parse_service_account(text: str) -> dict[str, str]:
    """The key file's content, checked. Raises DataError with a message for the page."""
    try:
        data = json.loads(text)
    except ValueError:
        raise DataError("Isi kunci Google harus JSON. Salin seluruh isi file .json dari Google Cloud.") from None
    if not isinstance(data, dict) or data.get("type") != "service_account":
        raise DataError('Ini bukan kunci akun layanan: harus memuat "type": "service_account". '
                        "Buat di Google Cloud > IAM > Akun layanan > Kunci > Tambah kunci > JSON.")
    email, pem = str(data.get("client_email") or ""), str(data.get("private_key") or "")
    if not re.fullmatch(r"[\w.+-]+@[\w.-]+", email) or "PRIVATE KEY" not in pem:
        raise DataError("Kunci Google tidak lengkap: client_email atau private_key tidak ada.")
    return {"client_email": email, "private_key": pem, "project_id": str(data.get("project_id") or "")}


def parse_spreadsheet(text: str) -> tuple[str, int | None]:
    """The spreadsheet id (and the sheet's gid, if the link had one) from a link or a bare id."""
    raw = str(text or "").strip()
    found = _ID_IN_URL.search(raw)
    if found:
        gid = _GID.search(raw)
        return found.group(1), int(gid.group(1)) if gid else None
    if _BARE_ID.fullmatch(raw):
        return raw, None
    raise DataError("Tempel link spreadsheet-nya (docs.google.com/spreadsheets/d/…) atau ID-nya.")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_jwt(account: dict[str, str], now: float | None = None) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    issued = int(now if now is not None else time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": account["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                              "iat": issued, "exp": issued + 3600}).encode())
    try:
        key: Any = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
        signature = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception:  # noqa: BLE001 - never echo key material
        raise DataError("private_key di kunci Google tidak bisa dibaca. Salin ulang seluruh file JSON-nya.") from None
    return f"{header}.{claims}.{_b64(signature)}"


_tokens: dict[str, tuple[str, float]] = {}
_token_lock = threading.Lock()


def column_letter(number: int) -> str:
    letters = ""
    while number > 0:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _quote_sheet(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


class SheetsBackend(Backend):
    kind = "sheets"
    positional = True

    def __init__(self, spreadsheet_id: str, account: dict[str, str], label: str = "Google Sheets",
                 client_factory: Callable[[], httpx.Client] | None = None) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.account = account
        self.label = label
        self._factory = client_factory or (lambda: httpx.Client(timeout=25, follow_redirects=False))

    # -- HTTP
    def _token(self, client: httpx.Client, fresh: bool = False) -> str:
        email = self.account["client_email"]
        with _token_lock:
            cached = _tokens.get(email)
            if cached and cached[1] > time.time() + 60 and not fresh:
                return cached[0]
        try:
            response = client.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                    "assertion": sign_jwt(self.account)})
        except httpx.HTTPError as exc:
            raise DataError(f"Tidak bisa menghubungi Google ({type(exc).__name__}). Cek koneksi internet.") from None
        if response.status_code != 200:
            reason = ""
            with _suppress():
                reason = str(response.json().get("error_description") or response.json().get("error") or "")
            raise DataError(f"Google menolak kunci akun layanan ({response.status_code}): {reason[:120] or 'cek kuncinya'}")
        body = response.json()
        token = str(body["access_token"])
        with _token_lock:
            _tokens[email] = (token, time.time() + int(body.get("expires_in", 3600)))
        return token

    def _call(self, method: str, path: str, *, params: Any = None, body: Any = None) -> Any:
        url = f"{API}/{self.spreadsheet_id}{path}"
        with self._factory() as client:
            for attempt in range(2):
                token = self._token(client, fresh=attempt == 1)
                try:
                    response = client.request(method, url, params=params, json=body, headers={"Authorization": f"Bearer {token}"})
                except httpx.HTTPError as exc:
                    raise DataError(f"Tidak bisa menghubungi Google Sheets ({type(exc).__name__}).") from None
                if response.status_code == 401 and attempt == 0:
                    continue
                break
        if response.status_code >= 400:
            raise DataError(self._explain(response))
        return response.json() if response.content else {}

    def _explain(self, response: httpx.Response) -> str:
        email = self.account["client_email"]
        text = ""
        with _suppress():
            text = str((response.json().get("error") or {}).get("message") or "")
        if response.status_code == 403 and ("has not been used" in text or "SERVICE_DISABLED" in response.text):
            return "Google Sheets API belum aktif di proyek Google Cloud akun layanan. Aktifkan di APIs & Services > Library > Google Sheets API."
        if response.status_code in (403, 404):
            return (f"Spreadsheet tidak bisa dibuka. Buka spreadsheet-nya > Bagikan > tambahkan {email} sebagai Editor. "
                    "Cek juga linknya benar.")
        if response.status_code == 429:
            return "Kuota Google Sheets habis sebentar (maksimal sekitar 60 permintaan per menit). Coba lagi satu menit lagi."
        return f"Google Sheets menolak permintaan ({response.status_code}): {text[:160]}"

    # -- structure
    def _meta(self) -> dict[str, Any]:
        return dict(self._call("GET", "", params={"fields": "properties.title,sheets.properties(sheetId,title,gridProperties)"}))

    def _sheet(self, table: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        sheets = [s["properties"] for s in (meta or self._meta()).get("sheets", [])]
        for properties in sheets:
            if properties["title"] == table or properties["title"].lower() == str(table).strip().lower():
                return dict(properties)
        if not table and len(sheets) == 1:
            return dict(sheets[0])
        raise DataError(f"Sheet '{table}' tidak ada. Sheet yang ada: {', '.join(s['title'] for s in sheets) or '(kosong)'}")

    def _get_values(self, ranges: list[str], render: str = "UNFORMATTED_VALUE") -> list[list[list[Any]]]:
        params = [("ranges", r) for r in ranges] + [("valueRenderOption", render), ("dateTimeRenderOption", "FORMATTED_STRING")]
        data = self._call("GET", "/values:batchGet", params=params)
        return [list(item.get("values") or []) for item in data.get("valueRanges", [])]

    def _header(self, title: str) -> list[str]:
        (rows,) = self._get_values([f"{_quote_sheet(title)}!1:1"])
        header = list(rows[0]) if rows else []
        while header and header[-1] in ("", None):
            header.pop()
        return clean_columns([str(h) for h in header]) if header else []

    def tables(self) -> list[TableInfo]:
        meta = self._meta()
        titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if not titles:
            return []
        headers = self._get_values([f"{_quote_sheet(t)}!1:1" for t in titles])
        out = []
        for title, rows in zip(titles, headers, strict=False):
            header = list(rows[0]) if rows else []
            while header and header[-1] in ("", None):
                header.pop()
            out.append(TableInfo(title, clean_columns([str(h) for h in header]) if header else [], None))
        return out

    def verify_account(self) -> str:
        """Ask Google for a token with the saved key, without touching any spreadsheet. Returns the account's email."""
        with self._factory() as client:
            self._token(client, fresh=True)
        return self.account["client_email"]

    def check(self) -> str:
        meta = self._meta()
        return f"Tersambung ke \"{meta.get('properties', {}).get('title', 'spreadsheet')}\", {len(meta.get('sheets', []))} sheet."

    def _scan(self, table: str) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
        properties = self._sheet(table)
        title = properties["title"]
        last = min(MAX_ROWS, int(properties.get("gridProperties", {}).get("rowCount", MAX_ROWS)))
        (values,) = self._get_values([f"{_quote_sheet(title)}!A1:ZZ{last}"])
        header = list(values[0]) if values else []
        while header and header[-1] in ("", None):
            header.pop()
        columns = clean_columns([str(h) for h in header]) if header else []
        body = values[1:] if values else []
        while body and not any(c not in ("", None) for c in body[-1]):
            body.pop()
        keyed = [(i, {c: (r[j] if j < len(r) and r[j] != "" else None) for j, c in enumerate(columns)}) for i, r in enumerate(body, start=1)]
        return columns, keyed

    def all_rows(self, table: str, where: Any = None, search: str = "", cap: int = MAX_SCAN) -> Page:
        columns, keyed = self._scan(table)
        chosen = self.pick(keyed, columns, search, where)
        return Page(columns, self.rows_out(chosen[:cap], columns), len(chosen), 0)

    def read(self, table: str, offset: int = 0, limit: int = 50, sort: str = "", desc: bool = False,
             search: str = "", where: Any = None) -> Page:
        columns, keyed = self._scan(table)
        picked, total = self.window(keyed, columns, offset, min(int(limit), MAX_PAGE), sort, desc, search, where)
        return Page(columns, self.rows_out(picked, columns), total, max(0, int(offset)))

    # -- writing
    def _convert(self, title: str, columns: list[str], values: dict[str, Any]) -> dict[int, Any]:
        (sample,) = self._get_values([f"{_quote_sheet(title)}!2:31"])
        out = {}
        for column, value in values.items():
            real = resolve_column(columns, column)
            index = columns.index(real)
            samples = [row[index] if index < len(row) else None for row in sample]
            out[index + 1] = coerce(value, samples)
        return out

    def _write_cells(self, title: str, cells: dict[tuple[int, int], Any]) -> None:
        """(row, column) -> value. Plain values are stored as given; a text starting with = becomes a formula."""
        plain: list[dict[str, Any]] = []
        formulas: list[dict[str, Any]] = []
        for (row, column), value in cells.items():
            entry = {"range": f"{_quote_sheet(title)}!{column_letter(column)}{row}", "values": [["" if value is None else value]]}
            (formulas if isinstance(value, str) and value.startswith("=") else plain).append(entry)
        for mode, data in (("RAW", plain), ("USER_ENTERED", formulas)):
            if data:
                self._call("POST", "/values:batchUpdate", body={"valueInputOption": mode, "data": data})

    def _count(self, title: str) -> int:
        (values,) = self._get_values([f"{_quote_sheet(title)}!A1:ZZ{MAX_ROWS}"])
        body = values[1:] if values else []
        while body and not any(c not in ("", None) for c in body[-1]):
            body.pop()
        return len(body)

    def insert(self, table: str, values: dict[str, Any], at: int | None = None) -> int:
        properties = self._sheet(table)
        title, sheet_id = properties["title"], properties["sheetId"]
        columns = self._header(title)
        if not columns:
            raise DataError("Sheet ini belum punya baris judul kolom di baris 1")
        count = self._count(title)
        if count >= MAX_ROWS:
            raise DataError(f"Maksimal {MAX_ROWS} baris per tabel")
        position = count + 1 if at is None else max(1, min(int(at), count + 1))
        converted = self._convert(title, columns, values)
        sheet_rows = int(properties.get("gridProperties", {}).get("rowCount", 0))
        if position <= count:
            self._call("POST", ":batchUpdate", body={"requests": [{"insertDimension": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": position, "endIndex": position + 1}}}]})
        elif position + 1 > sheet_rows:
            self._call("POST", ":batchUpdate", body={"requests": [{"appendDimension": {
                "sheetId": sheet_id, "dimension": "ROWS", "length": position + 1 - sheet_rows}}]})
        self._write_cells(title, {(position + 1, column): value for column, value in converted.items()})
        return position

    def update(self, table: str, key: Any, values: dict[str, Any]) -> None:
        title = self._sheet(table)["title"]
        columns = self._header(title)
        try:
            row = int(key) + 1
        except (TypeError, ValueError):
            raise DataError(f"Nomor baris tidak valid: {key!r}") from None
        if row < 2:
            raise DataError("Baris 1 adalah judul kolom; ubah lewat Ganti nama kolom")
        self._write_cells(title, {(row, column): value for column, value in self._convert(title, columns, values).items()})

    def delete(self, table: str, keys: list[Any]) -> int:
        properties = self._sheet(table)
        numbers = sorted({int(k) for k in keys}, reverse=True)
        if not numbers:
            return 0
        requests = [{"deleteDimension": {"range": {"sheetId": properties["sheetId"], "dimension": "ROWS",
                                                  "startIndex": n, "endIndex": n + 1}}} for n in numbers if n >= 1]
        self._call("POST", ":batchUpdate", body={"requests": requests})
        return len(requests)

    def add_column(self, table: str, name: str) -> None:
        properties = self._sheet(table)
        title = properties["title"]
        columns = self._header(title)
        label = str(name).strip()
        if not label:
            raise DataError("Nama kolom kosong")
        if label.lower() in (c.lower() for c in columns):
            raise DataError(f"Kolom '{label}' sudah ada")
        if len(columns) + 1 > int(properties.get("gridProperties", {}).get("columnCount", 26)):
            self._call("POST", ":batchUpdate", body={"requests": [{"appendDimension": {
                "sheetId": properties["sheetId"], "dimension": "COLUMNS", "length": 1}}]})
        self._write_cells(title, {(1, len(columns) + 1): label})

    def rename_column(self, table: str, old: str, new: str) -> None:
        title = self._sheet(table)["title"]
        columns = self._header(title)
        real = resolve_column(columns, old)
        label = str(new).strip()
        if not label:
            raise DataError("Nama kolom kosong")
        if label.lower() != real.lower() and label.lower() in (c.lower() for c in columns):
            raise DataError(f"Kolom '{label}' sudah ada")
        self._write_cells(title, {(1, columns.index(real) + 1): label})

    def drop_column(self, table: str, name: str) -> None:
        properties = self._sheet(table)
        columns = self._header(properties["title"])
        real = resolve_column(columns, name)
        if len(columns) == 1:
            raise DataError("Sheet harus punya minimal satu kolom")
        index = columns.index(real)
        self._call("POST", ":batchUpdate", body={"requests": [{"deleteDimension": {"range": {
            "sheetId": properties["sheetId"], "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1}}}]})

    def create_table(self, table: str, columns: list[str]) -> None:
        title = str(table).strip()[:100]
        if not title:
            raise DataError("Nama sheet kosong")
        self._call("POST", ":batchUpdate", body={"requests": [{"addSheet": {"properties": {"title": title}}}]})
        names = clean_columns(columns or ["kolom_1"])
        self._write_cells(title, {(1, i): n for i, n in enumerate(names, start=1)})

    def drop_table(self, table: str) -> None:
        meta = self._meta()
        properties = self._sheet(table, meta)
        if len(meta.get("sheets", [])) == 1:
            raise DataError("Sheet terakhir tidak bisa dihapus")
        self._call("POST", ":batchUpdate", body={"requests": [{"deleteSheet": {"sheetId": properties["sheetId"]}}]})


class _suppress:  # noqa: N801 - tiny local helper, a context manager that ignores parsing errors
    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: Any, value: Any, trace: Any) -> bool:
        return kind is not None and issubclass(kind, (ValueError, KeyError, AttributeError, TypeError))


__all__ = ["SheetsBackend", "parse_service_account", "parse_spreadsheet", "sign_jwt", "column_letter", "quote"]
