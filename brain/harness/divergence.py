"""Think wider: keep the model from handing over the same first idea every time.

A model asked twice for a design, an algorithm, a proof or a paragraph gives nearly the same answer twice, and the
same answer as every other model, because it stops at its first idea. This adds one block to a turn that has room
for choice. The block:

* names the autopilot answers of that kind of work, so they become the baseline to beat, not the answer;
* draws three axes along which real alternatives differ, and three lenses (reframings), skipping the ones used
  lately in any chat (``brain.utils.style_history``), so consecutive turns push in different directions;
* asks for a few genuinely different approaches, the strongest (or a blend) chosen with a reason, and a proof that
  fits the work: run the code, solve the maths a second way, check the page against its direction.

The catalog is ``input/think/lenses.yaml``; editing it applies on the next turn. ``THINK_WIDER=off`` switches it off.
The block goes after the cached prompt prefix, so it costs a few hundred tokens and no cache.
"""

from __future__ import annotations

import os
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from brain.config.constants import TaskType
from brain.utils import style_history
from brain.utils.cache import FileWatch

LENSES_FILE = Path(__file__).resolve().parents[2] / "input" / "think" / "lenses.yaml"
AXES_PER_TURN = 3
LENSES_PER_TURN = 3
# Requests this short with no domain word are chat, not work with room for choice.
_MIN_WORDS = 4
# The user asks for something different: the block says so and asks to change more.
_AGAIN_RE = re.compile(
    r"\b(beda|lain|lainnya|jangan sama|jangan yang sama|variasi|alternatif|alternative|different|another|other|"
    r"kreatif|creative|inovatif|innovative|unik|unique|out of the box|ganti|fresh|baru lagi|itu itu aja|monoton)\b",
    re.IGNORECASE,
)
# Requests with one right answer and nothing to choose.
_PLAIN_RE = re.compile(
    r"^\s*(halo|hai|hi|hello|thanks|terima kasih|makasih|ok|oke|sip|lanjut|lanjutkan|continue|ya|tidak|yes|no)\b[\s.!?]*$",
    re.IGNORECASE,
)
_BY_TASK = {
    TaskType.CODE: "code",
    TaskType.MATH: "math",
    TaskType.GENERATE_IMG: "design",
    TaskType.REASON: "reason",
}
_watch = FileWatch(lambda: [LENSES_FILE], interval=3.0)


def enabled() -> bool:
    return os.getenv("THINK_WIDER", "on").strip().lower() not in {"0", "off", "false", "no"}


@lru_cache(maxsize=1)
def _load() -> dict[str, dict[str, Any]]:
    try:
        data = yaml.safe_load(LENSES_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def catalog() -> dict[str, dict[str, Any]]:
    if _watch.changed():
        _load.cache_clear()
    return _load()


def _hits(text: str, words: list[str]) -> int:
    return sum(1 for w in words if re.search(rf"(?<![\w]){re.escape(str(w).lower())}(?![\w])", text))


def domains_for(task_type: TaskType | str | None, text: str) -> list[str]:
    """The kinds of work a request is, most fitting first. Words weigh more than the task type, because a
    "code" request to build a dashboard is a design question as much as a code one."""
    data = catalog()
    lowered = (text or "").lower()
    scores = {name: _hits(lowered, list(entry.get("words") or [])) for name, entry in data.items()}
    try:
        kind = TaskType(task_type) if task_type else None
    except ValueError:
        kind = None
    preferred = _BY_TASK.get(kind) if kind else None
    if preferred in scores:
        scores[preferred] += 2
    ranked = [name for name, score in sorted(scores.items(), key=lambda item: -item[1]) if score > 0]
    return ranked[:2]


def worth_it(text: str, domains: list[str]) -> bool:
    """A turn with room for choice: not a greeting, a yes, or a two-word reply."""
    if not domains or _PLAIN_RE.match(text or ""):
        return False
    return len((text or "").split()) >= _MIN_WORDS or bool(_AGAIN_RE.search(text or ""))


def _draw(options: list[str], count: int, recent: set[str], rng: random.Random) -> list[str]:
    fresh = [o for o in options if o not in recent]
    pool = fresh if len(fresh) >= count else options
    return rng.sample(pool, k=min(count, len(pool)))


def block(task_type: TaskType | str | None, text: str, rng: random.Random | None = None,
          remember: bool = True) -> str:
    """The "think wider" block for this turn, or "" when the turn has nothing to choose."""
    if not enabled():
        return ""
    domains = domains_for(task_type, text)
    if not worth_it(text, domains):
        return ""
    data = catalog()
    rng = rng or random.SystemRandom()
    main = domains[0]
    entry = data[main]
    axes = list(entry.get("axes") or [])
    lenses = list(entry.get("lenses") or [])
    # A second domain lends one lens: a dashboard is design and code, a pricing page is design and writing.
    extra = [str(x) for x in (data[domains[1]].get("lenses") or [])] if len(domains) > 1 else []
    recent = set(style_history.recent(f"think-lens-{main}"))
    picked_axes = _draw(axes, AXES_PER_TURN, set(), rng)
    picked_lenses = _draw(lenses, LENSES_PER_TURN - (1 if extra else 0), recent, rng)
    if extra:
        picked_lenses += _draw(extra, 1, set(style_history.recent(f"think-lens-{domains[1]}")), rng)
    if remember:
        for lens in picked_lenses:
            style_history.remember(f"think-lens-{main}", lens)
    autopilot = "; ".join(str(a) for a in entry.get("autopilot") or [])
    again = bool(_AGAIN_RE.search(text or ""))
    label = main if len(domains) == 1 else f"{main} + {domains[1]}"
    lines = [
        f"\n\n[THINK WIDER · {label}]",
        "Your first idea is what every model would hand over. Before you answer or build, go past it:",
        f"1. Autopilot to beat (do not ship these unless they are truly best here): {autopilot}.",
        "2. Sketch three genuinely different approaches that differ on these axes; one of them should feel risky:",
        *[f"   - {axis}" for axis in picked_axes],
        "3. Look at them through these lenses:",
        *[f"   - {lens}" for lens in picked_lenses],
        "4. Pick the strongest or combine two, and say in one line why it beats the obvious answer. Then build it "
        "fully, not as a sketch.",
        f"5. Prove it: {entry.get('proof', 'check the result against the request')}",
        "Do the exploring briefly, in your head or your plan; the user gets the finished result and one line on the "
        "choice, not the brainstorm. When the request has exactly one right answer, just give it, correctly.",
    ]
    if again:
        lines.append(
            "The user is asking for something different: do not reuse the structure, method or look of what you "
            "made earlier in this conversation. Change at least two of the axes above."
        )
    return "\n".join(lines)


def temperature_for(domains: list[str], current: float) -> float:
    """Creative work samples a little wider; code and maths keep their precision."""
    if domains and domains[0] in {"design", "writing"}:
        return max(current, 0.8)
    return current
