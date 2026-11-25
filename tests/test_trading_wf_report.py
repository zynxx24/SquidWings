"""The walk-forward report on daily history: honest statistics, no peeking, and a verdict that can say no."""

from __future__ import annotations

import math

import numpy as np
import pytest

from addons.trading.quant import daily, forecast, wf_report
from tests.test_trading_daily import walk

pytestmark = pytest.mark.enterprise


def test_binomial_tail_matches_hand_values():
    assert wf_report.binomial_tail(8, 10, 0.5) == pytest.approx(56 / 1024)   # C(10,8)+C(10,9)+C(10,10) = 45+10+1
    assert wf_report.binomial_tail(0, 10, 0.5) == 1.0
    assert wf_report.binomial_tail(10, 10, 0.5) == pytest.approx(1 / 1024)
    assert 0 < wf_report.binomial_tail(1100, 2000, 0.5) < 1e-3               # large n stays finite


def test_wilson_interval_brackets_the_rate_and_narrows_with_n():
    lo, hi = wf_report.wilson(55, 100)
    assert lo < 0.55 < hi
    lo2, hi2 = wf_report.wilson(5500, 10000)
    assert (hi2 - lo2) < (hi - lo)
    assert wf_report.wilson(0, 0) == (0.0, 1.0)


def test_sharpe_and_drawdown_of_known_series():
    steady = np.full(252, 0.001)
    assert wf_report.sharpe(steady) == 0.0                                   # no variance: no ratio
    swing = np.array([0.02, -0.01] * 126)
    assert wf_report.sharpe(swing) > 0
    assert wf_report.max_drawdown(np.array([0.1, -0.5, 0.2])) == pytest.approx(-0.5)
    assert wf_report.max_drawdown(np.array([0.01, 0.02])) == 0.0


def test_bootstrap_interval_contains_the_estimate_and_is_reproducible():
    rng = np.random.default_rng(3)
    returns = rng.normal(0.001, 0.01, 500)
    lo, hi = wf_report._block_bootstrap_sharpe(returns, np.random.default_rng(0))
    assert lo < wf_report.sharpe(returns) < hi
    assert (lo, hi) == wf_report._block_bootstrap_sharpe(returns, np.random.default_rng(0))
    assert math.isnan(wf_report._block_bootstrap_sharpe(returns[:5], np.random.default_rng(0))[0])


def test_permutation_test_is_small_for_real_timing_and_large_for_none():
    rng = np.random.default_rng(1)
    day_return = rng.normal(0, 0.01, 400)
    perfect = np.sign(day_return)
    assert wf_report._permutation_p(perfect, day_return, np.random.default_rng(0)) < 0.01
    noise = rng.choice([-1.0, 1.0], 400)
    assert wf_report._permutation_p(noise, day_return, np.random.default_rng(0)) > 0.05


def test_costs_are_charged_on_every_change_of_position():
    position = np.array([1.0, 1.0, -1.0, 0.0])
    out = wf_report._trade(position, np.zeros(4), cost_bps=10.0)
    # 0->1 (1), 1->1 (0), 1->-1 (2), -1->0 (1): four units of turnover at 10 bps each
    assert out["net"].sum() == pytest.approx(-4 * 10 / 1e4)
    assert out["turnover"] == pytest.approx(1.0) and out["exposure"] == pytest.approx(0.75)


def test_too_little_history_is_reported_not_crashed():
    report = wf_report.evaluate(walk(200))
    assert report["ok"] is False and "need at least" in report["error"]
    assert "Not run" in wf_report.render(report)


def test_predictions_never_use_days_after_them():
    bars = walk(1200, seed=4, ar=0.3)
    last = len(bars) - 1
    before = forecast.oos_predictions(daily.indicators(bars), last, 1)
    cut = 1000
    changed = [dict(b) for b in bars]
    for b in changed[cut:]:
        b["o"], b["h"], b["l"], b["c"] = b["o"] * 3, b["h"] * 4, b["l"] * 2, b["c"] * 3
    after = forecast.oos_predictions(daily.indicators(changed), last, 1)
    assert before is not None and after is not None
    safe = before["idx"] < cut - 5
    assert safe.sum() > 100
    assert np.array_equal(before["idx"][safe], after["idx"][after["idx"] < cut - 5])
    assert np.allclose(before["p"][safe], after["p"][after["idx"] < cut - 5])


def test_a_random_walk_has_no_edge():
    report = wf_report.evaluate(walk(900, seed=7), horizons=(1,))
    assert report["ok"] and report["verdict"]["edge"] is False
    assert "no edge" in report["verdict"]["text"]
    assert report["verdict"]["reasons"]


def test_a_strong_momentum_market_is_found():
    bars = walk(900, seed=2, ar=0.45)
    report = wf_report.evaluate(bars, horizons=(1,))
    cls = report["horizons"]["1d"]["classification"]
    assert cls["edge"] and cls["hit"] > cls["constant_guess"]
    trade = report["horizons"]["1d"]["trading"]
    assert trade["network_long_short"]["sharpe"] > trade["buy_and_hold"]["sharpe"]
    assert trade["network_long_short"]["sharpe_ci95"][0] > 0


def test_the_report_has_every_section_and_the_numbers_agree():
    report = wf_report.evaluate(walk(900, seed=5), horizons=(1,))
    entry = report["horizons"]["1d"]
    cls = entry["classification"]
    assert sum(f["n"] for f in cls["folds"]) == cls["n"] == entry["trading"]["days"]
    assert sum(c["n"] for c in cls["calibration"]) == cls["n"]
    text = wf_report.render(report, "XAUUSD")
    for needle in ("## Verdict", "## Horizon 1d", "network long/short", "buy and hold", "20-day momentum", "Brier", "refit window"):
        assert needle in text
    assert "promise" in text                                  # the closing caution is part of the report


def test_write_and_main_save_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "load", lambda symbol, folder=None: walk(900, seed=6))
    assert wf_report.main(["--symbol", "TESTXX", "--horizons", "1", "--out", str(tmp_path)]) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert any(n.startswith("walkforward-TESTXX-D1-") and n.endswith(".md") for n in names)
    assert any(n.endswith(".json") for n in names)


def test_main_without_a_d1_file_says_how_to_get_one(monkeypatch, capsys):
    monkeypatch.setattr(daily, "load", lambda symbol, folder=None: [])
    assert wf_report.main(["--symbol", "NOPE"]) == 1
    assert "History Center" in capsys.readouterr().out


def test_the_report_goes_to_an_ignored_folder(monkeypatch):
    import subprocess

    from brain.config import paths

    monkeypatch.delenv("SQUIDWINGS_DATA_DIR", raising=False)   # the default place, inside the repo
    folder = paths.report_dir("trading")
    assert folder.is_relative_to(paths.ROOT / "data")
    out = subprocess.run(["git", "check-ignore", str(folder / "x.md")], cwd=paths.ROOT, capture_output=True, text=True)
    assert out.returncode == 0, "report files must never be committed"
