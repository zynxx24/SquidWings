"""Relevance ranking that stays sharp as skills and tools pile up.

Two tools, both cached and rebuilt only when the documents change:

* ``Index``: BM25 over weighted fields (name, keywords, description). Used where a free-text
  need is matched against everything (``load_skill(search=...)``, ``find_tools``).
* ``TriggerWeights``: inverse document frequency for exact trigger phrases. With three skills,
  "desain" and "gsap" weigh the same; with three hundred, "desain" is in forty of them and says
  little, while "gsap" is in four and says a lot. A matched trigger counts ``1 + idf``, and a
  phrase of several words counts more than one word, so the specific guide wins over the
  general one without hand-tuned bonuses.

Words are lowercase letters and digits. A query word also matches a document word that starts
with it when both are at least 5 letters ("animasi" ~ "animasikan"), a cheap stand-in for
stemming in Indonesian and English.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

_WORD_RE = re.compile(r"[a-z0-9]+")
_K1 = 1.2
_B = 0.75
_PREFIX_MIN = 5


def words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 1]


@lru_cache(maxsize=4096)
def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """A whole-word pattern for a trigger phrase, compiled once."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])")


def has_phrase(phrase: str, text: str) -> bool:
    return phrase_pattern(phrase).search(text) is not None


@dataclass
class Index:
    """BM25 over documents made of weighted fields.

    ``docs``: id -> [(text, weight), ...]. A word in a field of weight 3 counts as three.
    """

    docs: Mapping[str, Sequence[tuple[str, float]]]
    _tf: dict[str, dict[str, float]] = field(default_factory=dict, init=False)
    _len: dict[str, float] = field(default_factory=dict, init=False)
    _df: Counter[str] = field(default_factory=Counter, init=False)
    _avg: float = field(default=1.0, init=False)
    _vocab_by_prefix: dict[str, set[str]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for doc_id, fields in self.docs.items():
            tf: dict[str, float] = {}
            for text, weight in fields:
                for w in words(text):
                    tf[w] = tf.get(w, 0.0) + weight
            self._tf[doc_id] = tf
            self._len[doc_id] = sum(tf.values()) or 1.0
            self._df.update(tf.keys())
        self._avg = (sum(self._len.values()) / len(self._len)) if self._len else 1.0
        for term in self._df:
            if len(term) >= _PREFIX_MIN:
                self._vocab_by_prefix.setdefault(term[:_PREFIX_MIN], set()).add(term)

    def __len__(self) -> int:
        return len(self._tf)

    def idf(self, term: str) -> float:
        n, df = len(self._tf), self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _expand(self, term: str) -> list[str]:
        """The term itself, plus longer vocabulary words that start with it."""
        if len(term) < _PREFIX_MIN:
            return [term]
        family = self._vocab_by_prefix.get(term[:_PREFIX_MIN], set())
        return [term, *(w for w in family if w != term and w.startswith(term))]

    def score(self, doc_id: str, query_terms: Iterable[str]) -> float:
        tf, length = self._tf.get(doc_id), self._len.get(doc_id, 1.0)
        if not tf:
            return 0.0
        total = 0.0
        for term in query_terms:
            best = 0.0
            for candidate in self._expand(term):
                freq = tf.get(candidate, 0.0)
                if not freq:
                    continue
                part = self.idf(candidate) * freq * (_K1 + 1) / (freq + _K1 * (1 - _B + _B * length / self._avg))
                best = max(best, part * (1.0 if candidate == term else 0.7))
            total += best
        return total

    def rank(self, query: str, limit: int = 10, min_score: float = 0.0,
             only: Iterable[str] | None = None) -> list[tuple[str, float]]:
        terms = list(dict.fromkeys(words(query)))
        if not terms:
            return []
        pool = list(only) if only is not None else list(self._tf)
        scored = [(doc_id, self.score(doc_id, terms)) for doc_id in pool]
        scored = [(d, s) for d, s in scored if s > min_score]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]


@dataclass
class TriggerWeights:
    """How much an exact trigger phrase says, from how many documents share it."""

    triggers: Mapping[str, Sequence[str]]
    _df: Counter[str] = field(default_factory=Counter, init=False)

    def __post_init__(self) -> None:
        for phrases in self.triggers.values():
            self._df.update({p.lower() for p in phrases if p})

    def weight(self, phrase: str) -> float:
        n = max(1, len(self.triggers))
        df = max(1, self._df.get(phrase.lower(), 1))
        multiword = 1.0 + 0.5 * (len(words(phrase)) > 1)
        return (1.0 + math.log(1 + n / df)) * multiword

    def score(self, doc_id: str, text: str) -> float:
        """Sum of matched trigger weights. Overlapping matches count once, the longest phrase
        winning: "landing page" does not also score "landing" and "page"."""
        lowered = text.lower()
        spans: list[tuple[int, int, str]] = []
        for phrase in self.triggers.get(doc_id, ()):
            if not phrase:
                continue
            for m in phrase_pattern(phrase).finditer(lowered):
                spans.append((m.start(), m.end(), phrase))
        spans.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
        taken: list[tuple[int, int]] = []
        counted: set[str] = set()
        total = 0.0
        for start, end, phrase in spans:
            if phrase in counted or any(start < e and s < end for s, e in taken):
                continue
            taken.append((start, end))
            counted.add(phrase)
            total += self.weight(phrase)
        return total

    def matched(self, doc_id: str, text: str) -> list[str]:
        lowered = text.lower()
        return [p for p in self.triggers.get(doc_id, ()) if p and has_phrase(p, lowered)]


__all__ = ["Index", "TriggerWeights", "has_phrase", "phrase_pattern", "words"]
