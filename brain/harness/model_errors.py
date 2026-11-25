"""What went wrong with a model, in words the user can act on.

A model the user picks is never swapped for another one behind their back. When it fails, the
chat says why (key refused, budget spent, rate limited, model unknown, too long a conversation,
provider down, timeout) and what to do, and the request ends there.
"""

from __future__ import annotations

import re
from typing import Any

_HINT = ("Model ini kamu pilih sendiri, jadi tidak diganti otomatis. Coba lagi, pilih model lain, "
         "atau pakai Auto.")


def _status(exc: BaseException) -> int:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    match = re.search(r"\b(40[0-9]|42[0-9]|5[0-9]{2})\b", str(exc))
    return int(match.group(1)) if match else 0


def classify(exc: BaseException) -> str:
    """One word for the kind of failure: auth, billing, rate_limit, not_found, context, timeout,
    connection, server, empty, or other."""
    text = str(exc).lower()
    status = _status(exc)
    name = type(exc).__name__.lower()
    if "empty" in name or "returned empty content" in text:
        return "empty"
    if status in (401, 403) or "invalid api key" in text or "unauthorized" in text or "authentication" in text:
        return "auth"
    if status == 402 or any(t in text for t in ("billing", "budget exhausted", "insufficient", "top up", "quota")):
        return "billing"
    if status == 429 or "ratelimit" in name or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if status == 404 or any(t in text for t in ("model not found", "not allowed to use model", "model is not available",
                                                 "does not exist", "unknown model")):
        return "not_found"
    if any(t in text for t in ("context length", "context_length", "maximum context", "too many tokens",
                                "prompt is too long", "reduce the length")):
        return "context"
    if "timeout" in name or "timed out" in text or "timeout" in text or "idle" in text:
        return "timeout"
    if "connect" in name or "connection" in text or "name or service not known" in text:
        return "connection"
    if 500 <= status < 600 or "server error" in text or "bad gateway" in text or "overloaded" in text:
        return "server"
    return "other"


def explain(exc: BaseException, model_id: str, model: Any = None) -> str:
    """A short Indonesian message: what failed, why, and what to do."""
    kind = classify(exc)
    detail = str(exc).strip().splitlines()[0][:240] if str(exc).strip() else type(exc).__name__
    retry = getattr(exc, "retry_after", None)
    window = int(getattr(model, "context_window", 0) or 0)
    reasons = {
        "auth": "API key-nya ditolak provider (401/403). Periksa key di halaman Router.",
        "billing": "Saldo atau kuota provider habis (402). Isi ulang di halaman billing provider.",
        "rate_limit": ("Kena batas rate provider (429)."
                       + (f" Coba lagi sekitar {int(retry)} detik lagi." if retry else " Tunggu sebentar lalu coba lagi.")),
        "not_found": "Model ini tidak ada di provider atau tidak diizinkan untuk API key ini.",
        "context": ("Percakapan ini terlalu panjang untuk konteks model ini"
                    + (f" ({window:,} token)." if window else ".")
                    + " Mulai chat baru, atau pilih model dengan konteks lebih besar."),
        "timeout": "Model tidak menjawab dalam batas waktu. Provider mungkin sedang lambat.",
        "connection": "Tidak bisa terhubung ke provider. Periksa URL di halaman Router dan koneksi internet.",
        "server": "Server provider sedang error (5xx). Biasanya sementara.",
        "empty": "Model mengembalikan jawaban kosong.",
        "other": "Model gagal menjawab.",
    }
    return f"⚠️ **{model_id}** gagal: {reasons[kind]}\n\nDetail: `{detail}`\n\n{_HINT}"


def unknown_model(model_id: str) -> str:
    return (f"⚠️ Model **{model_id}** tidak ada di daftar model provider saat ini. Muat ulang daftar model di "
            f"halaman Router, atau pilih model lain. {_HINT}")


__all__ = ["classify", "explain", "unknown_model"]
