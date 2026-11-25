"""The trading brain's learning math: recency weights, Polyak-averaged training, calibration, the entry
threshold, the walk-forward grade of the confluence, and the analyst's own calibration. Offline and seeded."""

from __future__ import annotations

import os

import numpy as np
import pytest

from addons.trading.quant import analyst, confluence, neural
from addons.trading.quant.memory import PLANS, SL, TIMEOUT, TP

os.environ.setdefault("TRADING_BRAIN_SIZE", "kecil")   # tests train the small networks; the machine's size is for real use


# ── neural ──────────────────────────────────────────────────────────────────
def test_recency_weights_favour_the_newest_rows_but_keep_the_oldest():
    w = neural.recency_weights(1000)
    assert w[-1] > w[0] and w.mean() == pytest.approx(1.0)
    assert w[0] >= neural.MIN_WEIGHT / w.max() * 0.9, "the past is not forgotten"
    assert w[-1] / w[int(1000 * (1 - neural.HALF_LIFE_SHARE))] == pytest.approx(2.0, rel=0.05), "half-life"
    assert len(neural.recency_weights(0)) == 0


def test_the_learning_rate_warms_up_and_then_decays():
    rates = [neural._lr_at(e, 100) for e in range(1, 101)]
    assert rates[0] < rates[neural.WARMUP_EPOCHS - 1] == pytest.approx(neural.LEARNING_RATE)
    assert rates[-1] == pytest.approx(0.1 * neural.LEARNING_RATE, rel=0.05)
    assert all(a >= b for a, b in zip(rates[neural.WARMUP_EPOCHS:], rates[neural.WARMUP_EPOCHS + 1:], strict=False))


def test_lower_bound_penalises_few_noisy_rows():
    rng = np.random.default_rng(0)
    many = rng.normal(0.3, 1.0, size=400)
    few = rng.normal(0.3, 1.0, size=12)
    assert neural.lower_bound(many) > neural.lower_bound(few)
    assert neural.lower_bound(np.array([])) == -np.inf
    assert neural.lower_bound(np.full(50, 0.2)) == pytest.approx(0.2)


def test_calibration_metrics_reward_honest_chances():
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, size=5000)
    honest = (rng.uniform(0, 1, size=5000) < p).astype(float)
    good = neural.calibration(p, honest)
    bad = neural.calibration(np.clip(p * 1.6, 0, 1), honest)      # over-confident
    assert good["ece"] < 0.05 < bad["ece"] and good["brier"] < bad["brier"]


def synthetic_rows(n: int = 1200, seed: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows where the first measurement really says whether the buy targets come first."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 12))
    y = np.full((n, len(PLANS)), TIMEOUT, dtype=int)
    res = np.zeros((n, len(PLANS)), dtype=np.float32)
    signal = x[:, 0] + 0.5 * rng.normal(size=n)
    for p, (side, sl, tp) in enumerate(PLANS):
        up = signal * side > 0.3
        down = signal * side < -0.3
        y[up, p], res[up, p] = TP, tp
        y[down, p], res[down, p] = SL, -sl
        res[~up & ~down, p] = 0.1 * side * signal[~up & ~down]
    return x, y, res


def test_the_ensemble_learns_a_planted_signal_and_its_chances_are_calibrated():
    x, y, res = synthetic_rows()
    cut, val_cut = 800, 1000
    names = [f"f{i}" for i in range(x.shape[1])]
    wave = neural.fit(x[:cut], y[:cut], res[:cut], x_val=x[cut:val_cut], y_val=y[cut:val_cut], result_val=res[cut:val_cut],
                      names=names, members=2, epochs=40)
    exam = neural.exam(wave, x[val_cut:], y[val_cut:], res[val_cut:], res[:cut])
    assert exam["skill"] > 0.2 and exam["ece"] < 0.12 and exam["avg_r_atr"] > 0
    assert exam["t_stat"] is not None and exam["avg_r_lower"] is not None
    assert 0.5 <= wave.temperature <= 8.0 and wave.threshold_profitable
    arrays = neural.to_arrays(wave)
    again = neural.from_arrays(arrays, names, exam)
    np.testing.assert_allclose(again.proba_members(x[-5:]), wave.proba_members(x[-5:]), atol=1e-4)


def test_the_threshold_is_set_where_the_total_profit_bound_is_highest():
    class Wave:
        def __init__(self, edge: np.ndarray) -> None:
            self.edge = edge

    rng = np.random.default_rng(5)
    n = 400
    edge = rng.uniform(0, 2.5, size=n)
    result = np.zeros((n, len(PLANS)), dtype=np.float32)
    result[:, 0] = np.where(edge > 0.4, 0.4 + rng.normal(0, 0.3, size=n), -0.5 + rng.normal(0, 0.3, size=n))
    wave = Wave(edge)
    fake_edges = lambda w, x: (np.zeros(n, dtype=int), w.edge)  # noqa: E731
    original = neural.edges
    neural.edges = fake_edges  # type: ignore[assignment]
    try:
        t, profitable = neural.choose_threshold(wave, np.zeros((n, 1)), result)  # type: ignore[arg-type]
    finally:
        neural.edges = original  # type: ignore[assignment]
    assert profitable and 0.3 <= t <= 0.6, t


# ── confluence ──────────────────────────────────────────────────────────────
def test_walk_forward_predicts_each_block_from_the_blocks_before_it_with_a_purge():
    rng = np.random.default_rng(2)
    n = 800
    x = np.column_stack([np.ones(n), rng.normal(size=(n, 3))])
    y = (x[:, 1] + 0.3 * rng.normal(size=n) > 0).astype(float)
    ridge, p, rows = confluence.walk_forward(x, y, gap=5, folds=4)
    assert ridge in confluence.RIDGES and len(p) == len(rows) == n - n // 4
    assert rows[0] == n // 4, "the first block only teaches"
    assert ((p > 0.5) == (y[rows] > 0.5)).mean() > 0.8
    # a label that only the purged rows could reveal does not leak into the grade
    leak = rng.normal(size=n)
    y2 = (np.roll(leak, -5) > 0).astype(float)             # the outcome 5 rows ahead
    x2 = np.column_stack([np.ones(n), leak])
    _, p2, rows2 = confluence.walk_forward(x2, y2, gap=5, folds=4)
    assert abs(((p2 > 0.5) == (y2[rows2] > 0.5)).mean() - 0.5) < 0.08


def test_the_confluence_reports_its_walk_forward_grade():
    from tests.test_confluence import trending_bars

    r = confluence.analyse(trending_bars(), horizon=3)
    assert r["ok"] and r["folds"] == confluence.FOLDS and r["purge_bars"] == 3 and r["ridge"] in confluence.RIDGES
    assert 0 <= r["test_brier"] <= 1 and r["test_samples"] > r["train_samples"] * 0.5


# ── the analyst's own calibration ───────────────────────────────────────────
def plan(confidence: float, status: str) -> dict:
    return {"status": status, "confidence": confidence, "side": "buy", "result_r": 1.0 if status == "target" else -1.0,
            "session": "london"}


def test_the_analyst_sees_when_its_confidence_is_upside_down():
    sure_losers = [plan(0.85, "stop") for _ in range(4)] + [plan(0.8, "target")]
    unsure_winners = [plan(0.4, "target") for _ in range(4)]
    cal = analyst.calibration(sure_losers + unsure_winners)
    assert cal["plans"] == 9 and "terbalik" in cal["verdict"]
    bands = {b["confidence"]: b for b in cal["bands"]}
    assert bands[">=0.7"]["target_rate"] == pytest.approx(0.2) and bands["<0.5"]["target_rate"] == 1.0
    honest = analyst.calibration([plan(0.8, "target")] * 4 + [plan(0.3, "stop")] * 4)
    assert honest["brier"] < 0.1 and honest["verdict"] == "cukup kalibrasi"
    assert analyst.calibration([])["plans"] == 0


def test_the_record_shrinks_a_short_streak_toward_even():
    s = analyst.summary([plan(0.7, "target"), plan(0.7, "target")])
    assert s["target_rate"] == 1.0 and s["target_rate_shrunk"] == pytest.approx(4 / 6, abs=1e-3)
    assert "calibration" in s and s["calibration"]["plans"] == 2
