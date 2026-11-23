"""The basket backtest, its reviewers, and the tick recorder. Offline; the LLM is a fake."""

from __future__ import annotations

import asyncio
import json
import random

import pytest

from trading.quant import scalp, scalp_review, ticks


def _walk(n: int = 1500, seed: int = 3, step: int = 60) -> list[dict[str, float]]:
    rng = random.Random(seed)
    bars, px = [], 2000.0
    for i in range(n):
        o = px
        c = px + rng.gauss(0, 0.8)
        bars.append({"t": 1_700_000_000.0 + i * step, "o": o, "h": max(o, c) + abs(rng.gauss(0, 0.4)),
                     "l": min(o, c) - abs(rng.gauss(0, 0.4)), "c": c, "v": 50.0})
        px = c
    return bars


def test_votes_never_look_ahead():
    bars = _walk(400)
    early = scalp.votes(bars[:300])
    changed = bars[:300] + [dict(b, c=b["c"] + 50, h=b["h"] + 50) for b in bars[300:]]
    later = scalp.votes(changed)
    assert [row[:300] for row in later] == early


def test_scores_never_use_the_future():
    bars = _walk(400)
    rows = scalp.votes(bars)
    full = scalp.weighted_scores(bars, rows, 1)
    cut = scalp.weighted_scores(bars[:300], [row[:300] for row in rows], 1)
    assert full[:300] == cut


def test_a_basket_has_five_positions_and_stop_wins_a_tie():
    bars = [{"t": 0.0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1}] * 2
    bars = bars + [{"t": 60.0, "o": 100, "h": 120, "l": 80, "c": 100, "v": 1}]
    basket = scalp._open_basket(bars, 1, 1, 2.0, scalp.Policy(tp_atr=1.0, sl_atr=1.0, hold_s=30), 0.0, 0.0, 60.0)
    assert scalp.POSITIONS == 5
    assert basket["stopped"] is True and basket["r"] == pytest.approx(-1.0)


def test_partial_targets_add_up_before_the_time_runs_out():
    bars = [{"t": 0.0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1}] * 2
    bars = bars + [{"t": 60.0, "o": 100, "h": 101.05, "l": 99.5, "c": 100, "v": 1}]
    basket = scalp._open_basket(bars, 1, 1, 1.0, scalp.Policy(tp_atr=1.0, sl_atr=1.0, hold_s=30), 0.0, 0.0, 60.0)
    assert basket["r"] == pytest.approx((0.6 + 0.8 + 1.0 + 0 + 0) / 5)


def test_spread_is_paid_on_entry():
    bars = [{"t": 0.0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1}] * 2
    bars = bars + [{"t": 60.0, "o": 100, "h": 100.1, "l": 99.9, "c": 100, "v": 1}]
    free = scalp._open_basket(bars, 1, 1, 1.0, scalp.Policy(), 0.0, 0.0, 60.0)
    paid = scalp._open_basket(bars, 1, 1, 1.0, scalp.Policy(), 0.2, 0.0, 60.0)
    assert paid["entry"] == pytest.approx(100.1) and paid["r"] < free["r"]


def test_summary_says_when_the_bars_are_too_long_for_the_hold():
    out = scalp.run(_walk(), policy=scalp.Policy(threshold=0.2))["summary"]
    assert out["bar_seconds"] == 60 and "resolution_warning" in out and out["baskets"] > 0


def test_the_summary_reports_the_break_even_win_rate():
    out = scalp.run(_walk(), policy=scalp.Policy(threshold=0.2), spread=0.1)["summary"]
    avg_win, avg_loss = out["avg_win_r"], out["avg_loss_r"]
    assert out["breakeven_win_rate"] == pytest.approx(avg_loss / (avg_win + avg_loss), abs=0.002)


def test_policy_answers_are_forced_inside_the_bounds():
    policy = scalp.Policy()
    changed = policy.apply({"sl_atr": 999, "tp_atr": -5, "threshold": "nonsense", "volume": 100, "hold_s": 9})
    assert policy.sl_atr == scalp.BOUNDS["sl_atr"][1] and policy.tp_atr == scalp.BOUNDS["tp_atr"][0]
    assert "volume" not in changed and "threshold" not in changed and policy.hold_s == 30


def test_rule_reviewer_waits_longer_after_a_losing_streak():
    stats = {"window": 10, "losing_streak": 3, "expectancy_r": -0.1, "win_rate": 0.5}
    answer = scalp_review.rule_reviewer(stats, {"cooldown_s": 30.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0})
    assert answer["cooldown_s"] == 60 and answer["threshold"] == pytest.approx(0.75)


def test_rule_reviewer_stops_relaxing_the_pause_at_a_floor():
    stats = {"window": 10, "losing_streak": 0, "expectancy_r": 0.1, "win_rate": 0.9}
    policy = {"cooldown_s": 20.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0}
    assert "cooldown_s" not in scalp_review.rule_reviewer(stats, policy)


def test_rule_reviewer_cuts_the_stop_when_wins_do_not_pay():
    stats = {"window": 10, "losing_streak": 0, "expectancy_r": -0.02, "win_rate": 0.9}
    answer = scalp_review.rule_reviewer(stats, {"cooldown_s": 30.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0})
    assert answer["sl_atr"] == pytest.approx(2.7)


def test_reviewer_sees_only_closed_baskets_and_its_changes_are_logged():
    seen = []

    def spy(stats, policy):
        seen.append(stats["baskets"])
        return {"threshold": 0.2 + len(seen) * 0.001, "why": "test"}

    out = scalp.run(_walk(), policy=scalp.Policy(threshold=0.2), reviewer=spy)
    assert seen == sorted(seen) and out["reviews"] and out["reviews"][0]["why"] == "test"


class _FakeClient:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    async def chat_completion(self, **kwargs):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"choices": [{"message": {"role": "assistant", "content": self.reply}}]}


def _stats():
    return {"window": 10, "losing_streak": 1, "expectancy_r": -0.1, "win_rate": 0.5, "baskets": 10}


def test_llm_answer_is_used_and_calls_are_capped():
    client = _FakeClient('```json\n{"tp_atr": 0.25, "why": "narrower"}\n```')
    review, usage = scalp_review.llm_reviewer(client, "cheap", max_calls=2)
    policy = {"cooldown_s": 30.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0}
    assert review(_stats(), policy)["tp_atr"] == 0.25
    review(_stats(), policy)
    review(_stats(), policy)
    assert client.calls == 2 and usage == {"llm_calls": 2, "llm_failures": 0}


def test_llm_failure_falls_back_to_the_rule():
    review, usage = scalp_review.llm_reviewer(_FakeClient(RuntimeError("down")), "cheap")
    assert "tp_atr" in review(_stats(), {"cooldown_s": 30.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0})
    assert usage["llm_failures"] == 1


def test_prompt_puts_expectancy_first_and_lists_the_limits():
    text = scalp_review.build_prompt(_stats(), {"tp_atr": 0.3})
    assert "Expectancy comes first" in text and json.dumps(list(scalp.BOUNDS["sl_atr"])) in text


def test_tick_files_become_short_bars(tmp_path):
    for seen, bid in ((100.0, 1.0), (101.0, 1.2), (104.9, 0.9), (105.0, 1.1)):
        ticks.append("EUR/USD", seen, bid, bid + 0.02, folder=tmp_path)
    (tmp_path / "EUR_USD-0.csv").open("a").write("106.0,1")      # a line cut short by a crash
    bars, spread = ticks.load_bars("EUR/USD", 5, folder=tmp_path)
    assert [b["t"] for b in bars] == [100.0, 105.0]
    assert bars[0]["h"] == pytest.approx(1.21) and bars[0]["l"] == pytest.approx(0.91) and bars[0]["v"] == 3
    assert spread == pytest.approx(0.02)


def test_no_recorded_ticks_gives_no_bars(tmp_path):
    assert ticks.load_bars("XAUUSD", folder=tmp_path) == ([], 0.0)


def test_the_llm_reviewer_can_be_built_from_the_real_settings():
    from web import quant_api

    review, usage = quant_api._scalp_reviewer(True)
    assert callable(review) and usage == {"llm_calls": 0, "llm_failures": 0}


class _Listing(_FakeClient):
    async def list_models(self):
        return [{"id": "text-embedding-3"}, {"id": "vendor/opus-big"}, {"id": "vendor/tiny-flash"}]

    async def chat_completion(self, **kwargs):
        self.model = kwargs["model"]
        return await super().chat_completion(**kwargs)


def test_with_no_model_set_the_reviewer_picks_a_cheap_listed_one():
    client = _Listing('{"tp_atr": 0.25}')
    review, _ = scalp_review.llm_reviewer(client, "")
    review(_stats(), {"cooldown_s": 30.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0})
    assert client.model == "vendor/tiny-flash"


def test_every_review_shares_one_event_loop():
    seen = []

    class Client(_FakeClient):
        async def chat_completion(self, **kwargs):
            seen.append(asyncio.get_running_loop())
            return await super().chat_completion(**kwargs)

    review, _ = scalp_review.llm_reviewer(Client('{"tp_atr": 0.25}'), "m", max_calls=3)
    for _ in range(3):
        review(_stats(), {"cooldown_s": 30.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0})
    assert len(set(seen)) == 1, "connections belong to one loop; a new loop per call breaks the second call"


def test_after_one_failure_the_llm_is_left_alone():
    client = _FakeClient(RuntimeError("down"))
    review, usage = scalp_review.llm_reviewer(client, "m")
    for _ in range(3):
        review(_stats(), {"cooldown_s": 30.0, "threshold": 0.7, "tp_atr": 0.3, "sl_atr": 3.0})
    assert client.calls == 1 and usage["llm_failures"] == 1


def test_the_heavy_job_really_runs_in_another_process(monkeypatch):
    import os

    from web import quant_api

    monkeypatch.setenv("QUANT_WORKER", "1")
    monkeypatch.setattr(quant_api, "_worker", None)
    try:
        assert quant_api._in_worker(os.getpid) != os.getpid()
    finally:
        if quant_api._worker:
            quant_api._worker.shutdown()

