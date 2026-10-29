"""
security.path_jail — Canonical, symlink-safe filesystem containment.

Every filesystem operation performed on behalf of a model or user must pass
through this jail. It resolves symlinks, normalises `..` segments and rejects
paths that escape the configured root — including paths that only appear to
be inside because of a shared string prefix (e.g. `/proj` vs `/proj-evil`).
"""

from __future__ import annotations

import os
from pathlib import Path


class PathJailError(ValueError):
    """Raised when a path escapes the jail or hits a denied location."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Path rejected ({reason}): {path}")


# Absolute locations that are never readable or writable by the agent, even
# when the jail root is `/`. Matched by exact path or ancestor relationship.
DENIED_PREFIXES: tuple[str, ...] = (
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/ssh",
    "/root/.ssh",
    "/proc/sysrq-trigger",
    "/sys/kernel",
    "/dev/mem",
    "/dev/kmem",
)

DENIED_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".pgpass",
)

# Filenames that are denied only when the jail root is the filesystem root.
DENIED_SECRET_NAMES: tuple[str, ...] = (
    ".env",
    ".env.local",
    "credentials",
    "credentials.json",
)


class PathJail:
    """Confine filesystem access to a single root directory.

    Args:
        root: Directory that bounds all access. Created if missing.
        allow_hidden: Permit dot-files/dot-directories inside the root.
        extra_denied: Additional absolute prefixes to deny.
        max_path_length: Reject absurdly long paths before touching the FS.
    """

    __slots__ = ("_root", "_allow_hidden", "_denied", "_max_len")

    def __init__(
        self,
        root: str | Path,
        *,
        allow_hidden: bool = False,
        extra_denied: tuple[str, ...] = (),
        max_path_length: int = 4096,
    ) -> None:
        resolved = Path(root).expanduser()
        resolved.mkdir(parents=True, exist_ok=True)
        self._root = resolved.resolve(strict=True)
        self._allow_hidden = allow_hidden
        self._denied = DENIED_PREFIXES + tuple(extra_denied)
        self._max_len = max_path_length

    @property
    def root(self) -> Path:
        return self._root

    # ── resolution ────────────────────────────────────────────────────
    def resolve(self, candidate: str | Path, *, must_exist: bool = False) -> Path:
        """Resolve `candidate` inside the jail or raise PathJailError.

        Relative paths are interpreted relative to the jail root, never the
        process working directory — this removes an entire class of
        confusion attacks.
        """
        raw = str(candidate)
        if not raw.strip():
            raise PathJailError(raw, "empty path")
        if "\x00" in raw:
            raise PathJailError(raw, "NUL byte in path")
        if len(raw) > self._max_len:
            raise PathJailError(raw, "path too long")

        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self._root / path

        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:  # symlink loop, ELOOP, etc.
            raise PathJailError(raw, f"unresolvable: {exc}") from exc

        if resolved != self._root and self._root not in resolved.parents:
            raise PathJailError(raw, "escapes jail root")

        if not self._allow_hidden:
            rel_parts = resolved.relative_to(self._root).parts
            if any(part.startswith(".") and part not in (".", "..") for part in rel_parts):
                raise PathJailError(raw, "hidden path not permitted")

        for denied in self._denied:
            denied_path = Path(denied)
            if resolved == denied_path or denied_path in resolved.parents:
                raise PathJailError(raw, f"denied location: {denied}")

        if self._root == Path("/") and resolved.name in DENIED_SECRET_NAMES:
            raise PathJailError(raw, "denied secret file")

        if resolved.suffix.lower() in DENIED_SUFFIXES:
            raise PathJailError(raw, f"denied extension: {resolved.suffix}")

        if must_exist and not resolved.exists():
            raise PathJailError(raw, "does not exist")

        return resolved

    def contains(self, candidate: str | Path) -> bool:
        """Non-raising containment check."""
        try:
            self.resolve(candidate)
        except PathJailError:
            return False
        return True

    def relative(self, candidate: str | Path) -> str:
        """Return the jail-relative POSIX path (safe for display/logging)."""
        return self.resolve(candidate).relative_to(self._root).as_posix()

    def safe_join(self, *parts: str) -> Path:
        """Join untrusted path fragments without allowing traversal."""
        for part in parts:
            if part in ("..", ".") or part.startswith("/") or "\\" in part:
                raise PathJailError(part, "unsafe path fragment")
        return self.resolve(self._root.joinpath(*parts))

    def is_writable(self, candidate: str | Path) -> bool:
        """Check write permission on an already-resolved jail path."""
        try:
            target = self.resolve(candidate)
        except PathJailError:
            return False
        probe = target if target.exists() else target.parent
        return os.access(probe, os.W_OK)

    def __repr__(self) -> str:
        return f"PathJail(root={self._root}, allow_hidden={self._allow_hidden})"
