"""Remote access needs the owner's access PIN; this machine never does.

The web app runs an agent that can run commands, so anyone who reaches it through a tunnel or the network could
drive the laptop. Requests from this machine pass as before. Everything else (the chat socket included) needs a
signed cookie that ``/access`` hands out after the right PIN:

* The PIN is stored as a PBKDF2 hash with its own salt, never as text.
* The cookie is ``<expiry>.<HMAC(secret, expiry)>``; the secret changes with the PIN, so a new PIN logs every
  browser out.
* Wrong PINs are rate limited per client (5 a minute), and every check is constant time.
* With no PIN set, remote access is refused outright; ``ensure_pin()`` makes one when a tunnel opens and the
  server prints it once.

State lives in ``data/web_access.json`` (mode 600).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from brain.config import paths

COOKIE = "squidwings_access"
MAX_AGE_S = 30 * 24 * 3600
MIN_PIN = 6
OPEN_PATHS = frozenset({"/access", "/api/access/login", "/favicon.ico"})
_ITERATIONS = 200_000
_lock = threading.Lock()
_cached: tuple[float, dict[str, str]] = (-1.0, {})


def _file() -> Path:
    return paths.data_dir() / "web_access.json"


def _load() -> dict[str, str]:
    global _cached
    path = _file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if mtime != _cached[0]:
        data: object = {}
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(path.read_text(encoding="utf-8"))
        _cached = (mtime, {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {})
    return _cached[1]


def _save(data: dict[str, str]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _digest(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITERATIONS).hex()


def has_pin() -> bool:
    return bool(_load().get("pin_hash"))


def set_pin(pin: str) -> None:
    """Store a new PIN (at least six characters) and log every browser out."""
    pin = (pin or "").strip()
    if len(pin) < MIN_PIN:
        raise ValueError(f"PIN akses minimal {MIN_PIN} karakter.")
    salt = secrets.token_hex(16)
    with _lock:
        _save({"pin_hash": _digest(pin, salt), "pin_salt": salt, "secret": secrets.token_hex(32),
               "set_at": str(int(time.time()))})


def ensure_pin() -> str:
    """A fresh eight-digit PIN when none is set (returned once, to print), else ""."""
    if has_pin():
        return ""
    pin = f"{secrets.randbelow(10**8):08d}"
    set_pin(pin)
    return pin


def check_pin(pin: str | None) -> bool:
    data = _load()
    stored, salt = data.get("pin_hash", ""), data.get("pin_salt", "")
    if not stored or not salt or not pin:
        return False
    return hmac.compare_digest(_digest(pin.strip(), salt), stored)


def _sign(expiry: int, secret: str) -> str:
    return hmac.new(bytes.fromhex(secret), str(expiry).encode(), hashlib.sha256).hexdigest()


def issue() -> str:
    """A cookie value valid for 30 days."""
    secret = _load().get("secret", "")
    if not secret:
        raise RuntimeError("no access PIN is set")
    expiry = int(time.time()) + MAX_AGE_S
    return f"{expiry}.{_sign(expiry, secret)}"


def valid(value: str | None) -> bool:
    secret = _load().get("secret", "")
    if not value or not secret or "." not in value:
        return False
    raw_expiry, signature = value.split(".", 1)
    if not raw_expiry.isdigit() or int(raw_expiry) < time.time():
        return False
    return hmac.compare_digest(_sign(int(raw_expiry), secret), signature)


LOGIN_PAGE = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SquidWings · masuk</title>
<style>
:root{--bg:#F4F1EA;--ink:#1F2421;--muted:#5C625D;--accent:#2F6F5E;--line:#D6D1C4}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px}
form{width:100%;max-width:360px}h1{font-size:28px;line-height:1.15;margin:0 0 8px}
p{color:var(--muted);margin:0 0 24px}label{display:block;font-weight:600;margin-bottom:6px}
input{width:100%;font:inherit;font-size:22px;letter-spacing:.2em;padding:12px 14px;border:2px solid var(--line);
border-radius:10px;background:#fff;color:var(--ink)}input:focus{outline:3px solid var(--accent);outline-offset:2px}
button{margin-top:16px;width:100%;min-height:48px;font:inherit;font-weight:700;border:0;border-radius:10px;
background:var(--accent);color:#fff;cursor:pointer}button:focus-visible{outline:3px solid var(--ink);outline-offset:2px}
#err{color:#9B2C2C;min-height:24px;margin-top:12px}
</style></head><body><form id="f"><h1>SquidWings</h1>
<p>Akses dari luar komputer ini butuh PIN akses. PIN-nya tampil di konsol server saat tunnel dibuka.</p>
<label for="pin">PIN akses</label><input id="pin" name="pin" type="password" inputmode="numeric"
autocomplete="current-password" required minlength="6" autofocus>
<button type="submit">Masuk</button><div id="err" role="alert"></div></form>
<script>
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("err"); err.textContent = "";
  const r = await fetch("/api/access/login", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pin: document.getElementById("pin").value})});
  if (r.ok) { const next = new URLSearchParams(location.search).get("next") || "/";
    location.href = next.startsWith("/") && !next.startsWith("//") ? next : "/"; return; }
  const body = await r.json().catch(() => ({}));
  err.textContent = body.detail || "PIN salah.";
});
</script></body></html>"""
