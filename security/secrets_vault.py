"""
security.secrets_vault - Encrypted-at-rest credential storage.

API keys must not live in plaintext .env files on a shared machine, and they
must not be readable by a prompt-injected agent. This vault stores secrets in
an authenticated-encryption envelope:

    key     = scrypt(passphrase, salt)              -> 32 bytes
    nonce   = os.urandom(12)
    cipher  = AES-256-GCM(key, nonce, plaintext, aad=name)

When `cryptography` is installed we use AES-256-GCM (preferred). Otherwise we
fall back to an HMAC-SHA256 keystream with encrypt-then-MAC, which is sound
for small secrets and needs zero dependencies.

Vault file format (JSON):
    {
      "version": 1,
      "kdf": {"name": "scrypt", "salt": "<b64>", "n": 16384, "r": 8, "p": 1},
      "cipher": "aes-256-gcm" | "hmac-stream",
      "entries": {
         "OPENAI_KEY": {"nonce": "<b64>", "ct": "<b64>", "tag": "<b64>",
                        "updated": "1234.5"}
      }
    }

The passphrase never touches disk. Rotation re-encrypts every entry under a
new key in a single atomic write. File mode is forced to 0600.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets as _secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    _HAVE_AESGCM = True
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore[assignment,misc]
    _HAVE_AESGCM = False


class VaultError(RuntimeError):
    """Base error for vault operations."""


class VaultLockedError(VaultError):
    """Raised when a secret is requested before unlock()."""


# N818 wants an ``Error`` suffix; the short public name is kept as an alias so
# existing imports (tests, security/__init__.py) keep working unchanged.
VaultLocked = VaultLockedError


class VaultAuthError(VaultError):
    """Raised when authentication fails (wrong passphrase or tampering)."""


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """scrypt KDF - memory-hard and in the standard library."""
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=False))


class _HmacStreamCipher:
    """Encrypt-then-MAC fallback: HMAC-SHA256 CTR keystream + HMAC tag.

    Not a general-purpose cipher, but safe for small secrets when
    `cryptography` is unavailable.
    """

    @staticmethod
    def _keystream(enc_key: bytes, length: int) -> bytes:
        blocks: list[bytes] = []
        counter = 0
        while sum(len(b) for b in blocks) < length:
            blocks.append(
                hmac.new(enc_key, counter.to_bytes(8, "big"), hashlib.sha256).digest()
            )
            counter += 1
        return b"".join(blocks)[:length]

    @classmethod
    def encrypt(
        cls, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes
    ) -> tuple[bytes, bytes]:
        enc_key = hmac.new(key, b"enc" + nonce, hashlib.sha256).digest()
        mac_key = hmac.new(key, b"mac" + nonce, hashlib.sha256).digest()
        ct = _xor(plaintext, cls._keystream(enc_key, len(plaintext)))
        tag = hmac.new(mac_key, aad + nonce + ct, hashlib.sha256).digest()
        return ct, tag

    @classmethod
    def decrypt(
        cls, key: bytes, nonce: bytes, ct: bytes, tag: bytes, aad: bytes
    ) -> bytes:
        mac_key = hmac.new(key, b"mac" + nonce, hashlib.sha256).digest()
        expected = hmac.new(mac_key, aad + nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, tag):
            raise VaultAuthError(
                "authentication failed (wrong passphrase or tampered vault)"
            )
        enc_key = hmac.new(key, b"enc" + nonce, hashlib.sha256).digest()
        return _xor(ct, cls._keystream(enc_key, len(ct)))


@dataclass(frozen=True, slots=True)
class KdfParams:
    """scrypt parameters persisted alongside the ciphertext."""

    salt: bytes
    n: int = 16384
    r: int = 8
    p: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": "scrypt",
            "salt": _b64e(self.salt),
            "n": self.n,
            "r": self.r,
            "p": self.p,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> KdfParams:
        return cls(
            salt=_b64d(str(data["salt"])),
            n=int(data.get("n", 16384)),
            r=int(data.get("r", 8)),
            p=int(data.get("p", 1)),
        )


class SecretsVault:
    """Encrypted key/value store for credentials.

    Usage:
        vault = SecretsVault(".vault.json")
        vault.create("correct horse battery staple")
        vault.set("OPENAI_KEY", "sk-...")
        vault.save()
        # ... later, new process ...
        vault.unlock("correct horse battery staple")
        key = vault.get("OPENAI_KEY")
    """

    VERSION = 1
    MIN_PASSPHRASE = 12

    def __init__(self, path: str | Path = ".vault.json") -> None:
        self.path = Path(path)
        self._kdf: KdfParams | None = None
        self._key: bytes | None = None
        self._entries: dict[str, dict[str, str]] = {}
        self._cipher = "aes-256-gcm" if _HAVE_AESGCM else "hmac-stream"

    # -- lifecycle ------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def create(self, passphrase: str, force: bool = False) -> None:
        """Initialise a new vault with a fresh random salt."""
        if self.exists and not force:
            raise VaultError(f"vault already exists: {self.path}")
        if len(passphrase) < self.MIN_PASSPHRASE:
            raise ValueError(
                f"passphrase must be at least {self.MIN_PASSPHRASE} characters"
            )
        self._kdf = KdfParams(salt=os.urandom(16))
        self._key = _derive_key(
            passphrase, self._kdf.salt, self._kdf.n, self._kdf.r, self._kdf.p
        )
        self._entries = {}
        self._cipher = "aes-256-gcm" if _HAVE_AESGCM else "hmac-stream"
        self.save()

    def unlock(self, passphrase: str) -> None:
        """Load the vault and derive the key, verifying with a probe decrypt."""
        if not self.exists:
            raise VaultError(f"vault not found: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if int(data.get("version", 0)) != self.VERSION:
            raise VaultError(f"unsupported vault version: {data.get('version')!r}")
        self._kdf = KdfParams.from_dict(data["kdf"])
        self._cipher = str(data.get("cipher", "hmac-stream"))
        self._key = _derive_key(
            passphrase, self._kdf.salt, self._kdf.n, self._kdf.r, self._kdf.p
        )
        self._entries = {
            str(k): {kk: str(vv) for kk, vv in v.items()}
            for k, v in (data.get("entries") or {}).items()
        }
        # Probe: decrypting any entry must succeed, else the passphrase is wrong.
        for name, entry in self._entries.items():
            self._decrypt_entry(name, entry)  # raises VaultAuthError
            break

    def lock(self) -> None:
        """Wipe the derived key from memory."""
        self._key = None

    def __enter__(self) -> SecretsVault:
        return self

    def __exit__(self, *exc: object) -> None:
        self.lock()

    # -- crypto ---------------------------------------------------------
    def _require_key(self) -> bytes:
        if self._key is None:
            raise VaultLockedError("vault is locked; call unlock() first")
        return self._key

    def _encrypt_entry(self, name: str, value: str) -> dict[str, str]:
        key = self._require_key()
        nonce = os.urandom(12)
        aad = name.encode("utf-8")
        plaintext = value.encode("utf-8")
        if self._cipher == "aes-256-gcm" and _HAVE_AESGCM:
            blob = AESGCM(key).encrypt(nonce, plaintext, aad)
            return {
                "nonce": _b64e(nonce),
                "ct": _b64e(blob[:-16]),
                "tag": _b64e(blob[-16:]),
            }
        ct, tag = _HmacStreamCipher.encrypt(key, nonce, plaintext, aad)
        return {"nonce": _b64e(nonce), "ct": _b64e(ct), "tag": _b64e(tag)}

    def _decrypt_entry(self, name: str, entry: Mapping[str, str]) -> str:
        key = self._require_key()
        nonce = _b64d(entry["nonce"])
        ct = _b64d(entry["ct"])
        tag = _b64d(entry["tag"])
        aad = name.encode("utf-8")
        if self._cipher == "aes-256-gcm" and _HAVE_AESGCM:
            try:
                plaintext = AESGCM(key).decrypt(nonce, ct + tag, aad)
                return plaintext.decode("utf-8")
            except Exception as exc:
                raise VaultAuthError(
                    "authentication failed (wrong passphrase or tampered vault)"
                ) from exc
        return _HmacStreamCipher.decrypt(key, nonce, ct, tag, aad).decode("utf-8")

    # -- CRUD -----------------------------------------------------------
    def set(self, name: str, value: str) -> None:
        """Encrypt and store a secret."""
        if not name or not name.replace("_", "").isalnum():
            raise VaultError(f"invalid secret name: {name!r}")
        entry = self._encrypt_entry(name, value)
        entry["updated"] = str(time.time())
        self._entries[name] = entry

    def get(self, name: str, default: str | None = None) -> str | None:
        """Decrypt and return a secret."""
        entry = self._entries.get(name)
        if entry is None:
            return default
        return self._decrypt_entry(name, entry)

    def require(self, name: str) -> str:
        """Return a secret or raise if absent."""
        value = self.get(name)
        if value is None:
            raise VaultError(f"secret not found: {name}")
        return value

    def delete(self, name: str) -> bool:
        return self._entries.pop(name, None) is not None

    def names(self) -> list[str]:
        return sorted(self._entries)

    def __contains__(self, name: object) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    # -- persistence ----------------------------------------------------
    def save(self) -> Path:
        """Atomically persist the vault with mode 0600."""
        if self._kdf is None:
            raise VaultError("vault has no KDF params; call create() or unlock() first")
        payload = {
            "version": self.VERSION,
            "kdf": self._kdf.to_dict(),
            "cipher": self._cipher,
            "entries": self._entries,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        with contextlib.suppress(OSError):  # pragma: no cover - non-POSIX
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        with contextlib.suppress(OSError):  # pragma: no cover
            os.chmod(self.path, 0o600)
        return self.path

    def rotate(self, old_passphrase: str, new_passphrase: str) -> None:
        """Re-encrypt every entry under a new passphrase."""
        if len(new_passphrase) < self.MIN_PASSPHRASE:
            raise ValueError(
                f"passphrase must be at least {self.MIN_PASSPHRASE} characters"
            )
        self.unlock(old_passphrase)
        plaintext = {
            name: self._decrypt_entry(name, entry)
            for name, entry in self._entries.items()
        }
        self._kdf = KdfParams(salt=os.urandom(16))
        self._key = _derive_key(
            new_passphrase, self._kdf.salt, self._kdf.n, self._kdf.r, self._kdf.p
        )
        self._entries = {}
        for name, value in plaintext.items():
            self.set(name, value)
        self.save()

    # -- convenience ----------------------------------------------------
    def export_to_env(self, path: str | Path = ".env") -> Path:
        """Write decrypted secrets as KEY=VALUE lines (explicit opt-in only)."""
        out = Path(path)
        lines = [
            f"{name}={value}"
            for name, value in sorted(
                (n, self._decrypt_entry(n, e)) for n, e in self._entries.items()
            )
        ]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):  # pragma: no cover
            os.chmod(out, 0o600)
        return out

    def __repr__(self) -> str:
        state = "unlocked" if self.unlocked else "locked"
        return f"SecretsVault(path={self.path}, entries={len(self._entries)}, {state})"


# -- environment bridge -------------------------------------------------
def resolve_secret(name: str, vault_path: str | Path | None = None) -> str | None:
    """Look up a secret: process env first, then an unlocked vault file.

    Keeps existing `.env` deployments working while a vault takes over
    incrementally.
    """
    value = os.getenv(name)
    if value:
        return value
    if vault_path is None:
        vault_path = os.getenv("SQUIDWINGS_VAULT", ".vault.json")
    path = Path(vault_path)
    if not path.exists():
        return None

    passphrase = os.getenv("SQUIDWINGS_VAULT_PASSPHRASE")
    if not passphrase:
        return None

    vault = SecretsVault(path)
    try:
        vault.unlock(passphrase)
        return vault.get(name)
    except VaultError:
        return None
    finally:
        vault.lock()


def generate_passphrase(words: int = 6) -> str:
    """Generate a high-entropy passphrase from urandom (hex chunks, zero deps)."""
    return "-".join(_secrets.token_hex(4) for _ in range(words))
