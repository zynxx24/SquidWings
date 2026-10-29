"""
security.prompt_shield - Heuristic prompt-injection / jailbreak detection.

Untrusted text (tool output, retrieved documents, user-supplied snippets) is
scanned for patterns that commonly indicate an attempt to override the system
prompt or exfiltrate instructions. This is a *cheap first line of defence*: it
scores evidence and blocks only above a threshold, so ordinary technical prose
mentioning "instructions" or "system prompt" is not rejected.

Design notes:
    - Patterns carry weights; the verdict score is the sum of matched weights,
      capped at 1.0.
    - ``should_block`` is True when the score reaches ``block_threshold``.
    - ``Severity`` maps the score onto a coarse band for logging / UI.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "PromptShield",
    "Severity",
    "ShieldVerdict",
    "PATTERNS",
    "normalise",
]


class Severity(StrEnum):
    """Coarse severity band derived from a verdict score."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: float) -> Severity:
        if score <= 0.0:
            return cls.NONE
        if score < 0.35:
            return cls.LOW
        if score < 0.6:
            return cls.MEDIUM
        if score < 0.85:
            return cls.HIGH
        return cls.CRITICAL


@dataclass(frozen=True, slots=True)
class ShieldVerdict:
    """Result of inspecting one piece of untrusted text."""

    score: float = 0.0
    matches: tuple[str, ...] = ()
    severity: Severity = Severity.NONE
    threshold: float = 0.6

    @property
    def is_clean(self) -> bool:
        """True when nothing suspicious was found."""
        return not self.matches

    @property
    def should_block(self) -> bool:
        """True when the evidence is strong enough to reject the input."""
        return self.score >= self.threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "matches": list(self.matches),
            "severity": self.severity.value,
            "blocked": self.should_block,
        }


# (name, weight, compiled regex)
PATTERNS: tuple[tuple[str, float, re.Pattern[str]], ...] = (
    (
        "ignore_previous",
        0.55,
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget)\b[^.\n]{0,40}"
            r"\b(?:all\s+)?(?:previous|prior|above|earlier)\b[^.\n]{0,20}"
            r"\b(?:instructions?|prompts?|rules?|directions?)\b"
        ),
    ),
    (
        "reveal_system_prompt",
        0.55,
        re.compile(
            r"(?i)\b(?:reveal|show|print|output|repeat|dump|leak)\b[^.\n]{0,40}"
            r"\b(?:system\s+prompt|initial\s+prompt|hidden\s+instructions?|"
            r"your\s+instructions?)\b"
        ),
    ),
    (
        "role_override",
        0.45,
        re.compile(
            r"(?i)\b(?:you\s+are\s+now|from\s+now\s+on\s+you|act\s+as|"
            r"pretend\s+to\s+be)\b[^.\n]{0,30}"
            r"\b(?:dan|jailbreak|unrestricted|no\s+restrictions?|"
            r"developer\s+mode|admin)\b"
        ),
    ),
    (
        "jailbreak_keyword",
        0.5,
        re.compile(
            r"(?i)\b(?:jailbreak|do\s+anything\s+now|developer\s+mode|"
            r"god\s+mode|bypass\s+(?:your\s+)?(?:safety|guardrails?|filters?))\b"
        ),
    ),
    (
        "fake_system_tag",
        0.4,
        re.compile(
            r"(?i)(?:<\s*/?\s*system\s*>|\[\s*system\s*\]|"
            r"#{2,}\s*system\s*#{2,}|^\s*system\s*:)"
        ),
    ),
    (
        "exfiltrate_secrets",
        0.6,
        re.compile(
            r"(?i)\b(?:send|post|upload|exfiltrate|email)\b[^.\n]{0,40}"
            r"\b(?:api\s*keys?|credentials?|passwords?|secrets?|tokens?|"
            r"\.env|id_rsa)\b"
        ),
    ),
    (
        "instruction_override_verb",
        0.3,
        re.compile(
            r"(?i)\b(?:new\s+instructions?|override\s+(?:the\s+)?"
            r"(?:system|rules?)|ignore\s+(?:the\s+)?(?:rules?|policy))\b"
        ),
    ),
    (
        "encoded_payload",
        0.35,
        re.compile(r"(?i)\b(?:base64|rot13|hex)\s*(?:decode|encoded?)\b"),
    ),
    (
        "tool_call_spoof",
        0.4,
        re.compile(
            r"(?i)(?:\{\s*\"tool\"\s*:|\bexecute_command\s*\(|"
            r"<\s*function_calls?\s*>)"
        ),
    ),
)


def normalise(text: str) -> str:
    """Fold unicode lookalikes and collapse whitespace before matching."""
    folded = unicodedata.normalize("NFKC", text)
    # Strip zero-width and bidi control characters used to hide payloads.
    cleaned = "".join(
        ch
        for ch in folded
        if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in "\n\t"
    )
    return re.sub(r"[ \t]+", " ", cleaned)


class PromptShield:
    """Scores untrusted text for prompt-injection evidence."""

    __slots__ = ("block_threshold", "patterns")

    def __init__(
        self,
        *,
        block_threshold: float = 0.6,
        patterns: Sequence[tuple[str, float, re.Pattern[str]]] | None = None,
    ) -> None:
        """Create a shield.

        Args:
            block_threshold: Score at which ``should_block`` becomes True.
            patterns: Override the default pattern table (mainly for tests).
        """
        if not 0.0 < block_threshold <= 1.0:
            raise ValueError("block_threshold must be in (0.0, 1.0]")
        self.block_threshold = block_threshold
        self.patterns = tuple(patterns if patterns is not None else PATTERNS)

    def inspect(self, text: str) -> ShieldVerdict:
        """Inspect ``text`` and return a verdict."""
        if not text or not text.strip():
            return ShieldVerdict(threshold=self.block_threshold)

        haystack = normalise(text)
        hits: list[str] = []
        score = 0.0
        for name, weight, pattern in self.patterns:
            if pattern.search(haystack):
                hits.append(name)
                score += weight

        score = min(score, 1.0)
        return ShieldVerdict(
            score=score,
            matches=tuple(hits),
            severity=Severity.from_score(score),
            threshold=self.block_threshold,
        )

    def is_clean(self, text: str) -> bool:
        """Convenience predicate: True when no pattern matched."""
        return self.inspect(text).is_clean

    def should_block(self, text: str) -> bool:
        """Convenience predicate: True when the input should be rejected."""
        return self.inspect(text).should_block

    def __repr__(self) -> str:
        return (
            f"PromptShield(patterns={len(self.patterns)}, "
            f"block_threshold={self.block_threshold})"
        )
