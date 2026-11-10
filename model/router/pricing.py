"""What the tokens on the Router page probably cost, in US dollars.

Prices are per million tokens. For each model the first source that knows it wins:

1. ``custom``  a price typed on the Router page (always wins),
2. ``listed``  a price the provider gave in its ``/models`` list (OpenRouter and some resellers do),
3. ``bawaan``  the built-in table below: Anthropic's first-party prices for Claude models,
4. ``gratis``  an id ending in ``:free``.

Anything else has no price, and the page says so instead of counting it as free. A reseller's
price is often lower than the first-party one, so its own listed or typed price beats the table.

Cost of one request = fresh input x input price + cached x cache-read price + cache write x
cache-write price + output x output price. When a price leaves the cache rates out, a cache read
is taken as 10% of the input price and a cache write as 125%, Anthropic's multipliers. It is an
estimate: the provider's invoice is the real number.

Saved in ``prices.json`` next to the usage database, with the USD to IDR rate for the page.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from model.router.store import store_dir

CACHE_READ_SHARE = 0.10
CACHE_WRITE_SHARE = 1.25
MAX_PRICE = 10_000.0          # dollars per million tokens; anything above is a typo
SOURCES = ("custom", "listed", "bawaan", "gratis")

# Anthropic first-party API, USD per 1M tokens: (input, output, cache read or None for 10% of input).
CLAUDE: dict[str, tuple[float, float, float | None]] = {
    "claude-fable-5-2": (8.0, 40.0, 0.20),
    "claude-mythos-5-2": (8.0, 40.0, 0.20),
    "claude-fable-5-1": (10.0, 50.0, None),
    "claude-mythos-5-1": (10.0, 50.0, None),
    "claude-fable-5": (10.0, 50.0, None),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, None),
    "claude-opus-4-8": (5.0, 25.0, None),
    "claude-opus-4-7": (5.0, 25.0, None),
    "claude-opus-4-6": (5.0, 25.0, None),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, None),
    "claude-sonnet-4-6": (3.0, 15.0, None),
    "claude-haiku-5-5": (0.10, 0.50, None),
    "claude-haiku-4-5": (1.0, 5.0, None),
}
# Longest first, and a version may not run on: "claude-opus-5" must not price "claude-opus-5-5".
_CLAUDE_KEYS = sorted(CLAUDE, key=len, reverse=True)
_DATE = re.compile(r"-20\d{6}$")


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cached: float | None = None
    cache_write: float | None = None

    @property
    def cached_rate(self) -> float:
        return self.input * CACHE_READ_SHARE if self.cached is None else self.cached

    @property
    def write_rate(self) -> float:
        return self.input * CACHE_WRITE_SHARE if self.cache_write is None else self.cache_write

    def cost(self, *, prompt: int = 0, output: int = 0, cached: int = 0, cache_write: int = 0) -> float:
        """Dollars for these tokens. ``prompt`` counts every input token, cached ones included."""
        fresh = max(0, prompt - cached - cache_write)
        return (fresh * self.input + cached * self.cached_rate + cache_write * self.write_rate
                + output * self.output) / 1_000_000

    def public(self) -> dict[str, float]:
        return {"input": self.input, "output": self.output, "cached": round(self.cached_rate, 6),
                "cache_write": round(self.write_rate, 6)}


def canonical(model: str) -> str:
    """``vk/anthropic/claude-sonnet-4.6`` -> ``claude-sonnet-4-6``: the last part, dots as dashes, no date."""
    name = model.strip().lower().rsplit("/", 1)[-1].split(":", 1)[0].replace(".", "-").replace("_", "-")
    return _DATE.sub("", name)


def builtin(model: str) -> Price | None:
    name = canonical(model)
    for key in _CLAUDE_KEYS:
        if re.search(rf"(?:^|[^a-z0-9]){re.escape(key)}(?![-]?\d)", name):
            inp, out, cached = CLAUDE[key]
            return Price(inp, out, cached)
    return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= MAX_PRICE else None


def _rate(value: Any) -> float | None:
    """A USD to IDR rate: above 0 and at most a million, else None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= 1_000_000 else None


def _per_million(value: Any) -> float | None:
    """A provider's price: per token (OpenRouter's "0.000003") or already per million tokens."""
    number = _number(value)
    if number is None:
        return None
    return number * 1_000_000 if 0 < number < 0.01 else number


def from_listing(item: dict[str, Any]) -> Price | None:
    """The price inside one ``/models`` entry, if the provider gave one."""
    raw = item.get("pricing") or item.get("price")
    raw = raw if isinstance(raw, dict) else {}
    inp = _per_million(raw.get("prompt", raw.get("input", item.get("input_price"))))
    out = _per_million(raw.get("completion", raw.get("output", item.get("output_price"))))
    if inp is None or out is None:
        return None
    return Price(inp, out, _per_million(raw.get("input_cache_read", raw.get("cached"))),
                 _per_million(raw.get("input_cache_write", raw.get("cache_write"))))


def _check(body: dict[str, Any]) -> Price:
    values = {}
    for key in ("input", "output", "cached", "cache_write"):
        if body.get(key) in (None, ""):
            if key in ("input", "output"):
                raise ValueError(f"Harga {key} wajib diisi (USD per 1 juta token).")
            continue
        number = _number(body[key])
        if number is None:
            raise ValueError(f"Harga {key} harus angka 0 sampai {MAX_PRICE:g}.")
        values[key] = number
    return Price(**values)


class PriceBook:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or store_dir() / "prices.json"
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data = data if isinstance(data, dict) else {}
            self._data = {"custom": dict(data.get("custom") or {}), "listed": dict(data.get("listed") or {}),
                          "usd_idr": data.get("usd_idr")}
        return self._data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._load(), indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    def lookup(self, model: str) -> tuple[Price | None, str]:
        with self._lock:
            data = self._load()
            for source in ("custom", "listed"):
                found = data[source].get(model)
                if isinstance(found, dict):
                    with contextlib.suppress(TypeError, ValueError):
                        return _check(found), source
        if model.endswith(":free"):
            return Price(0.0, 0.0, 0.0, 0.0), "gratis"
        price = builtin(model)
        return (price, "bawaan") if price else (None, "")

    def price(self, model: str) -> Price | None:
        return self.lookup(model)[0]

    def set_custom(self, model: str, body: dict[str, Any]) -> Price:
        model = model.strip()[:200]
        if not model:
            raise ValueError("Nama model kosong.")
        price = _check(body)
        with self._lock:
            self._load()["custom"][model] = {k: v for k, v in asdict(price).items() if v is not None}
            self._save()
        return price

    def clear_custom(self, model: str) -> None:
        with self._lock:
            if self._load()["custom"].pop(model, None) is not None:
                self._save()

    def remember_listed(self, models: list[dict[str, Any]]) -> int:
        """Keep the prices a provider lists with its models. Returns how many it gave."""
        found = {}
        for item in models:
            if isinstance(item, dict) and item.get("id"):
                price = from_listing(item)
                if price is not None:
                    found[str(item["id"])] = {k: v for k, v in asdict(price).items() if v is not None}
        if found:
            with self._lock:
                self._load()["listed"].update(found)
                self._save()
        return len(found)

    def usd_idr(self) -> float | None:
        with self._lock:
            return _rate(self._load().get("usd_idr"))

    def set_usd_idr(self, rate: Any) -> float | None:
        number = _rate(rate)
        if rate not in (None, "", 0, "0") and number is None:
            raise ValueError("Kurs harus angka, misalnya 16500.")
        with self._lock:
            self._load()["usd_idr"] = number
            self._save()
        return number

    def custom_models(self) -> list[str]:
        with self._lock:
            return sorted(self._load()["custom"])


_book: PriceBook | None = None
_book_lock = threading.Lock()


def get_price_book() -> PriceBook:
    global _book
    with _book_lock:
        if _book is None or _book.path != store_dir() / "prices.json":
            _book = PriceBook()
        return _book


def cost_of(model: str, *, prompt: int = 0, output: int = 0, cached: int = 0, cache_write: int = 0) -> float | None:
    """Dollars for one request's tokens (``prompt`` = every input token), or None when the model has no price."""
    price = get_price_book().price(model)
    return price.cost(prompt=prompt, output=output, cached=cached, cache_write=cache_write) if price else None


__all__ = ["Price", "PriceBook", "get_price_book", "cost_of", "builtin", "canonical", "from_listing", "CLAUDE", "SOURCES"]
