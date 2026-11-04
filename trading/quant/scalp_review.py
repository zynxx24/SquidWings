"""Who adjusts the basket policy after a loss: a fixed rule, or an LLM with the rule as its fallback.

Both see numbers about baskets that have already closed, and both answer with new policy values.
``Policy.apply`` forces every answer inside ``BOUNDS``, so a bad reply cannot leave them. Neither
can change volume, risk per trade or the daily stops: those stay with the risk engine.

The stated goal is 8 wins in 10, but the win rate alone is not the score. A narrow target raises
the win rate and can still lose money, so both reviewers are told to keep the expectancy positive.

An LLM reply takes seconds, so this suits a backtest or the pause between baskets. It cannot act
inside a 30-second trade.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from typing import Any

from model.connection.response_handler import ResponseHandler
from trading.quant.scalp import BOUNDS, TARGET_WIN_RATE
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_LLM_CALLS = 5
LLM_TIMEOUT_S = 20.0
MIN_RELAXED_COOLDOWN_S = 15.0
ANSWER_KEYS = ("threshold", "tp_atr", "sl_atr", "cooldown_s")


def rule_reviewer(stats: dict[str, Any], policy: dict[str, float]) -> dict[str, Any]:
    """The same inputs and limits as the LLM, with fixed steps. Always available, always offline."""
    if stats["window"] < 5:
        return {}
    change: dict[str, Any] = {}
    why = []
    if stats["losing_streak"] >= 2:
        change["cooldown_s"] = policy["cooldown_s"] * 2 or 30.0
        change["threshold"] = policy["threshold"] + 0.05
        why.append(f"{stats['losing_streak']} losses in a row: wait longer and demand more agreement")
    elif stats["expectancy_r"] < 0 and stats["win_rate"] >= TARGET_WIN_RATE:
        change["sl_atr"] = policy["sl_atr"] * 0.9
        why.append("winning often but still losing money: the losses are too big")
    elif stats["win_rate"] < TARGET_WIN_RATE:
        change["tp_atr"] = policy["tp_atr"] * 0.9
        why.append("win rate under target: take profit sooner")
    elif stats["expectancy_r"] > 0 and policy["cooldown_s"] >= MIN_RELAXED_COOLDOWN_S * 2:
        change["cooldown_s"] = policy["cooldown_s"] / 2
        why.append("on target and profitable: relax the pause")
    change["why"] = "; ".join(why)
    return change


def build_prompt(stats: dict[str, Any], policy: dict[str, float]) -> str:
    limits = {name: list(BOUNDS[name]) for name in ANSWER_KEYS}
    return (
        "You tune the exit settings of a 30-second, five-position scalping backtest after a loss. "
        f"The goal is a win rate near {TARGET_WIN_RATE:.0%} with a positive expectancy. Expectancy comes first: "
        "a higher win rate from a narrower target is worse if the expectancy falls.\n"
        "Return only a JSON object with any of these keys: threshold, tp_atr, sl_atr, cooldown_s, why. "
        f"Allowed ranges: {json.dumps(limits)}. Size and risk per trade are not yours to change.\n"
        f"Current policy: {json.dumps(policy)}\n"
        f"Last baskets: {json.dumps(stats)}"
    )


def parse_answer(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    data = json.loads(fenced.group(1) if fenced else text)
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def llm_reviewer(client: Any, model: str, *, max_calls: int = MAX_LLM_CALLS,
                 timeout: float = LLM_TIMEOUT_S) -> tuple[Callable[[dict[str, Any], dict[str, float]], dict[str, Any]], dict[str, int]]:
    """A reviewer that asks the LLM, up to `max_calls` times, then falls back to the rule.

    Returns the reviewer and a counter of calls and failures, so the result can say what really ran.
    """
    usage = {"llm_calls": 0, "llm_failures": 0}

    async def ask(prompt: str) -> Any:
        call = client.chat_completion(model=model, messages=[{"role": "user", "content": prompt}],
                                      temperature=0.0, max_tokens=256)
        return await call if inspect.isawaitable(call) else call

    def review(stats: dict[str, Any], policy: dict[str, float]) -> dict[str, Any]:
        if usage["llm_calls"] >= max_calls:
            return rule_reviewer(stats, policy)
        usage["llm_calls"] += 1
        try:
            raw = asyncio.run(asyncio.wait_for(ask(build_prompt(stats, policy)), timeout))
            return parse_answer(ResponseHandler.handle(raw).content)
        except Exception as exc:  # noqa: BLE001 - a model failure must not end a backtest
            usage["llm_failures"] += 1
            logger.warning(f"[scalp] LLM review failed, using the rule: {exc}")
            return rule_reviewer(stats, policy)

    return review, usage


__all__ = ["rule_reviewer", "llm_reviewer", "build_prompt", "parse_answer", "MAX_LLM_CALLS"]
