"""Providers and accounts added from the Router page. Saved on this machine only.

A provider is one endpoint: OpenAI-compatible (``POST /chat/completions``) or Claude-compatible
(``POST /v1/messages``). It owns the model ids that start with its prefix, such as ``9r/``. Each
provider has one or more accounts, and every account is one API key. The rotation picks which
account serves each request.

One provider may be the **default**: it has no prefix and serves every model id no other
provider claims, which is what ``API_BASE_URL`` in ``.env`` used to do. ``import_env`` copies
that endpoint (and pairs such as ``BANSOS_API_BASE_URL`` + ``BANSOS_API_KEY``) here once, so
URLs and keys are managed on the page from then on.

The file holds API keys, so it is written with owner-only permissions (0600) next to the other
local data in ``web/data/`` (ignored by git). Keys never leave this module unmasked except to
build the request header: ``public()`` is what the web page sees. It also keeps the page's
optional edit PIN (salted PBKDF2 hash, never the PIN) and the default chat model.

ROUTER_STORE_DIR moves the folder.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("openai", "anthropic")
STRATEGIES = ("smart", "round_robin", "fill_first")
AUTH_MODES = ("bearer", "x-api-key", "both")
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,30}/$")
MAX_ACCOUNTS = 200
MAX_PROVIDERS = 50


def store_dir() -> Path:
    custom = os.getenv("ROUTER_STORE_DIR", "").strip()
    return Path(custom).expanduser() if custom else Path(__file__).resolve().parents[2] / "web" / "data" / "router"


class StoreError(ValueError):
    """A provider or account that cannot be saved as given. The message is for the user."""


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "…" + key[-2:]
    return key[:6] + "…" + key[-4:]


@dataclass
class Account:
    id: str
    api_key: str
    label: str = ""
    enabled: bool = True
    weight: float = 1.0          # share of traffic relative to the other accounts
    max_concurrency: int = 0     # requests at once; 0 = no limit
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("api_key")
        data["key"] = mask(self.api_key)
        return data


@dataclass
class ProviderConfig:
    id: str
    name: str
    kind: str                    # "openai" or "anthropic"
    base_url: str
    prefix: str                  # model ids inside the app, e.g. "9r/claude-sonnet-4-5"
    strategy: str = "smart"
    enabled: bool = True
    models: list[str] = field(default_factory=list)   # extra ids to list when /models is missing or partial
    auth: str = ""               # "" = the kind's default (bearer for openai, x-api-key for anthropic)
    stream_usage: bool = True    # openai: ask for usage in streams (stream_options.include_usage)
    auto_cache: bool = True      # anthropic: mark the system prompt and the latest turn for prompt caching
    anthropic_version: str = "2023-06-01"
    timeout_s: int = 180
    default: bool = False        # no prefix: serves every model id no other provider claims
    source: str = ""             # "env" when imported from .env
    accounts: list[Account] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def auth_mode(self) -> str:
        return self.auth or ("x-api-key" if self.kind == "anthropic" else "bearer")

    def owns(self, model_id: str) -> bool:
        return bool(self.prefix) and model_id.startswith(self.prefix)

    @property
    def usable(self) -> bool:
        return self.enabled and any(a.enabled for a in self.accounts)

    def api_model(self, model_id: str) -> str:
        return model_id[len(self.prefix):] if model_id.startswith(self.prefix) else model_id

    def account(self, account_id: str) -> Account | None:
        return next((a for a in self.accounts if a.id == account_id), None)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["accounts"] = [a.public() for a in self.accounts]
        data["auth_mode"] = self.auth_mode
        return data


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return slug or "provider"


def _clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", url):
        raise StoreError("Base URL harus diawali http:// atau https://, misalnya https://api.openai.com/v1")
    for suffix in ("/chat/completions", "/messages"):
        if url.endswith(suffix):    # a pasted full endpoint still works
            url = url[: -len(suffix)]
    return url


def _clean_prefix(prefix: str, name: str, default: bool = False) -> str:
    prefix = (prefix or "").strip()
    if default and not prefix:
        return ""      # the default provider keeps model ids as the endpoint lists them
    prefix = prefix or _slug(name) + "/"
    if not prefix.endswith("/"):
        prefix += "/"
    if not _PREFIX.match(prefix):
        raise StoreError("Prefix hanya huruf, angka, titik, minus atau garis bawah, diakhiri '/', misalnya 9r/")
    return prefix


def _number(value: Any, low: float, high: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StoreError(f"{name} harus angka") from exc
    if not low <= number <= high:
        raise StoreError(f"{name} harus antara {low:g} dan {high:g}")
    return number


class Store:
    """Thread-safe, file-backed list of providers. Every change is written at once."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or store_dir()
        self.path = self.folder / "providers.json"
        self._lock = threading.RLock()
        self._providers: list[ProviderConfig] = []
        self._meta: dict[str, Any] = {}
        self._mtime = -1.0
        self.version = 0     # bumped on every change, so callers can notice

    # ------------------------------------------------------------ persistence

    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            if self._mtime != 0.0:
                self._providers, self._meta, self._mtime = [], {}, 0.0
            return
        if mtime == self._mtime:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return     # a half-written or hand-broken file keeps the last good copy
        providers = []
        for item in raw.get("providers", []) if isinstance(raw, dict) else []:
            try:
                accounts = [Account(**a) for a in item.pop("accounts", [])]
                providers.append(ProviderConfig(**item, accounts=accounts))
            except TypeError:
                continue
        self._providers, self._mtime = providers, mtime
        self._meta = raw.get("meta", {}) if isinstance(raw.get("meta"), dict) else {}
        self.version += 1

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.folder, 0o700)
        temporary = self.path.with_suffix(".tmp")
        body = json.dumps({"providers": [asdict(p) for p in self._providers], "meta": self._meta}, indent=1)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        temporary.replace(self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        self._mtime = self.path.stat().st_mtime
        self.version += 1

    # ------------------------------------------------------------ reading

    def providers(self) -> list[ProviderConfig]:
        with self._lock:
            self._load()
            return list(self._providers)

    def get(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self.providers() if p.id == provider_id), None)

    def route(self, model_id: str) -> ProviderConfig | None:
        """The enabled provider whose prefix starts this model id (longest wins), else the default one."""
        providers = self.providers()
        found = [p for p in providers if p.enabled and p.owns(model_id)]
        if found:
            return max(found, key=lambda p: len(p.prefix))
        return next((p for p in providers if p.default and p.usable), None)

    def default_provider(self) -> ProviderConfig | None:
        return next((p for p in self.providers() if p.default), None)

    def public(self) -> list[dict[str, Any]]:
        return [p.public() for p in self.providers()]

    # ------------------------------------------------------------ changing

    def add_provider(self, data: dict[str, Any]) -> ProviderConfig:
        with self._lock:
            self._load()
            if len(self._providers) >= MAX_PROVIDERS:
                raise StoreError(f"Paling banyak {MAX_PROVIDERS} provider")
            name = str(data.get("name", "")).strip()[:60]
            if not name:
                raise StoreError("Nama provider wajib diisi")
            kind = str(data.get("kind", "openai")).strip().lower()
            if kind not in KINDS:
                raise StoreError("Jenis harus openai atau anthropic")
            default = bool(data.get("default"))
            prefix = _clean_prefix(str(data.get("prefix", "")), name, default)
            # An empty prefix belongs to the default; _make_default gives the old default a prefix.
            if prefix and any(p.prefix == prefix for p in self._providers):
                raise StoreError(f"Prefix {prefix or '(kosong)'} sudah dipakai provider lain")
            base = _slug(name)
            pid, n = base, 2
            while any(p.id == pid for p in self._providers):
                pid, n = f"{base}-{n}", n + 1
            provider = ProviderConfig(id=pid, name=name, kind=kind, base_url=_clean_url(str(data.get("base_url", ""))),
                                      prefix=prefix, source=str(data.get("source", ""))[:10])
            if default:
                self._make_default(provider)
            self._apply(provider, data, creating=True)
            for key in data.get("api_keys") or []:
                self._new_account(provider, {"api_key": key})
            self._providers.append(provider)
            self._save()
            return provider

    def update_provider(self, provider_id: str, data: dict[str, Any]) -> ProviderConfig:
        with self._lock:
            provider = self._require(provider_id)
            if "name" in data:
                name = str(data["name"]).strip()[:60]
                if not name:
                    raise StoreError("Nama provider wajib diisi")
                provider.name = name
            if "base_url" in data:
                provider.base_url = _clean_url(str(data["base_url"]))
            if "default" in data:
                if data["default"]:
                    self._make_default(provider)
                elif provider.default:
                    provider.default = False
                    if not provider.prefix:
                        provider.prefix = _clean_prefix("", provider.name)
            if "prefix" in data:
                prefix = _clean_prefix(str(data["prefix"]), provider.name, provider.default)
                if any(p.prefix == prefix and p.id != provider.id for p in self._providers):
                    raise StoreError(f"Prefix {prefix or '(kosong)'} sudah dipakai provider lain")
                provider.prefix = prefix
            if "kind" in data:
                kind = str(data["kind"]).strip().lower()
                if kind not in KINDS:
                    raise StoreError("Jenis harus openai atau anthropic")
                provider.kind = kind
            self._apply(provider, data, creating=False)
            self._save()
            return provider

    def _apply(self, provider: ProviderConfig, data: dict[str, Any], *, creating: bool) -> None:
        if "strategy" in data:
            strategy = str(data["strategy"]).strip()
            if strategy not in STRATEGIES:
                raise StoreError("Strategi harus smart, round_robin atau fill_first")
            provider.strategy = strategy
        if "enabled" in data:
            provider.enabled = bool(data["enabled"])
        if "models" in data:
            raw = data["models"]
            items = raw.split(",") if isinstance(raw, str) else list(raw or [])
            provider.models = sorted({str(m).strip() for m in items if str(m).strip()})[:500]
        if "auth" in data:
            auth = str(data["auth"] or "").strip()
            if auth and auth not in AUTH_MODES:
                raise StoreError("Auth harus bearer, x-api-key atau both")
            provider.auth = auth
        for flag in ("stream_usage", "auto_cache"):
            if flag in data:
                setattr(provider, flag, bool(data[flag]))
        if "anthropic_version" in data and str(data["anthropic_version"]).strip():
            provider.anthropic_version = str(data["anthropic_version"]).strip()[:20]
        if "timeout_s" in data:
            provider.timeout_s = int(_number(data["timeout_s"], 10, 1800, "Timeout"))

    def _make_default(self, provider: ProviderConfig) -> None:
        """Only one default. The old one gets a prefix of its own, so its models stay reachable."""
        for other in self._providers:
            if other is not provider and other.default:
                other.default = False
                if not other.prefix:
                    prefix, n = _clean_prefix("", other.name), 2
                    while any(p.prefix == prefix for p in self._providers):
                        prefix, n = f"{_slug(other.name)}{n}/", n + 1
                    other.prefix = prefix
        provider.default = True

    def delete_provider(self, provider_id: str) -> None:
        with self._lock:
            provider = self._require(provider_id)
            self._providers.remove(provider)
            self._save()

    def add_account(self, provider_id: str, data: dict[str, Any]) -> list[Account]:
        """One account, or several when ``api_key`` holds one key per line."""
        with self._lock:
            provider = self._require(provider_id)
            keys = [k.strip() for k in str(data.get("api_key", "")).splitlines() if k.strip()]
            if not keys:
                raise StoreError("API key wajib diisi")
            added = []
            for n, key in enumerate(keys):
                label = str(data.get("label", "")).strip()
                if label and len(keys) > 1:
                    label = f"{label} {n + 1}"
                added.append(self._new_account(provider, {**data, "api_key": key, "label": label}))
            self._save()
            return added

    def _new_account(self, provider: ProviderConfig, data: dict[str, Any]) -> Account:
        key = str(data.get("api_key", "")).strip()
        if not key or len(key) > 4096 or any(ch.isspace() for ch in key):
            raise StoreError("API key kosong atau berisi spasi")
        if any(a.api_key == key for a in provider.accounts):
            raise StoreError(f"Key {mask(key)} sudah ada di provider ini")
        if len(provider.accounts) >= MAX_ACCOUNTS:
            raise StoreError(f"Paling banyak {MAX_ACCOUNTS} akun per provider")
        account = Account(id=secrets.token_hex(4), api_key=key,
                          label=str(data.get("label", "")).strip()[:60] or f"akun {len(provider.accounts) + 1}")
        self._apply_account(account, data)
        provider.accounts.append(account)
        return account

    @staticmethod
    def _apply_account(account: Account, data: dict[str, Any]) -> None:
        if "weight" in data and data["weight"] not in (None, ""):
            account.weight = _number(data["weight"], 0.1, 100, "Bobot")
        if "max_concurrency" in data and data["max_concurrency"] not in (None, ""):
            account.max_concurrency = int(_number(data["max_concurrency"], 0, 1000, "Batas paralel"))
        if "enabled" in data:
            account.enabled = bool(data["enabled"])

    def update_account(self, provider_id: str, account_id: str, data: dict[str, Any]) -> Account:
        with self._lock:
            provider = self._require(provider_id)
            account = provider.account(account_id)
            if account is None:
                raise StoreError("Akun tidak ditemukan")
            if "label" in data:
                account.label = str(data["label"]).strip()[:60] or account.label
            self._apply_account(account, data)
            if "position" in data:    # fill_first order
                position = int(_number(data["position"], 0, len(provider.accounts) - 1, "Urutan"))
                provider.accounts.remove(account)
                provider.accounts.insert(position, account)
            self._save()
            return account

    def delete_account(self, provider_id: str, account_id: str) -> None:
        with self._lock:
            provider = self._require(provider_id)
            account = provider.account(account_id)
            if account is None:
                raise StoreError("Akun tidak ditemukan")
            provider.accounts.remove(account)
            self._save()

    # ------------------------------------------------------------ page settings

    def meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._load()
            return self._meta.get(key, default)

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self._load()
            self._meta[key] = value
            self._save()

    def has_pin(self) -> bool:
        return bool(self.meta("pin_hash"))

    def check_pin(self, pin: str | None) -> bool:
        stored, salt = self.meta("pin_hash", ""), self.meta("pin_salt", "")
        if not stored or not pin:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()
        return hmac.compare_digest(digest, stored)

    def set_pin(self, pin: str) -> None:
        """An empty PIN removes it. Four to sixty-four characters otherwise."""
        pin = (pin or "").strip()
        with self._lock:
            self._load()
            if not pin:
                self._meta.pop("pin_hash", None)
                self._meta.pop("pin_salt", None)
            else:
                if not 4 <= len(pin) <= 64:
                    raise StoreError("PIN 4 sampai 64 karakter")
                salt = secrets.token_bytes(16)
                self._meta["pin_salt"] = salt.hex()
                self._meta["pin_hash"] = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000).hex()
            self._save()

    # ------------------------------------------------------------ .env import

    def import_env(self, env: dict[str, str] | None = None, *, force: bool = False) -> list[ProviderConfig]:
        """Copy the endpoints in .env here, once. Afterwards the page is where they are managed.

        ``API_BASE_URL`` + ``API_KEY`` become the default provider (no prefix, so model ids and
        DEFAULT_MODEL keep working). Every other ``<NAME>_API_BASE_URL`` + ``<NAME>_API_KEY`` pair,
        such as BANSOS, becomes a provider with the prefix ``<name>/``; ``<NAME>_MODEL`` is added
        to its model list. Endpoints already here (same URL and key) are skipped.
        """
        env = dict(os.environ if env is None else env)
        with self._lock:
            self._load()
            if self._meta.get("env_imported") and not force:
                return []
            found: list[tuple[str, str, str, list[str], bool]] = []
            main_url, main_key = env.get("API_BASE_URL", "").strip(), env.get("API_KEY", "").strip()
            if main_url and main_key and main_key != "your-api-key-here":
                found.append(("Default", main_url, main_key, [], True))
            for name in sorted(env):
                if not name.endswith("_API_BASE_URL") or name == "API_BASE_URL":
                    continue
                stem = name[: -len("_API_BASE_URL")]
                url, key = env.get(name, "").strip(), env.get(f"{stem}_API_KEY", "").strip()
                if url and key:
                    model = env.get(f"{stem}_MODEL", "").strip()
                    found.append((stem.replace("_", " ").title(), url, key, [model] if model else [], False))
            added: list[ProviderConfig] = []
            for name, url, key, models, default in found:
                try:
                    clean = _clean_url(url)
                except StoreError:
                    continue
                if any(p.base_url == clean and any(a.api_key == key for a in p.accounts) for p in self._providers):
                    continue
                has_default = any(p.default for p in self._providers)
                try:
                    provider = self.add_provider({"name": name, "kind": "anthropic" if "anthropic" in clean else "openai",
                                                  "base_url": clean, "default": default and not has_default,
                                                  "api_keys": [key], "models": models, "source": "env"})
                except StoreError:
                    continue
                added.append(provider)
            self._meta["env_imported"] = True
            self._save()
            return added

    def _require(self, provider_id: str) -> ProviderConfig:
        self._load()
        provider = next((p for p in self._providers if p.id == provider_id), None)
        if provider is None:
            raise StoreError("Provider tidak ditemukan")
        return provider


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None or _store.folder != store_dir():
            _store = Store()
        return _store


__all__ = ["Account", "ProviderConfig", "Store", "StoreError", "get_store", "mask", "KINDS", "STRATEGIES", "AUTH_MODES"]
