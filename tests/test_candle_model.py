"""The candle model in the trading system: its scenarios, the paper simulation's limit orders at A and B, the LLM block."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import torch

from trading.quant import candle_lm, candle_view, papersim
from trading.quant.papersim import CANDLE, PaperSim


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0 + 1200

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Mt4Like:
    """M5 bars only, the price set by the test, and no order method at all."""

    def __init__(self, clock: Clock) -> None:
        self.clock, self.mid = clock, 100.0

    def tick(self, symbol: str) -> dict[str, Any]:
        return {"bid": self.mid - 0.05, "ask": self.mid + 0.05}

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        return {"digits": 2}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        if timeframe != "M5":
            raise RuntimeError("M5 only")
        end = int(self.clock.t) // 300 * 300
        return [{"t": end - i * 300, "o": 100.0 + (i % 7) * 0.3, "h": 101.0 + (i % 7) * 0.3, "l": 99.0 + (i % 7) * 0.3,
                 "c": 100.0 + ((i + 3) % 7) * 0.3, "v": 5} for i in range(count, 0, -1)]


def forecast(p_up: float, *, close: float = 100.0, atr: float = 1.0, t: float = 1_800_000_000.0) -> dict[str, Any]:
    """A made-up forecast: candle k's close band widens with k; the high and low bands sit around it."""
    q = [[[-0.3 * (k + 1) ** 0.5, 0.0, 0.3 * (k + 1) ** 0.5], [0.0, 0.4, 0.4 + 0.5 * (k + 1) ** 0.5],
          [-0.4 - 0.5 * (k + 1) ** 0.5, -0.4, 0.0]] for k in range(12)]
    return {"close": close, "atr_price": atr, "last_bar_time": t, "quantiles_atr": q, "exam": {"edge": False},
            "candles": [{"candle": k + 1, "p_up": p_up} for k in range(12)]}


def test_flat_model_gives_limit_orders_at_the_band_edges_and_a_leaning_one_adds_a_trend_entry():
    flat = candle_view.scenarios(forecast(0.5))
    kinds = {s["kind"]: s for s in flat}
    assert set(kinds) == {"reversal_buy", "reversal_sell"}
    buy, sell = kinds["reversal_buy"], kinds["reversal_sell"]
    assert buy["order"] == "limit" and buy["entry"] < 100 < sell["entry"]
    assert buy["stop"] < buy["entry"] < buy["target"] and sell["target"] < sell["entry"] < sell["stop"]
    assert buy["entry"] == pytest.approx(100 * np.exp(-(0.4 + 0.5 * 12 ** 0.5) / 100), abs=1e-3)   # the 10% low over the hour
    assert "lemah" in buy["strength"]
    up = {s["kind"]: s for s in candle_view.scenarios(forecast(0.6), price=100.2)}
    assert up["trend"]["side"] == "buy" and up["trend"]["order"] == "market" and up["trend"]["entry"] == 100.2
    down = {s["kind"]: s for s in candle_view.scenarios(forecast(0.4))}
    assert down["trend"]["side"] == "sell" and down["trend"]["stop"] > 100 > down["trend"]["target"]


def make_sim(monkeypatch: pytest.MonkeyPatch, fc: dict[str, Any]) -> tuple[PaperSim, Clock, Mt4Like]:
    clock = Clock()
    feed = Mt4Like(clock)

    def fake_view(symbol: str, bars: Any = None, *, price: float | None = None) -> dict[str, Any]:
        return {"ok": True, **fc, "scenarios": candle_view.scenarios(fc, price)}

    monkeypatch.setattr(candle_view, "view", fake_view)
    sim = PaperSim(feed, "XAUUSD", timeframe="H1", hold_bars=4, max_minutes=24 * 60, clock=clock, sleep=clock.sleep,
                   seed=1, candle=True)
    return sim, clock, feed


def test_the_simulation_waits_at_a_and_b_fills_one_and_cancels_the_other(monkeypatch):
    sim, clock, feed = make_sim(monkeypatch, forecast(0.5, t=1_800_000_000.0 + 1200))
    assert CANDLE in sim.candidates
    sim.step()
    assert sim.cycles == 1 and len(sim.pending) == 2 and not any(p.rule == CANDLE for p in sim.open)
    buy = next(p for p in sim.pending if p.side > 0)
    report = sim.report()
    assert {o["side"] for o in report["pending"]} == {"buy", "sell"} and report["candle"]["ok"]
    feed.mid = buy.entry + 1.0                       # not there yet
    clock.t += 5
    sim.step()
    assert len(sim.pending) == 2
    feed.mid = buy.entry - 0.1                       # the ask comes down to A
    clock.t += 5
    sim.step()
    position = next(p for p in sim.open if p.rule == CANDLE)
    assert position.side == 1 and position.entry == pytest.approx(feed.mid + 0.05)
    assert position.stop == buy.stop and position.target == buy.target and position.risk
    assert not sim.pending, "the sell at B was cancelled when A filled"
    feed.mid = buy.target + 0.2                      # up to the target
    clock.t += 5
    sim.step()
    trade = next(t for t in sim.trades if t["rule"] == CANDLE)
    assert trade["exit_reason"] == "target" and trade["win"] and trade["r"] > 1


def test_orders_expire_and_a_trend_scenario_enters_at_market(monkeypatch):
    sim, clock, feed = make_sim(monkeypatch, forecast(0.5, t=1_800_000_000.0 + 1200))
    sim.step()
    assert len(sim.pending) == 2
    clock.t = sim.pending[0].expires + 1
    sim.step()
    assert not sim.pending and not any(p.rule == CANDLE for p in sim.open)

    sim2, _, feed2 = make_sim(monkeypatch, forecast(0.62, t=1_800_000_000.0 + 1200))
    sim2.step()
    position = next(p for p in sim2.open if p.rule == CANDLE)
    assert position.side == 1 and position.entry == pytest.approx(feed2.mid + 0.05) and not sim2.pending
    assert position.stop < position.entry < position.target


def test_the_candle_rule_needs_the_intraday_style():
    with pytest.raises(ValueError):
        PaperSim(object(), "XAUUSD", candle=True)
    assert papersim.LABELS[CANDLE]


def test_the_llm_block_reads_the_exam_path_band_and_scenarios():
    fc = {**forecast(0.5), "ok": True,
          "candles": [{"candle": k + 1, "close": 100.0, "close_p10_p90": [99.0, 101.0], "p_up": 0.5, "low_p10": 98.0 - k,
                       "high_p90": 102.0 + k} for k in range(12)],
          "exam": {"score": 0.998, "edge": False},
          "exam_per_candle": [{"candle": k + 1, "closeness": 60.0, "closeness_no_brain": 59.9, "direction_hit": 50.0,
                               "direction_majority": 52.0, "band_80_hit": 79.0} for k in range(12)]}
    fc["scenarios"] = candle_view.scenarios(fc)
    block = candle_view.compact(fc)
    assert block["exam"]["edge"] is False and block["exam"]["candle_1"]["band_80_hit"] == 79.0
    assert [p["candle"] for p in block["path"]] == [1, 3, 6, 12]
    assert block["band_hour"] == {"low_10": 87.0, "high_90": 113.0}
    assert {s["kind"] for s in block["scenarios"]} == {"reversal_buy", "reversal_sell"}
    assert candle_view.compact({"ok": False}) is None


def test_predict_works_on_bars_from_any_source():
    rng = np.random.default_rng(0)
    n = 900
    close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    t = 1_700_000_000 + np.arange(n) * 300.0
    bars = np.stack([t, close, close * 1.001, close * 0.999, close, np.full(n, 10.0)], axis=1)
    bars[1:, 1] = close[:-1]
    cfg = candle_lm.make_config("fast")
    data = candle_lm.Dataset(bars, cfg)
    model = candle_lm.CandleGPT(cfg, data.stats).eval()
    out = candle_lm.predict(model, {"instrument": "TEST", "epoch": 0, "config": {}}, bars)
    assert len(out["candles"]) == 12 and len(out["quantiles_atr"]) == 12
    first = out["candles"][0]
    assert first["close_p10_p90"][0] <= first["close"] <= first["close_p10_p90"][1]
    with pytest.raises(ValueError):
        candle_lm.predict(model, {}, bars[:100])
    with torch.no_grad():                               # an untrained model sits exactly on the brainless guess
        q = model(torch.from_numpy(data.x[-cfg.ctx:]).unsqueeze(0))[0, -1]
    assert torch.allclose(q[:, 1], torch.tensor(data.stats["median"], dtype=torch.float32))


# ── lab: settings, control, live changes ─────────────────────────────────────
def test_lab_settings_are_checked_and_round_trip(tmp_path):
    import pytest

    from trading.quant import candle_lm

    clean = candle_lm.clean_settings({"lr": "0.002", "batch": 32, "compile": None, "hours": 0, "d_model": 256, "heads": 8}, "base")
    assert clean == {"lr": 0.002, "batch": 32, "compile": None, "hours": 0.0, "d_model": 256, "heads": 8}
    with pytest.raises(ValueError):
        candle_lm.clean_settings({"lr": 5}, "base")                       # outside its limits
    with pytest.raises(ValueError):
        candle_lm.clean_settings({"nope": 1}, "base")                     # unknown key
    with pytest.raises(ValueError):
        candle_lm.clean_settings({"d_model": 200, "heads": 6}, "base")    # 200 / 6 is not whole
    candle_lm.write_settings(tmp_path, clean)
    values, mtime = candle_lm.read_settings(tmp_path)
    assert values == clean and mtime > 0
    assert candle_lm.read_settings(tmp_path / "none") == ({}, 0.0)


def test_lab_control_is_taken_once(tmp_path):
    import pytest

    from trading.quant import candle_lm

    candle_lm.send_control(tmp_path, "reset_lr")
    assert candle_lm._take_control(tmp_path) == "reset_lr"
    assert candle_lm._take_control(tmp_path) is None
    with pytest.raises(ValueError):
        candle_lm.send_control(tmp_path, "rm -rf")


def test_live_settings_reach_the_running_model():
    from torch import nn
    from torch.optim.swa_utils import AveragedModel

    from trading.quant import candle_lm

    cfg = candle_lm.make_config("fast")
    stats = {"median": [0.0] * (cfg.horizon * 3), "scale": [1.0] * (cfg.horizon * 3)}
    model = candle_lm.CandleGPT(cfg, stats)
    ema = AveragedModel(model, avg_fn=candle_lm._ema_fn(cfg.ema_decay))
    changed = candle_lm._apply_live(cfg, model, ema, {"lr": 0.0005, "dropout": 0.3, "batch": 16, "ema_decay": 0.995, "ctx": 999})
    assert cfg.lr == 0.0005 and cfg.batch == 16 and cfg.ctx != 999      # the shape never changes while running
    assert len(changed) == 4
    assert all(m.p == 0.3 for m in model.modules() if isinstance(m, nn.Dropout))
    assert all(b.p == 0.3 for b in model.blocks)
    assert candle_lm._apply_live(cfg, model, ema, {"lr": 0.0005}) == []


def test_lab_status_and_history_read_a_run(tmp_path, monkeypatch):
    import json

    from trading.quant import candle_lm, candle_view

    monkeypatch.setattr(candle_lm, "RUNS", tmp_path)
    monkeypatch.setattr(candle_view, "trainer_runs", lambda: [])
    folder = candle_lm.run_dir("XAUUSD", "fast")
    folder.mkdir(parents=True)
    rows = [{"epoch": i, "score": 1 - i / 1000, "train_ratio": 1.0, "lr": 1e-3, "seconds": 2.0, "best": i % 50 == 0} for i in range(1, 1201)]
    (folder / "log.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (folder / "status.json").write_text(json.dumps({"epoch": 1200, "best_score": 0.8, "running": False}))
    hist = candle_view.lab_history("XAUUSD", "fast", points=100)
    assert hist["total"] == 1200 and len(hist["rows"]) <= 130
    assert all(r["epoch"] in {x["epoch"] for x in hist["rows"]} for r in rows if r["best"])
    assert hist["rows"][-1]["epoch"] == 1200
    st = candle_view.lab_status("XAUUSD", "fast")
    assert st["size"] == "fast" and st["running"] is False and st["epoch"] == 1200
    assert "lr" in st["keys"]["live"] and "ctx" in st["keys"]["fresh"]
    saved = candle_view.save_settings("XAUUSD", "fast", {"lr": 0.002, "ctx": 64})
    assert saved["now"] == [] and "lr" in saved["next_start"] and saved["fresh_only"] == ["ctx"]


def test_a_short_run_takes_saved_settings(tmp_path, monkeypatch):
    """Two epochs on made-up bars: settings.json shapes a fresh run, a request left from before is dropped."""
    import json

    import numpy as np

    from trading.quant import candle_lm

    rng = np.random.default_rng(1)
    n = 3000
    close = 2000 + np.cumsum(rng.normal(0, 1, n))
    o = np.concatenate([[close[0]], close[:-1]])
    hi, lo = np.maximum(o, close) + rng.random(n), np.minimum(o, close) - rng.random(n)
    bars = np.column_stack([np.arange(n) * 300.0, o, hi, lo, close, np.ones(n)])
    monkeypatch.setattr(candle_lm, "RUNS", tmp_path)
    monkeypatch.setattr(candle_lm, "_load_bars", lambda name, sync: (bars, {}))
    monkeypatch.setattr(candle_lm, "_go_light", lambda threads: 2)
    folder = candle_lm.run_dir("XAUUSD", "fast")
    candle_lm.write_settings(folder, {"lr": 0.0007, "batch": 16, "ctx": 64, "d_model": 64, "layers": 1, "heads": 2})
    candle_lm.send_control(folder, "reset_lr")          # left over from before: dropped at start
    candle_lm.train("XAUUSD", "fast", epochs=2, fresh=True, compile_model=False)
    st = json.loads((folder / "status.json").read_text())
    assert st["epoch"] == 2 and st["running"] is False
    assert st["config"]["lr"] == 0.0007 and st["config"]["ctx"] == 64 and st["config"]["batch"] == 16
    assert not (folder / "control.json").exists()
    assert (folder / "best.pt").exists()
    assert len((folder / "log.jsonl").read_text().splitlines()) == 2
