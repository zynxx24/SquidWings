"""The M5 Kalman-HMM trend-pullback model: regime, entry rule, stop and target.

BUY (evaluated once per closed M5 bar, t):
    P(trend_t) > 0.65  and  slope_t > 0  and  C_t > VWAP_t
    and  Z_{t-1} <= -0.8  and  Z_t >= -0.5  and  C_t > High_{t-1}
SELL is the mirror image.

Stops and targets:
    D = max(1.2 ATR, Entry - LowestLow_5 + 0.1 ATR)     (BUY; SELL uses the highest high)
    SL = Entry - D,  TP = Entry + 1.5 D                  (1 : 1.5)

Regime features U_t = [|slope|/ATR, ADX/100, |C - VWAP|/ATR, ATR ratio]. The fourth one is
added on purpose: the spec's three features do not separate a high-volatility state from a
range, so the high-volatility label would have no basis. This is a deviation from the spec and
is documented here.

The model proposes. It never sends. The caller runs the risk engine, the news filter and the
user's confirmation before anything reaches a broker.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from trading.quant.features import Features
from trading.quant.hmm import GaussianHMM, label_states

TREND_MIN = 0.65
Z_LOW, Z_HIGH = -0.8, 0.8
Z_BACK = -0.5           # Z_t must rise to at least this (BUY)
Z_BACK_SELL = 0.5       # Z_t must fall to at most this (SELL)
RR = 1.5
TREND_EXIT_LOOKBACK = 5
# Quality gates shared by the live proposal and the backtest, so the backtest tests the same rule.
SPREAD_MAX_STOP_FRAC = 0.15   # skip when the spread eats more than 15% of the stop distance
ADX_LOOKBACK = 3              # trend strength must not be fading: ADX_t > ADX_{t-3}


@dataclass
class Proposal:
    symbol: str
    side: str | None
    entry: float
    sl: float | None
    tp: float | None
    regime: str
    p_trend: float
    atr: float
    z_prev: float
    z_now: float
    reasons: list[str] = field(default_factory=list)
    blocked: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def feature_vector(f: Features, bars: list[dict[str, float]], i: int) -> list[float] | None:
    a = f.atr[i]
    if a != a or a <= 0 or f.atr_ratio[i] != f.atr_ratio[i] or f.adx[i] != f.adx[i]:
        return None
    c = bars[i]["c"]
    return [abs(f.slope[i]) / a, f.adx[i] / 100.0, abs(c - f.vwap[i]) / a, f.atr_ratio[i]]


def stop_distance(bars: list[dict[str, float]], i: int, atr: float, side: str) -> float:
    window = bars[max(0, i - TREND_EXIT_LOOKBACK + 1): i + 1]
    if side == "buy":
        extreme = min(b["l"] for b in window)
        return max(1.2 * atr, bars[i]["c"] - extreme + 0.1 * atr)
    extreme = max(b["h"] for b in window)
    return max(1.2 * atr, extreme - bars[i]["c"] + 0.1 * atr)


def adx_rising(f: Features, i: int) -> bool | None:
    """True when trend strength grew over the last ADX_LOOKBACK bars. None when it cannot be known yet."""
    j = i - ADX_LOOKBACK
    if j < 0 or f.adx[i] != f.adx[i] or f.adx[j] != f.adx[j]:
        return None
    return f.adx[i] > f.adx[j]


def decide(symbol: str, bars: list[dict[str, float]], f: Features, post: list[float] | None,
           states: dict[str, int] | None, i: int, *, spread: float | None = None) -> Proposal:
    """The rule at bar i, given the regime posterior `post` (a list over states) for that bar.

    `spread` is the current ask minus bid in price units, when known. A wide spread relative to
    the stop is a cost the rule does not model, so the entry is skipped.
    """
    c = bars[i]["c"]
    a = f.atr[i]
    z_now = f.z[i]
    z_prev = f.z[i - 1] if i > 0 else float("nan")
    p = Proposal(symbol, None, c, None, None, "unknown", float("nan"), a, z_prev, z_now)
    if post is None or states is None or a != a or i < 1:
        p.blocked = "warming up: not enough history for the features"
        return p
    p_trend = post[states["trend"]]
    p_range = post[states["range"]]
    p_high = post[states["highvol"]]
    p.p_trend = p_trend
    p.regime = max((("trend", p_trend), ("range", p_range), ("highvol", p_high)), key=lambda kv: kv[1])[0]
    p.reasons.append(f"regime {p.regime} (P trend {p_trend:.2f}, range {p_range:.2f}, highvol {p_high:.2f})")
    if p_high >= max(p_trend, p_range):
        p.blocked = "high volatility is the dominant regime: no new entries"
        return p
    if p_range > p_trend:
        p.blocked = "range is the dominant regime: the trend strategy stays out"
        return p
    if p_trend <= TREND_MIN:
        p.blocked = f"trend probability {p_trend:.2f} is at or below {TREND_MIN}"
        return p
    slope = f.slope[i]
    vwap = f.vwap[i]
    prev = bars[i - 1]
    buy = slope > 0 and c > vwap and z_prev <= Z_LOW and z_now >= Z_BACK and c > prev["h"]
    sell = slope < 0 and c < vwap and z_prev >= Z_HIGH and z_now <= Z_BACK_SELL and c < prev["l"]
    if not (buy or sell):
        p.blocked = "no entry on this bar"
        return p
    side = "buy" if buy else "sell"
    d = stop_distance(bars, i, a, side)
    if spread is not None and spread > SPREAD_MAX_STOP_FRAC * d:
        p.blocked = (f"spread {spread:.5f} is more than {SPREAD_MAX_STOP_FRAC:.0%} of the stop distance {d:.5f}")
        return p
    rising = adx_rising(f, i)
    if rising is False:
        p.blocked = f"ADX fell over the last {ADX_LOOKBACK} bars: the trend is fading"
        return p
    if side == "buy":
        p.side, p.sl, p.tp = "buy", c - d, c + RR * d
        p.reasons += ["slope positive", "close above VWAP", f"Z {z_prev:.2f} -> {z_now:.2f} crossed up",
                      "close above previous high"]
    else:
        p.side, p.sl, p.tp = "sell", c + d, c - RR * d
        p.reasons += ["slope negative", "close below VWAP", f"Z {z_prev:.2f} -> {z_now:.2f} crossed down",
                      "close below previous low"]
    if rising:
        p.reasons.append(f"ADX rising over {ADX_LOOKBACK} bars")
    return p


def states_for(model: GaussianHMM) -> dict[str, int]:
    return label_states(model)


def sanity(p: Proposal) -> list[str]:
    """Reasons a proposal is not even well formed (used by tests and the risk engine)."""
    out: list[str] = []
    if p.side is None:
        return out
    if p.sl is None or p.tp is None or not (math.isfinite(p.sl) and math.isfinite(p.tp)):
        out.append("missing stop or target")
    elif p.side == "buy" and not (p.sl < p.entry < p.tp):
        out.append("buy levels out of order")
    elif p.side == "sell" and not (p.tp < p.entry < p.sl):
        out.append("sell levels out of order")
    return out
