"""Smart memory layer — relevance scoring, decay, dedup, and auto-capture.

Upgrades :class:`WorkspaceMemory` without changing its on-disk format:

* **Relevance scoring** — files ranked by recency, edit frequency, keyword
  overlap, and directory proximity.
* **Decay** — old facts/decisions/errors lose weight over time.
* **Dedup** — near-duplicate entries are merged, not piled up.
* **Auto-capture** — decisions/facts/errors extracted from a turn.
* **Token budget** — summary trimmed highest-signal-first.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from brain.memory.workspace_memory import _TEXT_EXT, WorkspaceMemory
from utils.logger import get_logger

logger = get_logger(__name__)

_HALF_LIFE_SECONDS = 3 * 24 * 3600
_ERROR_HALF_LIFE = 12 * 3600
_RECENCY_BOOST = 2.0
_FREQ_WEIGHT = 1.5
_KEYWORD_MIN_LEN = 3
_MAX_KEYWORDS = 40
_DEDUP_SIMILARITY = 0.82

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will",
    "yang", "dan", "untuk", "dengan", "dari", "ini", "itu", "ada", "bisa",
    "tidak", "juga", "saya", "kamu", "kita", "atau", "pada", "ke", "di",
    "buat", "buatkan", "tolong", "mohon", "please", "make", "build", "add",
    "file", "code", "kode", "function", "class", "def", "return",
}


@dataclass
class ScoredFile:
    rel_path: str
    score: float
    reason: str
    status: str = "unchanged"
    size: int = 0
    ext: str = ""


@dataclass
class MemorySnapshot:
    workspace: str
    summary: str
    top_files: list[ScoredFile] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    turn_count: int = 0
    token_estimate: int = 0


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) >= _KEYWORD_MIN_LEN}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _decay(ts: float, half_life: float, now: float | None = None) -> float:
    now = now if now is not None else time.time()
    age = max(0.0, now - ts)
    return math.pow(0.5, age / half_life)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class SmartMemory:
    """Wrap :class:`WorkspaceMemory` with scoring, decay, and auto-capture."""

    def __init__(
        self,
        workspace_path: str | Path,
        workspace_memory: WorkspaceMemory | None = None,
    ) -> None:
        self._ws = Path(workspace_path)
        # Reuse the caller's WorkspaceMemory when given: two instances on the
        # same state.json each hold their own copy, and whichever saves last
        # silently overwrites the other's decisions/facts.
        self._wm = workspace_memory if workspace_memory is not None else WorkspaceMemory(self._ws)

    @property
    def raw(self) -> WorkspaceMemory:
        return self._wm

    @property
    def workspace(self) -> Path:
        return self._ws

    def score_files(
        self,
        user_input: str = "",
        *,
        limit: int = 20,
        prefer_ext: Iterable[str] | None = None,
    ) -> list[ScoredFile]:
        files = self._wm.scan_workspace_files()
        if not files:
            return []

        keywords = _tokenize(user_input)
        prefer = {e.lower() for e in prefer_ext} if prefer_ext else None

        recent_dirs: set[str] = set()
        for rel, info in files.items():
            if info.get("changed"):
                recent_dirs.add(str(Path(rel).parent))

        scored: list[ScoredFile] = []
        for rel, info in files.items():
            if info.get("status") == "deleted":
                continue
            ext = str(info.get("ext", "")).lower()
            if ext not in _TEXT_EXT:
                continue

            score = 0.0
            reasons: list[str] = []

            if info.get("changed"):
                score += _RECENCY_BOOST
                reasons.append("changed")

            path_tokens = _tokenize(rel.replace("/", " ").replace(".", " "))
            overlap = keywords & path_tokens
            if overlap:
                score += 2.5 * len(overlap)
                reasons.append(f"path:{','.join(sorted(overlap)[:3])}")

            if not overlap and keywords and info.get("size", 0) < 200_000:
                try:
                    content = (self._ws / rel).read_text(
                        encoding="utf-8", errors="replace"
                    )[:20_000]
                    c_overlap = keywords & _tokenize(content)
                    if c_overlap:
                        score += 1.5 * len(c_overlap)
                        reasons.append(f"body:{','.join(sorted(c_overlap)[:3])}")
                except OSError:
                    pass

            if self._wm.state.get("files", {}).get(rel):
                score += _FREQ_WEIGHT
                reasons.append("edited-before")

            if str(Path(rel).parent) in recent_dirs:
                score += 0.8
                reasons.append("near-recent")

            if prefer and ext in prefer:
                score += 1.0
                reasons.append(f"ext:{ext}")

            if score <= 0:
                continue

            scored.append(
                ScoredFile(
                    rel_path=rel,
                    score=round(score, 2),
                    reason="; ".join(reasons) or "baseline",
                    status=str(info.get("status", "unchanged")),
                    size=int(info.get("size", 0)),
                    ext=ext,
                )
            )

        scored.sort(key=lambda f: f.score, reverse=True)
        return scored[:limit]

    def build_summary(self, token_budget: int = 400) -> str:
        state = self._wm.state
        now = time.time()
        lines: list[str] = ["[MEMORY]"]

        progress = state.get("progress", [])
        if progress:
            lines.append("Progress:")
            for p in progress[-8:]:
                icon = {
                    "done": "OK", "in_progress": "..",
                    "pending": "--", "failed": "XX",
                }.get(p.get("status", ""), ".")
                detail = f" - {p['detail'][:80]}" if p.get("detail") else ""
                lines.append(f"  [{icon}] {p.get('phase', '?')}: {p.get('status', '?')}{detail}")

        decisions = state.get("decisions", [])
        if decisions:
            ranked = sorted(
                decisions,
                key=lambda d: _decay(d.get("ts", 0), _HALF_LIFE_SECONDS, now),
                reverse=True,
            )[:5]
            lines.append("Decisions:")
            for d in ranked:
                why = f" ({d['why'][:60]})" if d.get("why") else ""
                lines.append(f"  * {d.get('what', '?')[:100]}{why}")

        facts = state.get("facts", [])
        if facts:
            ranked = sorted(
                facts,
                key=lambda f: _decay(f.get("ts", 0), _HALF_LIFE_SECONDS, now),
                reverse=True,
            )[:6]
            lines.append("Facts:")
            for f in ranked:
                lines.append(f"  * {f.get('fact', '?')[:120]}")

        errors = state.get("errors", [])
        if errors:
            ranked = sorted(
                errors,
                key=lambda e: _decay(e.get("ts", 0), _ERROR_HALF_LIFE, now),
                reverse=True,
            )[:3]
            lines.append("Recent errors:")
            for e in ranked:
                lines.append(f"  ! {e.get('err', '?')[:90]}")

        file_edits = state.get("files", {})
        if file_edits:
            ranked = sorted(
                file_edits.items(),
                key=lambda kv: kv[1].get("ts", 0),
                reverse=True,
            )[:8]
            lines.append("File edits:")
            for rel, info in ranked:
                summary = f" - {info['summary'][:60]}" if info.get("summary") else ""
                lines.append(f"  [{info.get('action', '?')}] {rel}{summary}")

        if state.get("model_history"):
            lines.append(f"Last model: {state['model_history'][-1].get('model', '?')}")
        lines.append(f"Turns: {state.get('turn_count', 0)}")
        lines.append("[/MEMORY]")

        text = "\n".join(lines)
        if _estimate_tokens(text) <= token_budget:
            return text
        while len(lines) > 2 and _estimate_tokens("\n".join(lines)) > token_budget:
            lines.pop(-2)
        return "\n".join(lines)

    def capture_from_turn(
        self,
        user_input: str,
        assistant_output: str,
        *,
        model_id: str = "",
        errors: list[str] | None = None,
    ) -> dict[str, int]:
        captured = {"decisions": 0, "facts": 0, "errors": 0}

        for line in assistant_output.splitlines():
            stripped = line.strip()
            for prefix in ("decision:", "keputusan:", "we will", "kita akan"):
                if stripped.lower().startswith(prefix):
                    what = stripped[len(prefix):].strip(" :-")
                    if what and self._add_decision(what):
                        captured["decisions"] += 1
                    break

        for line in assistant_output.splitlines():
            stripped = line.strip()
            for prefix in ("fact:", "fakta:", "catatan:", "note:"):
                if stripped.lower().startswith(prefix):
                    fact = stripped[len(prefix):].strip(" :-")
                    if fact and self._add_fact(fact):
                        captured["facts"] += 1
                    break

        for err in errors or []:
            if err and self._add_error(err):
                captured["errors"] += 1

        if model_id:
            self._wm.record_model(model_id)

        return captured

    def _add_decision(self, what: str) -> bool:
        state = self._wm.state
        tokens = _tokenize(what)
        for existing in state.get("decisions", []):
            if _jaccard(tokens, _tokenize(existing.get("what", ""))) >= _DEDUP_SIMILARITY:
                existing["ts"] = time.time()
                self._wm._save_state()
                return False
        self._wm.record_decision(what)
        return True

    def _add_fact(self, fact: str) -> bool:
        state = self._wm.state
        tokens = _tokenize(fact)
        for existing in state.get("facts", []):
            if _jaccard(tokens, _tokenize(existing.get("fact", ""))) >= _DEDUP_SIMILARITY:
                existing["ts"] = time.time()
                self._wm._save_state()
                return False
        self._wm.record_fact(fact)
        return True

    def _add_error(self, err: str) -> bool:
        state = self._wm.state
        tokens = _tokenize(err)
        for existing in state.get("errors", []):
            if _jaccard(tokens, _tokenize(existing.get("err", ""))) >= _DEDUP_SIMILARITY:
                existing["ts"] = time.time()
                self._wm._save_state()
                return False
        self._wm.record_error(err)
        return True

    def snapshot(self, user_input: str = "", token_budget: int = 400) -> MemorySnapshot:
        summary = self.build_summary(token_budget=token_budget)
        top = self.score_files(user_input, limit=15)
        keywords = sorted(_tokenize(user_input))[:_MAX_KEYWORDS]
        return MemorySnapshot(
            workspace=str(self._ws),
            summary=summary,
            top_files=top,
            keywords=keywords,
            turn_count=int(self._wm.state.get("turn_count", 0)),
            token_estimate=_estimate_tokens(summary),
        )


__all__ = ["SmartMemory", "ScoredFile", "MemorySnapshot"]
