"""The M5 Kalman-HMM trend-pullback model: regime, entry rule, stop and target.

BUY (evaluated once per closed M5 bar, t):
    P(trend_t) > 0.65  and  slope_t > 0  and  C_t > VWAP_t
    and  min(Z_{t-3..t-1}) <= -0.8  and  Z_t >= -0.5  and  C_t > High_{t-1}
SELL is the mirror image. The pullback may be one to PULLBACK_BARS bars old: a pullback that
bottoms two bars before the breakout bar is the same setup, and requiring it on exactly the
previous bar threw most of them away.

Context filters, in the order they are checked (each one is a reason the panel shows):
    * no entries in the rollover hours (QUANT_BLOCK_HOURS_UTC, default 21 and 22 UTC), when
      spreads widen and gold and FX quotes thin out;
    * the higher timeframe (about the H1 EMA 20, features.htf_bias) must not point the other way;
    * room to the target: a recent swing high (BUY) less than ROOM_MIN_R stops above the entry
      is resistance the trade must break first, so it is skipped;
    * a spread wider than SPREAD_MAX_STOP_FRAC of the stop, and fading ADX, as before.

Stops and targets:
    D = max(1.2 ATR, Entry - LowestLow_5 + 0.1 ATR)     (BUY; SELL uses the highest high)
    SL = Entry - D,  TP = Entry + RR D, RR = 1.5, or RR_STRONG = 2.0 when the higher timeframe
    agrees, ADX is at least ADX_STRONG and no swing level sits inside 2.5 D.

Every proposal carries a 0..100 score of how many things line up (trend probability, ADX,
higher timeframe, room, pullback depth). It explains; it does not decide. QUANT_MIN_SCORE can
make it a gate.

Open trades (manage): a suggested stop, never sent by itself. Move the stop to entry plus
BE_LOCK_R once price has gone BE_TRIGGER_R in favour, then trail it TRAIL_ATR ATRs behind the
best price. backtest.run(manage=True) measures the same rule; it is off by default because live
trades are not managed automatically.

Regime features U_t = [|slope|/ATR, ADX/100, |C - VWAP|/ATR, ATR ratio]. The fourth one is
added on purpose: the spec's three features do not separate a high-volatility state from a
range, so the high-volatility label would have no basis. This is a deviation from the spec and
is documented here.

The model proposes. It never sends. The caller runs the risk engine, the news filter and the
user's confirmation before anything reaches a broker.
"""

from __future__ import annotations

import datetime as dt
import math
import os
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
PULLBACK_BARS = 3             # the Z pullback may have happened on any of the last 3 bars
ROOM_LOOKBACK = 48            # bars (4 hours) searched for the swing level in the way
ROOM_MIN_R = 1.0              # skip when that level is closer than 1 stop distance
RR_STRONG = 2.0
ROOM_STRONG_R = 2.5
ADX_STRONG = 25.0
# Trade management. On the recorded gold M5 data (2026-11), break-even at 1 R and a 2 or 3 ATR
# trail each cut winners and lowered the total R; 1.5 R and 4 ATR left it unchanged. The sample
# was 3 trades, so these are cautious defaults, not tuned values.
BE_TRIGGER_R = 1.5            # manage: price has gone 1.5 stop distances in favour
BE_LOCK_R = 0.1               # ... so the stop goes to entry + 0.1 R, which pays the costs
TRAIL_ATR = 4.0               # then trails 4 ATR behind the best price (0 = no trail)
DEFAULT_BLOCK_HOURS = (21, 22)


def block_hours() -> tuple[int, ...]:
    """UTC hours with no new entries. QUANT_BLOCK_HOURS_UTC="" turns the filter off."""
    raw = os.getenv("QUANT_BLOCK_HOURS_UTC")
    if raw is None:
        return DEFAULT_BLOCK_HOURS
    hours = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            hours.append(int(part))
    return tuple(hours)


def min_score() -> float:
    try:
        return float(os.getenv("QUANT_MIN_SCORE", "0") or 0)
    except ValueError:
        return 0.0


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
    score: float = 0.0
    rr: float = RR

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


def room_to_level(bars: list[dict[str, float]], i: int, side: str) -> float | None:
    """Distance from the close to the nearest swing level in the trade's way, or None when there is none.

    A swing high is a bar whose high beats the two bars on each side. The bar being decided and
    the one before it are left out: the breakout bar itself is not resistance.
    """
    c = bars[i]["c"]
    lo = max(2, i - ROOM_LOOKBACK)
    best: float | None = None
    for k in range(lo, i - 2):
        if side == "buy":
            h = bars[k]["h"]
            if h > c and all(h >= bars[j]["h"] for j in (k - 2, k - 1, k + 1, k + 2)):
                best = h - c if best is None else min(best, h - c)
        else:
            low = bars[k]["l"]
            if low < c and all(low <= bars[j]["l"] for j in (k - 2, k - 1, k + 1, k + 2)):
                best = c - low if best is None else min(best, c - low)
    return best


def _score(p_trend: float, adx_now: float, htf: int, side: int, room_r: float | None, z_depth: float) -> float:
    s = 35 * min(1.0, max(0.0, (p_trend - TREND_MIN) / (1 - TREND_MIN)))
    if adx_now == adx_now:
        s += 20 * min(1.0, max(0.0, (adx_now - 15) / 25))
    s += 20 if htf == side else 8 if htf == 0 else 0
    s += 15 if room_r is None or room_r >= ROOM_STRONG_R else 15 * min(1.0, max(0.0, (room_r - ROOM_MIN_R) / (ROOM_STRONG_R - ROOM_MIN_R)))
    s += 10 * min(1.0, max(0.0, (z_depth - abs(Z_LOW)) / 1.0 + 0.5))
    return round(s, 1)


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
    recent = [f.z[k] for k in range(max(0, i - PULLBACK_BARS), i) if f.z[k] == f.z[k]]
    z_min = min(recent) if recent else float("nan")
    z_max = max(recent) if recent else float("nan")
    buy = slope > 0 and c > vwap and z_min <= Z_LOW and z_now >= Z_BACK and c > prev["h"]
    sell = slope < 0 and c < vwap and z_max >= Z_HIGH and z_now <= Z_BACK_SELL and c < prev["l"]
    if not (buy or sell):
        p.blocked = "no entry on this bar"
        return p
    side = "buy" if buy else "sell"
    sign = 1 if buy else -1
    hour = dt.datetime.fromtimestamp(bars[i]["t"], dt.UTC).hour
    if hour in block_hours():
        p.blocked = f"{hour:02d}:00 UTC is a rollover hour (thin quotes, wide spreads): no new entries"
        return p
    htf = f.htf_bias[i] if f.htf_bias is not None and i < len(f.htf_bias) else 0
    if htf == -sign:
        p.blocked = f"the higher timeframe (H1 EMA 20) points {'down' if htf < 0 else 'up'}, against this {side}"
        return p
    d = stop_distance(bars, i, a, side)
    room = room_to_level(bars, i, side)
    room_r = room / d if room is not None and d > 0 else None
    if room_r is not None and room_r < ROOM_MIN_R:
        level = c + sign * (room or 0.0)
        p.blocked = (f"swing {'high' if buy else 'low'} {level:.5f} is only {room_r:.2f} stops away: "
                     "no room to the target")
        return p
    if spread is not None and spread > SPREAD_MAX_STOP_FRAC * d:
        p.blocked = (f"spread {spread:.5f} is more than {SPREAD_MAX_STOP_FRAC:.0%} of the stop distance {d:.5f}")
        return p
    rising = adx_rising(f, i)
    if rising is False:
        p.blocked = f"ADX fell over the last {ADX_LOOKBACK} bars: the trend is fading"
        return p
    adx_now = f.adx[i]
    strong = htf == sign and adx_now == adx_now and adx_now >= ADX_STRONG and (room_r is None or room_r >= ROOM_STRONG_R)
    rr = RR_STRONG if strong else RR
    z_depth = -z_min if buy else z_max
    p.score = _score(p_trend, adx_now, htf, sign, room_r, z_depth)
    if p.score < min_score():
        p.blocked = f"score {p.score:.0f} is below QUANT_MIN_SCORE={min_score():.0f}"
        return p
    p.rr = rr
    p.side, p.sl, p.tp = side, c - sign * d, c + sign * rr * d
    word = "up" if buy else "down"
    p.reasons += [f"slope {'positive' if buy else 'negative'}", f"close {'above' if buy else 'below'} VWAP",
                  f"Z pullback to {(z_min if buy else z_max):.2f}, now {z_now:.2f}: crossed {word}",
                  f"close {'above previous high' if buy else 'below previous low'}"]
    p.reasons.append("higher timeframe agrees" if htf == sign else "higher timeframe neutral")
    p.reasons.append(f"room to the next swing level: {room_r:.1f} stops" if room_r is not None else "no swing level in the way")
    if rising:
        p.reasons.append(f"ADX rising over {ADX_LOOKBACK} bars")
    p.reasons.append(f"target {rr:g}R ({'strong setup' if strong else 'standard'}), score {p.score:.0f}/100")
    return p


def manage(position: dict[str, Any], bars: list[dict[str, float]], atr: float) -> dict[str, Any] | None:
    """A better stop for an open trade, or None to leave it. Never widens the stop.

    `position` needs side, open_price and sl (the original stop sets 1 R). Bars are the closed bars
    since the trade opened. Break-even first, then an ATR trail behind the best price.
    """
    side = position.get("side")
    entry = float(position.get("open_price") or 0)
    sl = position.get("sl")
    if side not in ("buy", "sell") or not entry or not sl or not bars or not (atr == atr and atr > 0):
        return None
    sl = float(sl)
    sign = 1 if side == "buy" else -1
    risk = sign * (entry - sl)
    if risk <= 0:      # already at or beyond break-even: only the trail can tighten it
        risk = float(position.get("initial_risk") or 0) or atr
    best = max(b["h"] for b in bars) if sign > 0 else min(b["l"] for b in bars)
    gained_r = sign * (best - entry) / risk
    if gained_r < BE_TRIGGER_R:
        return None
    new_sl = entry + sign * BE_LOCK_R * risk
    trail = best - sign * TRAIL_ATR * atr if TRAIL_ATR > 0 else new_sl
    if sign * (trail - new_sl) > 0:
        new_sl = trail
    if sign * (new_sl - sl) <= 0:
        return None
    why = f"trail {TRAIL_ATR:g} ATR behind the best price" if TRAIL_ATR > 0 and new_sl == trail else f"break-even + {BE_LOCK_R:g}R after {BE_TRIGGER_R:g}R in favour"
    return {"ticket": position.get("ticket"), "sl": new_sl, "gained_r": round(gained_r, 2), "why": why}


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
