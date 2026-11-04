"""Checks every order passes before it is previewed or sent.

Limits come from the environment, so the owner sets them once and the web page cannot
loosen them:

* ``MT5_MAX_VOLUME``   largest volume in lots per order (default 0.10)
* ``MT5_REQUIRE_SL``   1 = every order needs a stop loss (default 1)
* ``MT5_ALLOW_LIVE``   1 = a real-money account may be used at all (default 0: demo only)
"""

from __future__ import annotations

import math
import os
from typing import Any

from trading.mt5_adapter import SIDES


def limits() -> dict[str, Any]:
    def num(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, default))
        except ValueError:
            return default

    return {
        "max_volume": num("MT5_MAX_VOLUME", 0.10),
        "require_sl": os.getenv("MT5_REQUIRE_SL", "1").strip() != "0",
        "allow_live": os.getenv("MT5_ALLOW_LIVE", "0").strip() == "1",
    }


def check_account(account: dict[str, Any]) -> list[str]:
    if account.get("live") and not limits()["allow_live"]:
        return ["This is a real-money account. Set MT5_ALLOW_LIVE=1 to allow it; demo accounts work without it."]
    return []


def _multiple_of(value: float, step: float) -> bool:
    if step <= 0:
        return True
    k = value / step
    return abs(k - round(k)) < 1e-6


def check_order(order: dict[str, Any], spec: dict[str, Any], tick: dict[str, Any]) -> list[str]:
    """Problems with a market order, as messages for the user. An empty list means it may be sent."""
    lim = limits()
    problems: list[str] = []
    if order.get("side") not in SIDES:
        problems.append("Side must be buy or sell.")
    volume = float(order.get("volume") or 0)
    if volume <= 0:
        problems.append("Volume must be above zero.")
    else:
        if volume < spec["volume_min"]:
            problems.append(f"Volume {volume} is below the minimum {spec['volume_min']}.")
        if volume > spec["volume_max"]:
            problems.append(f"Volume {volume} is above the broker maximum {spec['volume_max']}.")
        if volume > lim["max_volume"]:
            problems.append(f"Volume {volume} is above the limit MT5_MAX_VOLUME={lim['max_volume']}.")
        if not _multiple_of(volume, spec["volume_step"]):
            problems.append(f"Volume must be a multiple of {spec['volume_step']}.")
    problems += check_levels(order.get("side"), order.get("sl"), order.get("tp"), tick, spec)
    if len(str(order.get("comment") or "")) > 31:
        problems.append("Comment is longer than 31 characters.")
    return problems


def check_levels(side: str | None, sl: Any, tp: Any, tick: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Stop loss and take profit for a buy or sell at the current price.

    ``stops_level`` is the broker's minimum distance from the price, in price units. A level
    closer than that is refused by the broker, so it is refused here first with the reason.
    """
    lim = limits()
    problems: list[str] = []
    ref = tick["ask"] if side == "buy" else tick["bid"]
    digits = spec["digits"]
    stops = float(spec.get("stops_level") or 0)
    if lim["require_sl"] and not sl:
        problems.append("A stop loss is required (MT5_REQUIRE_SL=1).")
    for name, level in (("Stop loss", sl), ("Take profit", tp)):
        if level is None or level == 0:
            continue
        if not isinstance(level, (int, float)) or not math.isfinite(level) or level <= 0:
            problems.append(f"{name} must be a positive price.")
            continue
        if abs(level - round(level, digits)) > 1e-9:
            problems.append(f"{name} has more decimals than the symbol ({digits}).")
    if sl and side == "buy" and sl >= ref:
        problems.append("For a buy, the stop loss must be below the current ask.")
    if sl and side == "sell" and sl <= ref:
        problems.append("For a sell, the stop loss must be above the current bid.")
    if tp and side == "buy" and tp <= ref:
        problems.append("For a buy, the take profit must be above the current ask.")
    if tp and side == "sell" and tp >= ref:
        problems.append("For a sell, the take profit must be below the current bid.")
    if stops > 0:
        for name, level in (("Stop loss", sl), ("Take profit", tp)):
            if level and isinstance(level, (int, float)) and abs(level - ref) < stops:
                problems.append(f"{name} is closer than the broker minimum of {stops:g} from the price.")
    return problems


def check_modify(position: dict[str, Any], sl: Any, tp: Any, spec: dict[str, Any], tick: dict[str, Any]) -> list[str]:
    """Problems with new SL/TP on an open position. The side is the position's own side."""
    if position.get("side") not in SIDES:
        return ["Position side is unknown."]
    return check_levels(position["side"], sl, tp, tick, spec)


def risk_at_stop(volume: float, risk_per_lot: float) -> float:
    """Money lost if the stop is hit, for ``volume`` lots. ``risk_per_lot`` comes from the broker."""
    return round(abs(volume) * abs(risk_per_lot), 2)
