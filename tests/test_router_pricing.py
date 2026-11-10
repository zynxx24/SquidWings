"""Router page cost estimate: which price applies, how a request is priced, and the usage summary in dollars."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from model.router import pricing
from model.router.pricing import Price, PriceBook, builtin, canonical, from_listing
from model.router.usage import UsageLog


def test_model_ids_are_read_the_way_resellers_write_them():
    assert canonical("vk/anthropic/claude-sonnet-4.6") == "claude-sonnet-4-6"
    assert canonical("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5"
    assert canonical("or/anthropic/claude-haiku-5.5:beta") == "claude-haiku-5-5"


def test_a_version_never_prices_its_successor():
    assert builtin("claude-opus-5-5") == Price(4.0, 20.0, 0.20)
    assert builtin("claude-opus-5") == Price(5.0, 25.0)
    assert builtin("vk/anthropic/claude-sonnet-4.6") == Price(3.0, 15.0)
    assert builtin("anthropic/claude-sonnet-5.5") == Price(2.0, 10.0, 0.10)
    assert builtin("deepseek/deepseek-v4.1-flash") is None, "no made-up prices for other providers"
    assert builtin("claude-opus-4-1") is None


def test_a_request_is_priced_with_cache_reads_and_writes():
    price = Price(4.0, 20.0, 0.20)
    # 1M prompt tokens of which 600k cached and 100k written, plus 10k output.
    cost = price.cost(prompt=1_000_000, output=10_000, cached=600_000, cache_write=100_000)
    assert cost == pytest.approx(300_000 * 4 / 1e6 + 600_000 * 0.2 / 1e6 + 100_000 * 5 / 1e6 + 10_000 * 20 / 1e6)
    assert Price(1.0, 2.0).cached_rate == pytest.approx(0.1), "a missing cache price is 10% of the input"


def test_provider_listings_in_either_unit():
    per_token = from_listing({"id": "x", "pricing": {"prompt": "0.000003", "completion": "0.000015"}})
    assert per_token == Price(3.0, 15.0)
    per_million = from_listing({"id": "y", "pricing": {"input": 0.5, "output": 1.5, "cached": 0.05}})
    assert per_million == Price(0.5, 1.5, 0.05)
    assert from_listing({"id": "z"}) is None


def test_typed_price_beats_listed_beats_builtin(tmp_path):
    book = PriceBook(tmp_path / "prices.json")
    model = "vk/anthropic/claude-sonnet-4.6"
    assert book.lookup(model) == (Price(3.0, 15.0), "bawaan")
    book.remember_listed([{"id": model, "pricing": {"prompt": "0.000001", "completion": "0.000002"}}])
    assert book.lookup(model)[1] == "listed"
    book.set_custom(model, {"input": "0.3", "output": "1.2"})
    assert book.lookup(model) == (Price(0.3, 1.2), "custom")
    book.clear_custom(model)
    assert book.lookup(model)[1] == "listed"
    assert book.lookup("some/model:free") == (Price(0.0, 0.0, 0.0, 0.0), "gratis")
    assert PriceBook(tmp_path / "prices.json").lookup(model)[1] == "listed", "saved to disk"


@pytest.mark.parametrize("body", [{"input": "", "output": "1"}, {"input": "-1", "output": "1"}, {"input": "abc", "output": "1"}])
def test_bad_prices_are_refused(tmp_path, body):
    with pytest.raises(ValueError):
        PriceBook(tmp_path / "p.json").set_custom("m", body)


def test_summary_counts_dollars_and_lists_models_without_a_price(tmp_path):
    log = UsageLog(tmp_path / "usage.db")
    now = 1_800_000_000.0
    log.record(provider="a", model="claude-haiku-5-5", usage={"prompt_tokens": 1_000_000, "completion_tokens": 100_000}, ts=now - 600)
    log.record(provider="a", model="mystery-model", usage={"prompt_tokens": 500, "completion_tokens": 50}, ts=now - 300)
    book = PriceBook(tmp_path / "prices.json")
    data = log.summary("24h", "provider", now=now, price=book.price)
    expected = 1_000_000 * 0.10 / 1e6 + 100_000 * 0.50 / 1e6
    assert data["totals"]["cost_usd"] == pytest.approx(expected)
    assert data["totals"]["unpriced_models"] == ["mystery-model"] and data["totals"]["unpriced_requests"] == 1
    assert data["rows"][0]["cost_usd"] == pytest.approx(expected) and data["rows"][0]["unpriced_requests"] == 1
    assert sum(p["cost_usd"] for p in data["series"]) == pytest.approx(expected)
    assert data["totals"]["cost_30d_usd"] == pytest.approx(expected * 30, rel=0.01), "24 hours at this pace, times 30"
    by_model = log.summary("24h", "model", now=now, price=book.price)
    assert {r["model"]: r["cost_usd"] for r in by_model["rows"]}["mystery-model"] == 0
    assert "cost_usd" not in log.summary("24h", "provider", now=now)["totals"], "no lookup, no dollars"


def test_router_api_prices_and_usage(monkeypatch, tmp_path):
    from model.router import usage as usage_log
    from web.server import app

    monkeypatch.setattr(pricing, "_book", PriceBook(tmp_path / "prices.json"))
    monkeypatch.setattr(pricing, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(usage_log, "_log", UsageLog(tmp_path / "usage.db"))
    monkeypatch.setattr(usage_log, "store_dir", lambda: tmp_path)
    usage_log.get_usage_log().record(provider="p", model="cheap/model", usage={"prompt_tokens": 2_000_000, "completion_tokens": 0})
    client = TestClient(app, client=("127.0.0.1", 50000))
    listed = client.get("/api/router/prices").json()
    assert listed["models"][0] == {"model": "cheap/model", "tokens": 2_000_000, "source": "", "price": None}
    saved = client.post("/api/router/prices", json={"model": "cheap/model", "input": 0.25, "output": 1, "usd_idr": 16000}).json()
    assert saved["models"][0]["source"] == "custom" and saved["usd_idr"] == 16000
    usage = client.get("/api/router/usage?period=24h").json()
    assert usage["totals"]["cost_usd"] == pytest.approx(0.5) and usage["usd_idr"] == 16000
    assert client.post("/api/router/prices", json={"model": "cheap/model", "input": "x", "output": 1}).status_code == 422
    cleared = client.post("/api/router/prices", json={"model": "cheap/model", "clear": True}).json()
    assert cleared["models"][0]["source"] == ""
