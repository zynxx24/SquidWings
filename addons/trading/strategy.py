"""M5 pullback strategy: proposes trades, never sends them.

Rules, all on closed M5 bars:
* Trend: EMA9 > EMA21 > EMA50 for a buy (mirrored for a sell).
* Pullback: the close is within 0.5 ATR of EMA21, on the trend side.
* Momentum: RSI14 between 45 and 65 for a buy (35 to 55 for a sell), rising for two bars.
* Confirmation: the last bar closes in the trade direction.
* Volatility regime: ATR14 against its median over 100 bars. Above 2.0x or below 0.5x
  the market is too wild or too dead, and no trade is proposed.
* News: no trade within 30 minutes of a high-impact event for either currency.
* Stop 1.5 ATR, target 2.5 ATR, so the target is 1.67 times the risk.

Nothing here is a promise of profit. Run ``backtest`` on your own bars and read the
numbers before trusting a rule. The backtest assumes the stop is hit first when a bar
touches both levels, which is the pessimistic choice.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

NEWS_WINDOW_MIN = 30
SL_ATR = 1.5
TP_ATR = 2.5


# ---------------------------------------------------------------- indicators


def ema(values: list[float], n: int) -> list[float]:
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(bars: list[dict[str, float]], n: int = 14) -> list[float]:
    trs: list[float] = []
    prev_close = None
    for b in bars:
        tr = b["h"] - b["l"] if prev_close is None else max(b["h"] - b["l"], abs(b["h"] - prev_close), abs(b["l"] - prev_close))
        trs.append(tr)
        prev_close = b["c"]
    out: list[float] = []
    for i in range(len(trs)):
        if i + 1 < n:
            out.append(float("nan"))
        elif i + 1 == n:
            out.append(sum(trs[:n]) / n)
        else:
            out.append((out[-1] * (n - 1) + trs[i]) / n)
    return out


def rsi(values: list[float], n: int = 14) -> list[float]:
    out = [float("nan")] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    out[n] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def regime(atr_values: list[float]) -> tuple[str, float]:
    """(name, ratio): ratio = current ATR over the median ATR of the last 100 bars."""
    clean = [a for a in atr_values[-100:] if a == a]
    if len(clean) < 20 or atr_values[-1] != atr_values[-1]:
        return "unknown", float("nan")
    ratio = atr_values[-1] / statistics.median(clean)
    if ratio > 2.0:
        return "volatile", ratio
    if ratio < 0.5:
        return "dead", ratio
    return "normal", ratio


# ---------------------------------------------------------------- news


@dataclass
class NewsEvent:
    time: dt.datetime
    currency: str
    impact: str
    title: str


def load_news(items: Iterable[dict[str, Any]]) -> list[NewsEvent]:
    out = []
    for it in items:
        t = dt.datetime.fromisoformat(str(it["time"]).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.UTC)
        out.append(NewsEvent(t, str(it["currency"]).upper(), str(it.get("impact", "high")).lower(), str(it.get("title", ""))))
    return out


def news_block(symbol: str, when: dt.datetime, events: list[NewsEvent]) -> str | None:
    """A reason string if a high-impact event for either currency is near `when`, else None."""
    base, quote = symbol[:3].upper(), symbol[3:6].upper()
    window = dt.timedelta(minutes=NEWS_WINDOW_MIN)
    for e in events:
        if e.impact != "high" or e.currency not in (base, quote):
            continue
        if abs(e.time - when) <= window:
            return f"high-impact {e.currency} news within {NEWS_WINDOW_MIN} min: {e.title}"
    return None


# ---------------------------------------------------------------- signal


@dataclass
class Proposal:
    symbol: str
    side: str | None
    entry: float
    sl: float | None
    tp: float | None
    regime: str
    atr: float
    reasons: list[str] = field(default_factory=list)
    blocked: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def signal(symbol: str, bars: list[dict[str, float]], when: dt.datetime | None = None,
           news: list[NewsEvent] | None = None) -> Proposal:
    """Look at the bars up to the last one and say whether a trade is proposed."""
    if len(bars) < 60:
        return Proposal(symbol, None, bars[-1]["c"] if bars else float("nan"), None, None, "unknown", float("nan"),
                        blocked="not enough bars (need 60)")
    # The rules read at most ~150 bars back (EMA50 settles, ATR median uses 100). Keeping 300
    # keeps a long backtest linear instead of recomputing the whole history on every bar.
    bars = bars[-300:]
    closes = [b["c"] for b in bars]
    e9, e21, e50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    a = atr(bars)
    r = rsi(closes)
    name, ratio = regime(a)
    last = bars[-1]
    entry, atr_now = last["c"], a[-1]
    p = Proposal(symbol, None, entry, None, None, name, atr_now)
    p.reasons.append(f"ATR regime {name} (x{ratio:.2f} of median)")
    if name in ("volatile", "dead", "unknown"):
        p.blocked = f"volatility regime is {name}"
        return p
    if when is not None and news:
        reason = news_block(symbol, when, news)
        if reason:
            p.blocked = reason
            return p

    up = e9[-1] > e21[-1] > e50[-1]
    down = e9[-1] < e21[-1] < e50[-1]
    near = abs(entry - e21[-1]) <= 0.5 * atr_now
    rising = r[-1] > r[-2] if r[-2] == r[-2] else False
    falling = r[-1] < r[-2] if r[-2] == r[-2] else False
    if up and near and entry >= e21[-1] and 45 <= r[-1] <= 65 and rising and last["c"] > last["o"]:
        p.side = "buy"
        p.sl, p.tp = entry - SL_ATR * atr_now, entry + TP_ATR * atr_now
        p.reasons += ["EMA9 > EMA21 > EMA50 (uptrend)", "pullback to EMA21", f"RSI {r[-1]:.1f} rising", "bullish bar"]
    elif down and near and entry <= e21[-1] and 35 <= r[-1] <= 55 and falling and last["c"] < last["o"]:
        p.side = "sell"
        p.sl, p.tp = entry + SL_ATR * atr_now, entry - TP_ATR * atr_now
        p.reasons += ["EMA9 < EMA21 < EMA50 (downtrend)", "pullback to EMA21", f"RSI {r[-1]:.1f} falling", "bearish bar"]
    else:
        p.blocked = "no setup on the last closed bar"
    return p


# ---------------------------------------------------------------- backtest


def backtest(bars: list[dict[str, float]], max_hold: int = 48, news: list[NewsEvent] | None = None,
             symbol: str = "XXXYYY") -> dict[str, Any]:
    """Replay the rules bar by bar. Entry is the next bar's open; the stop wins a tie."""
    trades: list[dict[str, Any]] = []
    i = 60
    while i < len(bars) - 1:
        when = dt.datetime.fromtimestamp(bars[i]["t"], dt.UTC) if news else None
        p = signal(symbol, bars[: i + 1], when=when, news=news)
        if p.side is None:
            i += 1
            continue
        entry = bars[i + 1]["o"]
        if p.sl is None:
            i += 1
            continue
        risk = abs(p.entry - p.sl)
        if risk <= 0:
            i += 1
            continue
        sl = entry - risk if p.side == "buy" else entry + risk
        tp = entry + risk * (TP_ATR / SL_ATR) if p.side == "buy" else entry - risk * (TP_ATR / SL_ATR)
        outcome, exit_bar, exit_px = "timeout", min(i + 1 + max_hold, len(bars) - 1), bars[min(i + 1 + max_hold, len(bars) - 1)]["c"]
        for j in range(i + 1, min(i + 1 + max_hold, len(bars))):
            b = bars[j]
            hit_sl = b["l"] <= sl if p.side == "buy" else b["h"] >= sl
            hit_tp = b["h"] >= tp if p.side == "buy" else b["l"] <= tp
            if hit_sl:
                outcome, exit_bar, exit_px = "sl", j, sl
                break
            if hit_tp:
                outcome, exit_bar, exit_px = "tp", j, tp
                break
        sign = 1 if p.side == "buy" else -1
        r_mult = (exit_px - entry) * sign / risk
        trades.append({"i": i, "side": p.side, "entry": entry, "exit": exit_px, "outcome": outcome,
                       "r": round(r_mult, 3), "bars": exit_bar - i - 1, "reasons": p.reasons})
        i = exit_bar + 1
    return {"summary": summarize(trades), "trades": trades}


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "note": "no trades: the rules never lined up on this data"}
    wins = [t["r"] for t in trades if t["r"] > 0]
    losses = [t["r"] for t in trades if t["r"] <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 3),
        "expectancy_r": round(sum(t["r"] for t in trades) / n, 3),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "max_losing_streak": _longest(trades),
        "note": "small sample: treat as noise under ~100 trades" if n < 100 else "",
    }


def _longest(trades: list[dict[str, Any]]) -> int:
    best = cur = 0
    for t in trades:
        cur = cur + 1 if t["r"] <= 0 else 0
        best = max(best, cur)
    return best


__all__ = ["ema", "atr", "rsi", "regime", "load_news", "news_block", "signal", "backtest", "summarize", "Proposal", "NewsEvent"]
