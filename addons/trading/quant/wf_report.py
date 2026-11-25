"""A walk-forward report on the daily (D1) history: does the brain's network beat luck, and by how much after costs?

Paper only. It reads the saved D1 file, never touches the terminal, an account or an EA.

Everything below is measured on predictions made out of sample: every day's P(up) comes from a network trained only on
days whose outcome was already known then (``forecast.oos_predictions``). For each horizon the report says:

* the hit rate against the best constant guess, with a 99% Wilson interval and an exact one-sided binomial p-value;
* the Brier score against always predicting the base rate, and the same hit rate fold by fold, so one lucky stretch
  cannot carry the average;
* for the one-day horizon, the P&L of trading the signal (long above ``0.5 + margin``, short below ``0.5 - margin``,
  flat between) with a cost on every change of position, against buy-and-hold over the same days and against two
  rules anyone could write (yesterday's direction, the 20-day momentum). Sharpe comes with a block-bootstrap interval,
  and a permutation test shuffles the signals over the days to ask how often luck alone does as well.

``verdict`` is "edge" only when the hit rate, the Sharpe interval and the permutation test all agree. Two horizons are
looked at, so one pass is weaker evidence than it looks; the report says so. Past results do not promise future ones.

Run: ``python -m addons.trading.quant.wf_report --symbol XAUUSD``; the files go to ``data/reports/trading/`` (ignored by git).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from addons.trading.quant import daily, forecast
from brain.config.paths import report_dir

TRADING_DAYS = 252
COST_BPS = 2.0            # per side, in basis points of price: about a 40-cent spread on gold near 4000, rounded up
MARGIN = 0.02             # how far P(up) must be from 0.5 before the signal trades
BOOTSTRAP = 1000
BLOCK = 10                # days per bootstrap block, so the autocorrelation of returns survives
PERMUTATIONS = 1000
Z99 = 2.58
REPORT_DIR = report_dir("trading")


# ── statistics ──────────────────────────────────────────────────────────────
def wilson(k: float, n: int, z: float = Z99) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def binomial_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), exact, summed in log space so n in the thousands is safe."""
    if k <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    logs = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p) + (n - i) * math.log1p(-p)
            for i in range(k, n + 1)]
    top = max(logs)
    return min(1.0, math.exp(top) * sum(math.exp(v - top) for v in logs))


FLAT = 1e-12   # a daily-return spread below this is float noise, not risk: the ratio is undefined, not 1e16


def sharpe(returns: np.ndarray) -> float:
    sd = float(np.std(returns))
    return float(np.mean(returns) / sd * math.sqrt(TRADING_DAYS)) if sd > FLAT else 0.0


def max_drawdown(returns: np.ndarray) -> float:
    equity = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity / peak - 1.0)) if len(equity) else 0.0


def _block_bootstrap_sharpe(returns: np.ndarray, rng: np.random.Generator, draws: int = BOOTSTRAP, block: int = BLOCK) -> tuple[float, float]:
    """95% interval of the Sharpe ratio from resampling blocks of consecutive days."""
    n = len(returns)
    if n < 2 * block:
        return float("nan"), float("nan")
    blocks = math.ceil(n / block)
    starts = rng.integers(0, n - block + 1, size=(draws, blocks))
    index = (starts[:, :, None] + np.arange(block)).reshape(draws, -1)[:, :n]
    sample = returns[index]
    sd = sample.std(axis=1)
    values = np.where(sd > FLAT, sample.mean(axis=1) / np.where(sd > FLAT, sd, 1.0) * math.sqrt(TRADING_DAYS), 0.0)
    lo, hi = np.percentile(values, [2.5, 97.5])
    return float(lo), float(hi)


def _permutation_p(position: np.ndarray, day_return: np.ndarray, rng: np.random.Generator, draws: int = PERMUTATIONS) -> float:
    """How often the same positions, dealt to random days, earn at least the mean daily return actually earned (costless)."""
    actual = float(np.mean(position * day_return))
    pos = position.copy()
    beat = 0
    for _ in range(draws):
        rng.shuffle(pos)
        if float(np.mean(pos * day_return)) >= actual:
            beat += 1
    return (beat + 1) / (draws + 1)


def _trade(position: np.ndarray, day_return: np.ndarray, cost_bps: float) -> dict[str, Any]:
    """Daily returns of holding ``position`` through each day's return, paying the cost on every change of position."""
    prev = np.concatenate(([0.0], position[:-1]))
    net = position * day_return - np.abs(position - prev) * cost_bps / 1e4
    return {"net": net, "turnover": float(np.sum(np.abs(position - prev))) / max(1, len(position)), "exposure": float(np.mean(position != 0))}


def _summary(net: np.ndarray, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    total = float(np.prod(1.0 + net) - 1.0)
    years = len(net) / TRADING_DAYS
    return {"total_return": round(total, 4), "annual_return": round((1 + total) ** (1 / years) - 1, 4) if years > 0 and total > -1 else None,
            "sharpe": round(sharpe(net), 2), "max_drawdown": round(max_drawdown(net), 4),
            "hit_days": round(float(np.mean(net > 0)), 3), **(extra or {})}


# ── the report ──────────────────────────────────────────────────────────────
def _day(ind: dict[str, np.ndarray], i: int) -> str:
    return dt.datetime.fromtimestamp(float(ind["t"][i]), dt.UTC).strftime("%Y-%m-%d")


def _classification(pred: dict[str, np.ndarray], ind: dict[str, np.ndarray]) -> dict[str, Any]:
    p, y, fold, idx = pred["p"], pred["y"], pred["fold"], pred["idx"]
    n = len(p)
    hit = (p > 0.5) == (y > 0.5)
    up = float(np.mean(y))
    naive = max(up, 1 - up)
    wins = int(hit.sum())
    lo, hi = wilson(wins, n)
    base_brier = up * (1 - up)
    brier = float(np.mean((p - y) ** 2))
    folds = []
    for f in np.unique(fold):
        m = fold == f
        f_up = float(np.mean(y[m]))
        folds.append({"from": _day(ind, int(idx[m][0])), "to": _day(ind, int(idx[m][-1])), "n": int(m.sum()),
                      "hit": round(float(np.mean(hit[m])), 3), "constant": round(max(f_up, 1 - f_up), 3)})
    calibration = []
    for name, lower, upper in (("p<0.45", 0.0, 0.45), ("0.45-0.55", 0.45, 0.55), ("p>0.55", 0.55, 1.01)):
        m = (p >= lower) & (p < upper)
        calibration.append({"bucket": name, "n": int(m.sum()), "up_share": round(float(np.mean(y[m])), 3) if m.any() else None})
    return {"n": n, "hit": round(wins / n, 4), "hit_ci99": [round(lo, 4), round(hi, 4)], "constant_guess": round(naive, 4),
            "binomial_p": round(binomial_tail(wins, n, naive), 4), "brier": round(brier, 4), "brier_base": round(base_brier, 4),
            "brier_skill": round(1 - brier / base_brier, 4) if base_brier > 0 else 0.0, "folds": folds,
            "folds_beating_constant": sum(1 for f in folds if f["hit"] > f["constant"]), "calibration": calibration,
            "edge": bool(lo > naive)}


def _trading(pred: dict[str, np.ndarray], ind: dict[str, np.ndarray], *, cost_bps: float, margin: float,
             seed: int) -> dict[str, Any]:
    """One-day horizon only: position set at the close of day i earns the move from close i to close i+1."""
    idx, p = pred["idx"], pred["p"]
    close = ind["c"]
    day_return = close[idx + 1] / close[idx] - 1.0
    signal = np.where(p > 0.5 + margin, 1.0, np.where(p < 0.5 - margin, -1.0, 0.0))
    rng = np.random.default_rng(seed)

    def row(position: np.ndarray, test_luck: bool = False) -> dict[str, Any]:
        trade = _trade(position, day_return, cost_bps)
        out = _summary(trade["net"], {"exposure": round(trade["exposure"], 3), "turnover_per_day": round(trade["turnover"], 3)})
        if test_luck:
            lo, hi = _block_bootstrap_sharpe(trade["net"], rng)
            out.update(sharpe_ci95=[round(lo, 2), round(hi, 2)], permutation_p=round(_permutation_p(position, day_return, rng), 4))
        return out

    # the rules anyone could write, on the very same days
    yesterday = np.sign(close[idx] / close[idx - 1] - 1.0)
    momentum = np.sign(close[idx] / close[idx - 20] - 1.0)
    return {"days": len(idx), "from": _day(ind, int(idx[0])), "to": _day(ind, int(idx[-1]) + 1), "cost_bps_per_side": cost_bps, "margin": margin,
            "network_long_short": row(signal, test_luck=True),
            "network_long_only": row(np.maximum(signal, 0.0)),
            "buy_and_hold": row(np.ones(len(idx))),
            "rule_yesterday": row(yesterday),
            "rule_momentum_20d": row(momentum)}


def evaluate(bars: list[dict[str, float]], *, horizons: tuple[int, ...] = forecast.HORIZONS, cost_bps: float = COST_BPS,
             margin: float = MARGIN, seed: int = 0) -> dict[str, Any]:
    """The whole report as data. ``bars`` are closed daily candles, oldest first."""
    if len(bars) < forecast.FIRST_TRAIN + forecast.STEP + 60:
        return {"ok": False, "error": f"need at least {forecast.FIRST_TRAIN + forecast.STEP + 60} daily bars, got {len(bars)}"}
    ind = daily.indicators(bars)
    last = len(bars) - 1
    out: dict[str, Any] = {"ok": True, "days": len(bars), "from": _day(ind, 0), "to": _day(ind, last),
                           "refit_every_days": forecast.STEP, "first_train_days": forecast.FIRST_TRAIN, "horizons": {}}
    for h in horizons:
        pred = forecast.oos_predictions(ind, last, h)
        if pred is None:
            out["horizons"][f"{h}d"] = {"error": "too little history for out-of-sample predictions"}
            continue
        entry: dict[str, Any] = {"classification": _classification(pred, ind)}
        if h == 1:
            entry["trading"] = _trading(pred, ind, cost_bps=cost_bps, margin=margin, seed=seed)
        out["horizons"][f"{h}d"] = entry
    out["verdict"] = _verdict(out)
    return out


def _verdict(report: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    passed = 0
    looked = 0
    for name, entry in report["horizons"].items():
        cls = entry.get("classification")
        if not cls:
            continue
        looked += 1
        if not cls["edge"]:
            reasons.append(f"{name}: hit {cls['hit']:.1%} does not clear the constant guess {cls['constant_guess']:.1%} (99% interval {cls['hit_ci99'][0]:.1%}..{cls['hit_ci99'][1]:.1%})")
            continue
        trade = entry.get("trading")
        if trade is None:
            passed += 1
            continue
        net = trade["network_long_short"]
        lo = net["sharpe_ci95"][0]
        if not lo > 0:
            reasons.append(f"{name}: hit rate clears luck but the net Sharpe interval {net['sharpe_ci95']} includes 0 after {trade['cost_bps_per_side']} bps costs")
        elif net["permutation_p"] >= 0.05:
            reasons.append(f"{name}: shuffling the signals over the days does as well {net['permutation_p']:.0%} of the time")
        else:
            passed += 1
    if looked == 0:
        return {"edge": False, "text": "no horizon could be evaluated", "reasons": ["too little history"]}
    if passed:
        return {"edge": True, "reasons": reasons,
                "text": f"{passed} of {looked} horizons passed every test. {looked} were tried, so read this as a lead to confirm on new data, not as proof."}
    return {"edge": False, "reasons": reasons, "text": "no edge: nothing here beat luck after costs. The daily history gives context, not a direction."}


# ── rendering ───────────────────────────────────────────────────────────────
def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def render(report: dict[str, Any], symbol: str = "XAUUSD") -> str:
    if not report.get("ok"):
        return f"# Walk-forward {symbol} D1\n\nNot run: {report.get('error')}\n"
    v = report["verdict"]
    lines = [f"# Walk-forward report, {symbol} D1", "",
             f"{report['days']} daily bars, {report['from']} to {report['to']}. Network refit every {report['refit_every_days']} days "
             f"after the first {report['first_train_days']}; each prediction uses only days whose outcome was already known. Paper only.", "",
             f"## Verdict: {'EDGE' if v['edge'] else 'NO EDGE'}", "", v["text"], ""]
    lines += [f"- {r}" for r in v["reasons"]]
    for name, entry in report["horizons"].items():
        lines += ["", f"## Horizon {name}", ""]
        cls = entry.get("classification")
        if not cls:
            lines.append(entry.get("error", "no result"))
            continue
        lines += [f"Out-of-sample days: {cls['n']}. Hit rate **{_pct(cls['hit'])}** (99% interval {_pct(cls['hit_ci99'][0])} to {_pct(cls['hit_ci99'][1])}) "
                  f"against a constant guess of {_pct(cls['constant_guess'])}; exact binomial p = {cls['binomial_p']}.",
                  f"Brier {cls['brier']} against {cls['brier_base']} for always saying the base rate (skill {cls['brier_skill']:+.1%}). "
                  f"The network beat the constant guess in {cls['folds_beating_constant']} of {len(cls['folds'])} refits.", "",
                  "| refit window | days | hit | constant |", "|---|---:|---:|---:|"]
        lines += [f"| {f['from']} to {f['to']} | {f['n']} | {_pct(f['hit'])} | {_pct(f['constant'])} |" for f in cls["folds"]]
        lines += ["", "| P(up) | days | went up |", "|---|---:|---:|"]
        lines += [f"| {c['bucket']} | {c['n']} | {_pct(c['up_share'])} |" for c in cls["calibration"]]
        trade = entry.get("trading")
        if trade:
            lines += ["", f"### Trading the signal, {trade['from']} to {trade['to']} ({trade['days']} days)", "",
                      f"Long above P(up) {0.5 + trade['margin']:.2f}, short below {0.5 - trade['margin']:.2f}, flat between. "
                      f"{trade['cost_bps_per_side']} bps paid on every change of position. Entry at the close, exit at the next close.", "",
                      "| strategy | total | per year | Sharpe | max drawdown | days up | in market | position changes/day |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
            for key, label in (("network_long_short", "network long/short"), ("network_long_only", "network long only"), ("buy_and_hold", "buy and hold"),
                               ("rule_yesterday", "rule: yesterday's direction"), ("rule_momentum_20d", "rule: 20-day momentum")):
                r = trade[key]
                lines.append(f"| {label} | {_pct(r['total_return'])} | {_pct(r['annual_return'])} | {r['sharpe']} | {_pct(r['max_drawdown'])} | "
                             f"{_pct(r['hit_days'])} | {_pct(r['exposure'])} | {r['turnover_per_day']} |")
            net = trade["network_long_short"]
            lines += ["", f"Network long/short Sharpe 95% interval (block bootstrap): {net['sharpe_ci95'][0]} to {net['sharpe_ci95'][1]}. "
                      f"Shuffling the same positions over the days earns as much {net['permutation_p']:.1%} of the time."]
    lines += ["", "---", "A hit rate near 50% on gold is what luck produces. This report is a test that could have failed; "
              "it is not a promise about the next day."]
    return "\n".join(lines) + "\n"


def write(report: dict[str, Any], symbol: str, directory: Path | None = None) -> tuple[Path, Path]:
    folder = directory or REPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    md, js = folder / f"walkforward-{symbol}-D1-{stamp}.md", folder / f"walkforward-{symbol}-D1-{stamp}.json"
    md.write_text(render(report, symbol), encoding="utf-8")
    js.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward report on the saved D1 history (paper only).")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--horizons", default=",".join(str(h) for h in forecast.HORIZONS), help="days ahead, comma separated")
    parser.add_argument("--cost-bps", type=float, default=COST_BPS)
    parser.add_argument("--margin", type=float, default=MARGIN)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None, help="folder for the .md and .json (default data/reports/trading)")
    args = parser.parse_args(argv)
    bars = daily.load(args.symbol)
    if not bars:
        print(f"No D1 file for {args.symbol} in addons/trading/data/bars (export it from the MT4 History Center).")
        return 1
    report = evaluate(bars, horizons=tuple(int(h) for h in args.horizons.split(",") if h.strip()), cost_bps=args.cost_bps,
                      margin=args.margin, seed=args.seed)
    print(render(report, args.symbol))
    if report.get("ok"):
        md, js = write(report, args.symbol, args.out)
        print(f"Saved {md} and {js}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
