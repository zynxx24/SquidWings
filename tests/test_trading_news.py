"""Market news from free sources: parsing, topics, tone, the cache, and the API. No network."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trading import news
from trading.news import NewsDesk

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Bitcoin surges as ETF inflows hit a record</title><link>https://example.com/a</link>
<pubDate>Tue, 10 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Crypto rally&lt;/p&gt;</description></item>
<item><title>Fed's Powell warns inflation is sticky - Reuters</title><link>javascript:alert(1)</link>
<pubDate>Tue, 10 Nov 2026 09:30:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Nvidia unveils new AI chips</title><link href="https://example.com/n"/><updated>2026-11-10T08:00:00Z</updated></entry>
</feed>"""
CALENDAR = json.dumps([
    {"title": "Core CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "impact": "High", "forecast": "0.2%", "previous": "0.3%"},
    {"title": "German ZEW", "country": "EUR", "date": "2026-11-10T05:00:00-05:00", "impact": "Medium", "forecast": "", "previous": ""},
]).encode()
GLOBAL = json.dumps({"data": {"market_cap_change_percentage_24h_usd": -2.5, "market_cap_percentage": {"btc": 59.6}}}).encode()
TRENDING = json.dumps({"coins": [{"item": {"name": "Avalanche", "symbol": "avax", "market_cap_rank": 26,
                                           "data": {"price_change_percentage_24h": {"usd": 9.18}}}}]}).encode()
PRICES = json.dumps({"bitcoin": {"usd": 100000, "usd_24h_change": 0.5}, "ethereum": {"usd": 4000, "usd_24h_change": -1.2}}).encode()
FNG = json.dumps({"data": [{"value": "32", "value_classification": "Fear"}]}).encode()
NOW = 1_794_312_000.0      # 2026-11-10 12:00 UTC: the CPI release is 1h30m later


def fake_get(fail: set[str] | None = None, calls: list[str] | None = None) -> Any:
    def get(url: str) -> bytes:
        if calls is not None:
            calls.append(url)
        if fail and any(part in url for part in fail):
            raise ConnectionError("down")
        if "faireconomy" in url:
            return CALENDAR
        if url.endswith("/global"):
            return GLOBAL
        if "trending" in url:
            return TRENDING
        if "simple/price" in url:
            return PRICES
        if "alternative.me" in url:
            return FNG
        if "decrypt" in url:
            return ATOM
        return RSS
    return get


def test_rss_and_atom_headlines_with_unsafe_links_dropped():
    items = news.parse_feed(RSS, "Test")
    assert [i["title"] for i in items] == ["Bitcoin surges as ETF inflows hit a record", "Fed's Powell warns inflation is sticky - Reuters"]
    assert items[0]["link"] == "https://example.com/a" and items[1]["link"] == "", "only http(s) links reach the page"
    assert items[0]["summary"] == "Crypto rally" and items[0]["published"] == pytest.approx(1794304800.0)
    atom = news.parse_feed(ATOM, "Atom")
    assert atom[0]["link"] == "https://example.com/n" and atom[0]["published"]
    google = news.parse_feed(RSS, "Google News · makro")
    assert google[1]["title"] == "Fed's Powell warns inflation is sticky" and google[1]["source"] == "Reuters (Google News)"


def test_entity_expansion_is_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'
    from defusedxml import EntitiesForbidden

    with pytest.raises(EntitiesForbidden):
        news.parse_feed(bomb, "x")


def test_topics_and_tone():
    assert set(news.topics_of("Elon Musk tweets about Dogecoin and Bitcoin")) >= {"influencer", "blockchain"}
    assert "ai" in news.topics_of("OpenAI and Nvidia lead AI rally")
    assert "bank_sentral" in news.topics_of("Fed signals rate cuts") and "geopolitik" in news.topics_of("Iran tensions rise")
    assert "ai" not in news.topics_of("Thailand said"), "words are matched whole"
    assert news.tone_of("Bitcoin surges to record high") > 0 > news.tone_of("Stocks plunge as recession fears grow")
    assert news.tone_of("Markets wait for the data") == 0


@pytest.mark.parametrize(("symbol", "topic", "currencies"), [
    ("BTCUSD", "blockchain", ["USD"]), ("XAUUSD", "komoditas", ["USD"]), ("EURUSD", "bank_sentral", ["EUR", "USD"]),
    ("US100", "teknologi", ["USD"]), ("GBPJPY", "makro", ["GBP", "JPY"]),
])
def test_each_market_reads_its_own_topics(symbol, topic, currencies):
    assert topic in news.relevant_topics(symbol)
    assert news.currencies_of(symbol) == currencies


def test_snapshot_from_every_source(tmp_path):
    desk = NewsDesk(get=fake_get(), folder=tmp_path, clock=lambda: NOW)
    snap = desk.snapshot("EURUSD")
    assert all(s["ok"] for s in snap["sources"]) and len(snap["sources"]) == 3 + len(news.FEEDS)
    assert snap["calendar"]["next_high"]["title"] == "Core CPI m/m" and snap["calendar"]["next_high"]["in_min"] == 90
    assert not snap["calendar"]["near_high"]
    assert snap["crypto"]["fear_greed"] == {"value": 32, "label": "Fear"} and snap["crypto"]["trending"][0]["symbol"] == "AVAX"
    assert snap["mood"]["label"] in ("risk-off", "netral")
    titles = [h["title"] for h in snap["headlines"]]
    assert len(titles) == len(set(titles)), "the same headline from several feeds shows once"
    assert snap["topics"]["bank_sentral"]["count"] >= 1
    text = news.brief(snap)
    assert "Core CPI m/m in 90 min" in text and "Fear&Greed 32" in text


def test_cache_ttl_and_a_failing_source_keeps_its_last_copy(tmp_path):
    calls: list[str] = []
    now = [NOW]
    desk = NewsDesk(get=fake_get(calls=calls), folder=tmp_path, clock=lambda: now[0])
    desk.snapshot("XAUUSD")
    first = len(calls)
    desk.snapshot("XAUUSD")
    assert len(calls) == first, "inside the TTL nothing is fetched again"
    now[0] += 3600
    desk.get = fake_get(fail={"faireconomy"}, calls=calls)
    snap = desk.snapshot("XAUUSD")
    calendar = next(s for s in snap["sources"] if s["name"] == "calendar")
    assert not calendar["ok"] and calendar["error"] and snap["calendar"]["next_high"], "the old calendar is still used"
    reloaded = NewsDesk(get=fake_get(fail={"http"}), folder=tmp_path, clock=lambda: now[0])
    assert reloaded.snapshot("XAUUSD", fetch=False)["calendar"]["next_high"], "the cache survives a restart"


def test_calendar_feeds_the_proposal_news_filter(tmp_path):
    from trading import strategy

    desk = NewsDesk(get=fake_get(), folder=tmp_path, clock=lambda: NOW)
    events = strategy.load_news(desk.calendar_events())
    assert {e.currency for e in events} == {"USD", "EUR"}
    import datetime as dt
    when = dt.datetime.fromtimestamp(NOW + 90 * 60 - 600, dt.UTC)
    assert strategy.news_block("EURUSD", when, events) == "high-impact USD news within 30 min: Core CPI m/m"


def test_market_condition_from_the_mt4_price():
    bars = [{"t": i * 60.0, "o": 100.0, "h": 100.5, "l": 99.5, "c": 100.0 + i * 0.01, "v": 1.0} for i in range(50)]
    bars += [{"t": (50 + i) * 60.0, "o": 100.5, "h": 102.0, "l": 99.0, "c": 100.6, "v": 1.0} for i in range(10)]
    cond = news.market_condition(bars, {"bid": 100.6, "ask": 100.7})
    assert cond["state"] == "ramai" and cond["volatility_ratio"] == pytest.approx(3.0)
    assert news.market_condition(bars[:5], {"bid": 1, "ask": 1.1})["state"] == "data kurang"


def test_news_api(monkeypatch, tmp_path):
    from web import quant_api
    from web.server import app

    desk = NewsDesk(get=fake_get(), folder=tmp_path, clock=lambda: NOW)
    monkeypatch.setattr(news, "_desk", desk)

    class Feed:
        def rates(self, symbol, tf, count):
            return [{"t": i * 60.0, "o": 1.1, "h": 1.1005, "l": 1.0995, "c": 1.1, "v": 1.0} for i in range(count)]

        def tick(self, symbol):
            return {"bid": 1.1, "ask": 1.1001}

    monkeypatch.setattr(quant_api, "_view_backend", lambda: Feed())
    client = TestClient(app, client=("127.0.0.1", 50000))
    r = client.get("/api/quant/news?symbol=EURUSD").json()
    assert r["calendar"]["next_high"]["currency"] == "USD" and r["condition"]["state"] == "normal"
    assert client.get("/api/quant/news?symbol=bad%20symbol").status_code == 422
    monkeypatch.setenv("MT5_TRADE_TOKEN", "rahasia")
    remote = TestClient(app, client=("203.0.113.5", 50000))
    assert remote.get("/api/quant/news?symbol=EURUSD").status_code == 200, "reading is open, as on the rest of the panel"
    assert remote.get("/api/quant/news?symbol=EURUSD&refresh=true").status_code in (401, 403)
