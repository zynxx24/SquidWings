"""Auto mode's conductor: the cheapest model that will do this request well.

Used when a chat is on **Auto** and ``ROUTING_QUALITY=smart`` (the default). A model the user
picks never comes here: that one is used as it is, and its errors are reported, not routed
around (see ``brain/agent/orchestrator.py``).

How a model is chosen:

1. **Tier from the request.** easy -> lite, medium -> standard, hard -> frontier. A request that
   needs tools on a lite tier goes to standard: small models fumble tool calls, and a failed
   tool loop costs more tokens than the bigger model would have.
2. **Escalation memory.** When this chat's last Auto turn on a tier went wrong (an error, a
   fallback, an unresolved verification), the next turn starts one tier higher. A turn that goes
   well lowers it again, so one hard question does not keep the chat expensive.
3. **Hard filters.** Healthy, a chat model, a context window that holds the conversation, native
   tool calling when tools are needed (else any), vision when images are attached.
4. **Price for quality.** Within the tier, the lowest expected cost per successful answer:
   ``price / success`` where success is the smoothed rate this model has had here, and price is
   from the Router's price book (or a tier default when unknown). Untried models get a fair
   prior, so new models are tried without a config edit.
5. **Nothing fits** the tier: the next tier up, then down.

``choose`` returns the model and one line of reasons, shown in the chat's route event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brain.harness.tiers import Tier, tier_for
from brain.utils.cache import cache
from brain.utils.logger import get_logger

logger = get_logger(__name__)

# USD for a typical turn (4k tokens in, 800 out) when the price book does not know the model.
_TIER_COST = {Tier.LITE: 0.002, Tier.STANDARD: 0.012, Tier.FRONTIER: 0.06}
_TYPICAL_IN, _TYPICAL_OUT = 4000, 800
_MIN_SUCCESS = 0.35
_ESCALATION = cache("auto.escalation", max_entries=2048, ttl=6 * 3600)
_BY_DIFFICULTY = {"easy": Tier.LITE, "medium": Tier.STANDARD, "hard": Tier.FRONTIER}
_TOOL_HEAVY_TASKS = {"code", "activity"}


@dataclass(frozen=True)
class Needs:
    """What this request needs from a model."""

    tools: bool = False
    vision: bool = False
    tokens: int = 0          # rough size of the conversation, so the window holds it


@dataclass(frozen=True)
class Choice:
    model: Any
    tier: Tier
    reason: str


def escalation(session_id: str) -> int:
    return int(_ESCALATION.get((session_id, "bump"), 0) or 0)


def record(session_id: str, ok: bool) -> None:
    """After an Auto turn: a bad one bumps the next turn a tier up, a good one steps back down."""
    if not session_id:
        return
    bump = escalation(session_id)
    _ESCALATION.set((session_id, "bump"), min(bump + 1, 2) if not ok else max(bump - 1, 0))


def target_tier(difficulty: str, task: str, needs: Needs, bump: int = 0) -> Tier:
    tier = _BY_DIFFICULTY.get(difficulty, Tier.STANDARD)
    if needs.tools and task in _TOOL_HEAVY_TASKS and tier == Tier.LITE:
        tier = Tier.STANDARD
    return Tier(min(int(tier) + bump, int(Tier.FRONTIER)))


def _cost(model_id: str, tier: Tier) -> float:
    try:
        from brain.model.router.pricing import cost_of

        price = cost_of(model_id, prompt=_TYPICAL_IN, output=_TYPICAL_OUT)
    except Exception:  # noqa: BLE001 - a broken price book must not stop routing
        price = None
    return float(price) if price and price > 0 else _TIER_COST[tier]


def _success(picker: Any, model_id: str) -> float:
    stats = picker.stats(model_id) if picker is not None else {}
    ok, fail = float(stats.get("ok", 0.0)), float(stats.get("fail", 0.0))
    return (ok + 2.0) / (ok + fail + 3.0)   # prior: 2 of 3 succeed, so a new model gets a fair try


def _fits(model: Any, needs: Needs) -> bool:
    window = int(getattr(model, "context_window", 0) or 0)
    if needs.tokens and window and window < needs.tokens * 1.25:
        return False
    return not (needs.vision and not getattr(model, "supports_vision", True))


def choose(models: list[Any], picker: Any, *, task: str, difficulty: str, needs: Needs,
           healthy: list[str] | None = None, session_id: str = "") -> Choice | None:
    """The model for an Auto turn, or None when nothing is usable."""
    from brain.model.settings.model_picker import is_chat_model

    bump = escalation(session_id)
    target = target_tier(difficulty, task, needs, bump)
    pool = [m for m in models if is_chat_model(m.id) and (healthy is None or m.id in healthy) and _fits(m, needs)]
    if not pool:
        return None
    by_tier: dict[Tier, list[Any]] = {}
    for m in pool:
        by_tier.setdefault(tier_for(m.id), []).append(m)
    order = [target, *[Tier(t) for t in range(int(target) + 1, int(Tier.FRONTIER) + 1)],
             *[Tier(t) for t in range(int(target) - 1, int(Tier.LITE) - 1, -1)]]
    for tier in order:
        candidates = by_tier.get(tier, [])
        if needs.tools:
            native = [m for m in candidates if getattr(m, "supports_tools", False)]
            candidates = native or candidates
        if not candidates:
            continue
        scored = []
        for m in candidates:
            success = _success(picker, m.id)
            cost = _cost(m.id, tier)
            scored.append((cost / max(success, 0.05), success < _MIN_SUCCESS, m, success, cost))
        # Reliable models first, then the lowest cost per successful answer, then the name for a stable order.
        scored.sort(key=lambda item: (item[1], item[0], item[2].id))
        _, _, model, success, cost = scored[0]
        reasons = [f"{difficulty}/{task}", f"tier {tier.name.lower()}"]
        if bump:
            reasons.append(f"naik {bump} tier setelah giliran yang gagal")
        if tier != target:
            reasons.append(f"tier {target.name.lower()} tidak tersedia")
        reasons.append(f"sukses {success:.0%}, ~${cost:.4f}/giliran")
        logger.info(f"[autopilot] {model.id}: {'; '.join(reasons)}")
        return Choice(model=model, tier=tier, reason="; ".join(reasons))
    return None


__all__ = ["Choice", "Needs", "choose", "escalation", "record", "target_tier"]
