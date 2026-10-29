"""
security.redactor — Outbound secret scrubbing.

Every string that leaves the process (logs, audit records, HTTP payloads,
error messages, web responses) should pass through a Redactor. Detection is
pattern-based and intentionally conservative: false positives only cost
readability, false negatives leak credentials.

Design notes:
    * Patterns are compiled once at import time.
    * Replacement is single-pass per pattern over the *original* text so a
      redacted token can never be re-matched by a later pattern.
    * `redact()` is the module-level convenience for hot paths (cached
      default instance, zero allocation when nothing matches).
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

MASK = "***REDACTED***"


@dataclass(frozen=True, slots=True)
class SecretPattern:
    """A named regex plus the group index holding the secret value."""

    name: str
    regex: re.Pattern[str]
    group: int = 0

    def mask(self, text: str, replacement: str = MASK) -> str:
        if self.group == 0:
            return self.regex.sub(replacement, text)

        def _sub(match: re.Match[str]) -> str:
            start, end = match.span(self.group)
            if start < 0:
                return match.group(0)
            whole_start = match.start()
            return (
                match.group(0)[: start - whole_start]
                + replacement
                + match.group(0)[end - whole_start :]
            )

        return self.regex.sub(_sub, text)


# Ordered roughly by specificity. `group` keeps the surrounding context
# ("Authorization: Bearer ...") readable while hiding the credential.
DEFAULT_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    SecretPattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    ),
    SecretPattern(
        "openai_key",
        re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    ),
    SecretPattern(
        "anthropic_key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"),
    ),
    SecretPattern(
        "google_key",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    ),
    SecretPattern(
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ),
    SecretPattern(
        "slack_token",
        re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    ),
    SecretPattern(
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    SecretPattern(
        "bearer_header",
        re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-+/=]{12,})"),
        group=2,
    ),
    SecretPattern(
        "basic_header",
        re.compile(r"(?i)\b(basic\s+)([A-Za-z0-9+/=]{12,})"),
        group=2,
    ),
    SecretPattern(
        "url_credentials",
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+:)([^@\s/]+)(@)"),
        group=2,
    ),
    SecretPattern(
        "assignment",
        re.compile(
            r"(?i)\b([a-z0-9_\-]*(?:api[_\-]?key|secret|token|password|passwd|"
            r"credential|auth)[a-z0-9_\-]*\s*[=:]\s*)"
            r"(['\"]?)([^\s'\"&,;]{6,})\2"
        ),
        group=3,
    ),
    SecretPattern(
        "cli_flag",
        re.compile(r"(?i)(\s--?(?:api[_\-]?key|token|password|secret)[= ])(\S{6,})"),
        group=2,
    ),
)

# Keys whose *values* are always masked in structured data, regardless of
# whether the value looks like a secret.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "authorization",
        "auth",
        "cookie",
        "set-cookie",
        "credential",
        "credentials",
        "client_secret",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "session_id",
        "token",
        "access_token",
    }
)

# Substrings that mark a key as sensitive even when not an exact match.
_SENSITIVE_HINTS: tuple[str, ...] = ("secret", "token", "password", "apikey", "credential")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().strip()
    if lowered in SENSITIVE_KEYS:
        return True
    return any(hint in lowered for hint in _SENSITIVE_HINTS)


@dataclass
class Redactor:
    """Scrub secrets from text and structured payloads.

    Args:
        patterns: Override the default pattern set.
        extra_patterns: Appended to the default set.
        mask: Replacement token.
        max_depth: Recursion limit for structured redaction.
        max_items: Cap on list/tuple elements inspected.
        max_string: Long strings are truncated after redaction.
    """

    patterns: tuple[SecretPattern, ...] = DEFAULT_PATTERNS
    mask: str = MASK
    max_depth: int = 6
    max_items: int = 100
    max_string: int = 8000
    _extra: tuple[SecretPattern, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self._extra:
            self.patterns = self.patterns + self._extra

    # ── text ──────────────────────────────────────────────────────────
    def scrub(self, text: str) -> str:
        """Return `text` with every detected secret masked."""
        if not text:
            return text
        out = text
        for pattern in self.patterns:
            if pattern.regex.search(out):
                out = pattern.mask(out, self.mask)
        if len(out) > self.max_string:
            out = out[: self.max_string] + "…<truncated>"
        return out

    def contains_secret(self, text: str) -> bool:
        """True when any pattern matches. Cheaper than scrubbing."""
        return any(p.regex.search(text) for p in self.patterns if text)

    def find(self, text: str) -> list[str]:
        """Return the *names* of patterns that matched (never the values)."""
        return [p.name for p in self.patterns if text and p.regex.search(text)]

    # ── structured ────────────────────────────────────────────────────
    def scrub_obj(self, value: Any, _depth: int = 0) -> Any:
        """Recursively redact a JSON-like structure.

        Dict keys matching SENSITIVE_KEYS have their values replaced wholesale;
        all remaining strings are pattern-scrubbed.
        """
        if _depth >= self.max_depth:
            return "<max-depth>"

        if isinstance(value, Mapping):
            return {
                str(k): (
                    self.mask
                    if _is_sensitive_key(str(k))
                    else self.scrub_obj(v, _depth + 1)
                )
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            items = list(value)[: self.max_items]
            return [self.scrub_obj(v, _depth + 1) for v in items]
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, bytes):
            try:
                return self.scrub(value.decode("utf-8", "replace"))
            except Exception:  # pragma: no cover - defensive
                return "<binary>"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.scrub(str(value))

    def scrub_lines(self, lines: Iterable[str]) -> list[str]:
        return [self.scrub(line) for line in lines]


@functools.lru_cache(maxsize=1)
def _default() -> Redactor:
    return Redactor()


def redact(value: Any) -> Any:
    """Module-level convenience wrapper around the shared Redactor."""
    return _default().scrub_obj(value)


def redact_text(text: str) -> str:
    """Scrub a single string using the shared Redactor."""
    return _default().scrub(text)
