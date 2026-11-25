"""The arithmetic of risk: what a trade costs, what a record is worth, and how sure anyone can be of it.

Everything here is a pure function, so the LLM prompts, the panel, the simulation and the tests
read the same numbers. Nothing here decides a trade; ``risk.py`` still says yes or no to an order.

The basics, as a trader writes them:

* position size ``lots = (equity x risk%) / (stop distance x value per point)``, floored to the step;
* reward to risk ``RR = (target - entry) / (entry - stop)``;
* the win rate that breaks even ``1 / (1 + RR)``, and with a cost paid on every trade ``(1 + c) / (1 + RR)``;
* expectancy ``E = WR x avg win - (1 - WR) x avg loss``;
* Kelly ``f = WR - (1 - WR) / RR``, used at half and never on a win rate taken at face value;
* drawdown ``(peak - trough) / peak`` and what it takes to come back, ``DD / (1 - DD)``;
* pip value, margin, compounding.

And what the basics leave out, which is where accounts are lost:

* a win rate from few trades is a guess. ``beta_lower`` gives the rate a cautious person would
  plan on (the Beta posterior's mean minus its spread), and ``kelly_safe`` sizes on that;
* an average result needs a test before it is called an edge: ``mean_test`` (Student's t), and
  ``sidak`` for when several rules were tried and the best one is reported;
* losing streaks are certain: ``losing_streak_probability`` is exact (a Markov chain over the run
  length) and ``risk_of_ruin`` solves the gambler's-ruin equation for unequal wins and losses.
"""

from __future__ import annotations

import math
from typing import Any

# ── the basics ──────────────────────────────────────────────────────────────


def position_size(equity: float, risk_pct: float, stop_distance: float, value_per_point: float, *, step: float = 0.01,
                  minimum: float = 0.01, maximum: float | None = None) -> dict[str, Any]:
    """Lots whose stop costs at most ``risk_pct`` percent of ``equity``.

    ``stop_distance`` is in price, ``value_per_point`` is the money one lot makes per 1.0 of price.
    The size is floored to ``step``, so the real risk is never above the request.
    """
    if equity <= 0 or risk_pct <= 0 or stop_distance <= 0 or value_per_point <= 0:
        raise ValueError("equity, risk_pct, stop_distance and value_per_point must be positive")
    risk_money = equity * risk_pct / 100.0
    loss_per_lot = stop_distance * value_per_point
    lots = math.floor(risk_money / loss_per_lot / step + 1e-9) * step
    if maximum is not None:
        lots = min(lots, maximum)
    lots = round(lots, 8)
    return {"lots": lots, "risk_money": round(risk_money, 2), "loss_per_lot": round(loss_per_lot, 2),
            "risk_money_actual": round(lots * loss_per_lot, 2),
            "risk_pct_actual": round(lots * loss_per_lot / equity * 100, 4),
            "tradable": lots >= minimum,
            "note": "" if lots >= minimum else f"di bawah lot minimum {minimum:g}: stop terlalu jauh untuk risiko ini"}


def risk_reward(entry: float, stop: float, target: float) -> float:
    """Reward divided by risk. Raises when the stop and target are on the same side of the entry."""
    risk, reward = entry - stop, target - entry
    if risk == 0 or risk * reward <= 0:
        raise ValueError("stop and target must be on opposite sides of the entry")
    return abs(reward / risk)


def breakeven_win_rate(rr: float, cost_r: float = 0.0) -> float:
    """The win rate at which a plan with this reward to risk makes nothing. ``cost_r`` is the spread
    and commission of one trade as a share of the risk: every win is that much smaller, every loss larger."""
    if rr <= 0:
        return 1.0
    return min(1.0, (1.0 + cost_r) / (1.0 + rr))


def expectancy(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Average result per trade. ``avg_loss`` is given as a positive size."""
    return win_rate * avg_win - (1.0 - win_rate) * abs(avg_loss)


def kelly(win_rate: float, rr: float) -> float:
    """The share of capital that grows it fastest when the win rate is known exactly. It never is."""
    if rr <= 0:
        return 0.0
    return win_rate - (1.0 - win_rate) / rr


def recovery(drawdown: float) -> float:
    """The gain needed to return to the peak after losing ``drawdown`` (0-1) of it: 50% down needs 100% up."""
    if drawdown >= 1:
        return math.inf
    return max(0.0, drawdown) / (1.0 - max(0.0, drawdown))


def drawdown(equity: list[float]) -> dict[str, float]:
    """The deepest fall from a peak in an equity curve, as a share of that peak."""
    peak = worst = 0.0
    peak_at = trough_at = worst_peak = 0.0
    for i, value in enumerate(equity):
        if i == 0 or value > peak:
            peak, peak_at = value, float(i)
        fall = (peak - value) / peak if peak > 0 else 0.0
        if fall > worst:
            worst, trough_at, worst_peak = fall, float(i), peak_at
    return {"max_drawdown": round(worst, 6), "peak_index": worst_peak, "trough_index": trough_at,
            "recovery_needed": round(recovery(worst), 6) if worst < 1 else math.inf}


def pip_value(pip_size: float, units: float, rate: float | None = None, *, account: str = "quote") -> float:
    """What one pip is worth for a position of ``units``.

    In the quote currency it is ``pip_size x units`` (EURUSD, 100,000 units: 10 USD). With the
    account in the base currency divide by the rate, which is the form ``(0.0001 / rate) x lot``.
    """
    value = pip_size * units
    if account == "base":
        if not rate or rate <= 0:
            raise ValueError("a positive rate is needed to convert to the base currency")
        return value / rate
    return value


def margin(units: float, price: float, leverage: float) -> float:
    """Money set aside to hold the position: its notional divided by the leverage."""
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    return units * price / leverage


def compound(capital: float, rate: float, periods: int) -> float:
    return capital * (1.0 + rate) ** periods


# ── how sure: distributions and tests ───────────────────────────────────────
def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_p(t: float, df: float, *, two_sided: bool = True) -> float:
    """The chance of a t this far from zero when the true mean is zero. One-sided: this far above zero."""
    if df <= 0 or not math.isfinite(t):
        return 1.0
    both = betainc(df / 2.0, 0.5, df / (df + t * t))
    if two_sided:
        return both
    return both / 2.0 if t > 0 else 1.0 - both / 2.0


def mean_test(values: list[float]) -> dict[str, Any]:
    """Is the average above zero by more than luck? Student's t, one-sided (we only care about profit)."""
    n = len(values)
    if n < 2:
        return {"n": n, "mean": values[0] if values else None, "t_stat": None, "p_value": None}
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return {"n": n, "mean": mean, "t_stat": None, "p_value": None}
    t = mean / math.sqrt(var / n)
    return {"n": n, "mean": mean, "sd": math.sqrt(var), "t_stat": round(t, 3), "p_value": round(student_p(t, n - 1, two_sided=False), 5)}


def mean_test_sums(n: int, total: float, total_sq: float) -> dict[str, Any]:
    """``mean_test`` from running sums, for a record that does not keep every trade."""
    if n < 2:
        return {"n": n, "t_stat": None, "p_value": None}
    mean = total / n
    var = max(0.0, (total_sq - n * mean * mean) / (n - 1))
    if var <= 0:
        return {"n": n, "t_stat": None, "p_value": None}
    t = mean / math.sqrt(var / n)
    return {"n": n, "t_stat": round(t, 3), "p_value": round(student_p(t, n - 1, two_sided=False), 5)}


def sidak(p: float | None, tried: int) -> float | None:
    """The p-value of the best of ``tried`` independent attempts: one in twenty looks good by luck alone."""
    if p is None:
        return None
    return round(1.0 - (1.0 - min(1.0, max(0.0, p))) ** max(1, tried), 5)


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """q-values: the false-discovery rate at which each p-value would be called a finding."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, p_values[i] * m / rank)
        q[i] = running
    return q


def beta_lower(wins: float, n: float, *, prior: tuple[float, float] = (2.0, 2.0), z: float = 1.0) -> float:
    """A win rate to plan on: the Beta posterior's mean minus ``z`` standard deviations.

    Two wins out of two is 100% on paper and about 47% here, which is what two trades are worth.
    """
    a, b = prior[0] + wins, prior[1] + max(0.0, n - wins)
    mean = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1.0)))
    return max(0.0, mean - z * sd)


def kelly_safe(wins: float, n: float, rr: float, *, share: float = 0.5, cap: float = 0.02) -> dict[str, Any]:
    """Kelly at face value, at half, and the one to use: half Kelly on the cautious win rate, capped.

    ``cap`` is the most any single trade may risk (2% of equity); Kelly only ever lowers it.
    """
    if n <= 0 or rr <= 0:
        return {"full": None, "half": None, "cautious_win_rate": None, "suggested_risk": 0.0}
    full = kelly(wins / n, rr)
    careful = beta_lower(wins, n)
    shrunk = share * kelly(careful, rr)
    return {"full": round(full, 4), "half": round(share * full, 4), "cautious_win_rate": round(careful, 4),
            "suggested_risk": round(max(0.0, min(cap, shrunk)), 4)}


# ── streaks and ruin ────────────────────────────────────────────────────────
def losing_streak_probability(win_rate: float, trades: int, streak: int) -> float:
    """The chance of at least one run of ``streak`` losses in a row somewhere in ``trades`` trades. Exact."""
    if streak <= 0:
        return 1.0
    if trades < streak:
        return 0.0
    q = min(1.0, max(0.0, 1.0 - win_rate))
    state = [1.0] + [0.0] * (streak - 1)     # state[k]: no run yet, currently k losses in a row
    for _ in range(trades):
        nxt = [0.0] * streak
        nxt[0] = sum(state) * (1.0 - q)
        for k in range(streak - 1):
            nxt[k + 1] = state[k] * q
        state = nxt                          # the mass that reached ``streak`` is dropped: it has happened
    return max(0.0, min(1.0, 1.0 - sum(state)))


def expected_longest_losing_streak(win_rate: float, trades: int) -> float:
    """About how long the worst run of losses gets: log(trades x win rate) / log(1 / loss rate)."""
    q = 1.0 - win_rate
    if trades <= 0 or q <= 0:
        return 0.0
    if q >= 1:
        return float(trades)
    return max(0.0, math.log(max(1.0, trades * win_rate)) / math.log(1.0 / q))


def risk_of_ruin(win_rate: float, rr: float, risk_per_trade: float, ruin_drawdown: float) -> float:
    """The chance the account ever falls ``ruin_drawdown`` (0-1) below where it starts.

    Each trade wins ``rr`` units with chance ``win_rate`` or loses one. For such a walk the chance of
    ever being N units down is ``theta ** N``, with theta the root in (0, 1) of
    ``p * theta**(rr + 1) - theta + (1 - p) = 0``. N is how many full losses reach the drawdown when
    each risks ``risk_per_trade`` of what is left. With no edge the answer is 1: it is only a matter of time.
    """
    p, q = win_rate, 1.0 - win_rate
    if not (0 < risk_per_trade < 1) or not (0 < ruin_drawdown < 1):
        raise ValueError("risk_per_trade and ruin_drawdown must be between 0 and 1")
    if p <= 0 or rr <= 0 or p * rr <= q:
        return 1.0
    if q <= 0:
        return 0.0
    units = math.log(1.0 - ruin_drawdown) / math.log(1.0 - risk_per_trade)

    def f(theta: float) -> float:
        return float(p * theta ** (rr + 1.0) - theta + q)

    lo, hi = 0.0, 1.0 - 1e-9            # f(0) = q > 0 and f is below zero just under 1 when there is an edge
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return max(0.0, min(1.0, float(((lo + hi) / 2.0) ** units)))


# ── a record and a plan, read with all of the above ─────────────────────────
def record(results_r: list[float], *, tried: int = 1) -> dict[str, Any]:
    """A list of closed trades in R, read honestly: the rates, what breaks even, the test, the streaks."""
    n = len(results_r)
    if not n:
        return {"trades": 0}
    wins = [r for r in results_r if r > 0]
    losses = [-r for r in results_r if r <= 0]
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = avg_win / avg_loss if avg_loss > 0 else None
    test = mean_test(results_r)
    curve, peak, deepest, run, longest = 0.0, 0.0, 0.0, 0, 0
    for r in results_r:
        curve += r
        peak = max(peak, curve)
        deepest = max(deepest, peak - curve)
        run = run + 1 if r <= 0 else 0
        longest = max(longest, run)
    out: dict[str, Any] = {
        "trades": n, "win_rate": round(win_rate, 4), "cautious_win_rate": round(beta_lower(len(wins), n), 4),
        "avg_win_r": round(avg_win, 3), "avg_loss_r": round(avg_loss, 3), "payoff": round(payoff, 3) if payoff else None,
        "breakeven_win_rate": round(breakeven_win_rate(payoff), 4) if payoff else None,
        "expectancy_r": round(sum(results_r) / n, 4),
        "profit_factor": round(sum(wins) / sum(losses), 3) if sum(losses) > 0 else None,
        "t_stat": test.get("t_stat"), "p_value": test.get("p_value"),
        "max_drawdown_r": round(deepest, 3), "longest_losing_streak": longest,
    }
    if tried > 1:
        out["p_value_adjusted"] = sidak(test.get("p_value"), tried)
    if payoff:
        out["kelly"] = kelly_safe(len(wins), n, payoff)
    p = out.get("p_value_adjusted", out["p_value"])
    out["verdict"] = ("terlalu sedikit trade untuk dinilai" if n < 20 or p is None else
                      "untung dan lolos uji (p < 0,05)" if out["expectancy_r"] > 0 and p < 0.05 else
                      "untung, tapi belum bisa dibedakan dari keberuntungan" if out["expectancy_r"] > 0 else
                      "rugi: expectancy negatif")
    return out


def plan_math(p_target: float | None, p_stop: float | None, stop_atr: float, target_atr: float, *, spread_atr: float = 0.0,
              timeout_r: float = 0.0) -> dict[str, Any]:
    """What a plan with these chances is worth, in R (one R is the stop distance).

    A plan has three ends: the target, the stop, or neither before the hold runs out
    (``timeout_r`` is what "neither" averaged). The spread is a cost on every end.
    """
    if stop_atr <= 0 or target_atr <= 0:
        return {}
    rr = target_atr / stop_atr
    cost_r = max(0.0, spread_atr) / stop_atr
    out: dict[str, Any] = {"rr": round(rr, 3), "cost_r": round(cost_r, 4),
                           "breakeven_win_rate": round(breakeven_win_rate(rr, cost_r), 4)}
    if p_target is None or p_stop is None:
        return out
    p_neither = max(0.0, 1.0 - p_target - p_stop)
    needed = min(1.0, (1.0 - p_neither) * (1.0 + cost_r) / (1.0 + rr))   # the target rate at which this plan makes nothing
    ev = p_target * rr - p_stop + p_neither * timeout_r - cost_r
    decided = p_target + p_stop
    out.update(p_target=round(p_target, 4), p_stop=round(p_stop, 4), p_neither=round(p_neither, 4),
               p_target_needed=round(needed, 4), edge=round(p_target - needed, 4), ev_r=round(ev, 4),
               kelly_half=round(max(0.0, 0.5 * kelly(p_target / decided, rr)), 4) if decided > 0 else 0.0)
    return out


__all__ = ["position_size", "risk_reward", "breakeven_win_rate", "expectancy", "kelly", "kelly_safe", "beta_lower", "recovery",
           "drawdown", "pip_value", "margin", "compound", "betainc", "student_p", "mean_test", "mean_test_sums", "sidak",
           "benjamini_hochberg", "losing_streak_probability", "expected_longest_losing_streak", "risk_of_ruin", "record",
           "plan_math"]
