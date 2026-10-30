"""Detect bare continuation prompts such as "lanjutkan..." or "continue".

Continuation turns are not new intents: they only make sense against the
previous turn's output. Detecting them reliably matters for two reasons.

1. Classification would otherwise report a meaningless low-confidence intent
   (``activity`` at ~0.30) and route the turn to whatever model that bucket
   happens to point at, discarding the context of the truncated work.
2. Memory injection depends on the answer: a continuation needs the code
   buffer / truncation buffer, not a freshly ranked workspace summary.

The detector is intentionally strict. A message that merely contains the word
"lanjut" ("lanjutkan implementasi modul auth") is a new instruction with real
content, so only messages that *are* the continuation word — optionally
decorated with punctuation and polite fillers — are treated as continuations.
"""

from __future__ import annotations

import re
import unicodedata

# Canonical continuation words, already normalised (lowercase, no punctuation).
_CONTINUATION_ALIASES: frozenset[str] = frozenset(
    {
        "continue",
        "continew",
        "cont",
        "go on",
        "keep going",
        "next",
        "lanjut",
        "lanjutkan",
        "terusin",
        "sambung",
        "terus",
        "teruskan",
        "gas",
        "ok lanjut",
        "oke lanjut",
        "okey lanjut",
        "okay lanjut",
        "silakan lanjut",
        "silakan lanjutkan",
    }
)

# Words that add politeness or urgency but no new instruction. Stripped from
# the tail only, so "lanjut aja dong" collapses to "lanjut".
_TRAILING_FILLERS: frozenset[str] = frozenset(
    {
        "aja",
        "aj",
        "dong",
        "deh",
        "sih",
        "ya",
        "yah",
        "kah",
        "please",
        "pls",
        "now",
        "sekarang",
        "kak",
        "bro",
        "bang",
        "min",
    }
)

_MAX_FILLER_STRIP = 3

# Instruction appended to a continuation turn when no truncation buffer exists,
# so the model resumes the plan instead of asking "lanjut apa?".
_CONTINUATION_HINT = (
    "(Lanjutkan langkah berikutnya sesuai rencana dan konteks sebelumnya. "
    "Jangan mengulang pekerjaan yang sudah selesai.)"
)


def normalize_input(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    Unicode is normalised first so curly quotes and ellipsis characters do not
    survive as word characters and break the alias comparison.
    """
    folded = unicodedata.normalize("NFKC", str(text or "")).lower()
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())


def is_continuation_input(text: str) -> bool:
    """Return True only when the whole message is a continuation request."""
    words = normalize_input(text).split()
    if not words or len(words) > 6:
        # A long message is an instruction; a continuation is a nudge.
        return False

    for _ in range(_MAX_FILLER_STRIP):
        if words and words[-1] in _TRAILING_FILLERS:
            words.pop()
        else:
            break

    return " ".join(words) in _CONTINUATION_ALIASES


def continuation_hint() -> str:
    """Return the resume instruction used when no buffer is available."""
    return _CONTINUATION_HINT
