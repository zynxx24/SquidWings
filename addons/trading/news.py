"""Market news from free sources, sorted into topics, for the panel, the paper simulation and the LLM.

Sources, none of which needs a key:

* the economic calendar of the week (ForexFactory's JSON feed): time, currency, impact, forecast,
* CoinGecko: total crypto market cap change, BTC dominance, trending coins, BTC and ETH 24h,
* the Crypto Fear & Greed index (alternative.me),
* RSS headlines: CoinDesk, Cointelegraph, Decrypt, FXStreet, and Google News searches for
  central banks and macro, and for technology and AI.

MQL4 has no access to the terminal's news tab or calendar, so the EA cannot send news. What MT4
does give, the live spread and price, is turned into a market condition by ``market_condition``.

Every headline gets topics from keyword lists (influencer, teknologi, ai, blockchain, regulasi,
bank_sentral, makro, geopolitik, komoditas) and a tone from a small word list: -1 bad news,
+1 good news. A keyword list misses sarcasm and context; the tone is a hint, not a signal. The
LLM reads the headlines themselves.

Each source is cached for its own time (``TTL_S``) in memory and in ``addons/trading/data/news/``, so a
restart does not lose them and a page refresh does not hit the sources again. A source that
fails keeps its last good copy and says how old it is.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import email.utils
import json
import re
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from defusedxml import ElementTree

FOLDER = Path(__file__).resolve().parent / "data" / "news"
TIMEOUT_S = 8.0
MAX_BYTES = 3_000_000
USER_AGENT = "Mozilla/5.0 (compatible; trading-panel/1.0)"
KEEP_HEADLINES = 60


def _google(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
COINGECKO = "https://api.coingecko.com/api/v3"
FNG_URL = "https://api.alternative.me/fng/?limit=1"
FEEDS: tuple[tuple[str, str], ...] = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss"),
    ("Cointelegraph", "https://cointelegraph.com/rss/"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("Google News · makro", _google('"federal reserve" OR inflation OR "interest rate" OR gold OR forex when:1d')),
    ("Google News · teknologi & AI", _google('"artificial intelligence" OR Nvidia OR OpenAI OR "tech stocks" when:1d')),
)
TTL_S = {"calendar": 1800.0, "coingecko": 300.0, "fng": 1800.0, "feed": 600.0}

TOPICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "influencer": ("Influencer & tokoh", ("elon musk", "musk", "saylor", "trump", "changpeng", "cz", "vitalik", "buterin",
                                          "cathie wood", "tweet", "tweets", "influencer", "kol", "x post", "jack dorsey")),
    "teknologi": ("Teknologi", ("apple", "microsoft", "alphabet", "google", "meta", "amazon", "tesla", "chip", "chips",
                                "semiconductor", "tech", "nasdaq", "software", "smartphone", "cloud", "quantum")),
    "ai": ("AI", ("ai", "artificial intelligence", "openai", "chatgpt", "anthropic", "claude", "gemini", "llm", "nvidia",
                  "machine learning", "gpu", "gpus", "data center", "data centers")),
    "blockchain": ("Blockchain & kripto", ("bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "blockchain",
                                           "stablecoin", "stablecoins", "defi", "nft", "solana", "xrp", "altcoin", "altcoins",
                                           "web3", "token", "tokens", "miners", "halving", "binance", "coinbase")),
    "regulasi": ("Regulasi & hukum", ("sec", "regulation", "regulator", "regulators", "regulatory", "lawsuit", "ban", "bans",
                                      "banned", "cftc", "court", "bill", "law", "legal", "probe", "license", "compliance")),
    "bank_sentral": ("Bank sentral & suku bunga", ("fed", "federal reserve", "fomc", "powell", "rate cut", "rate cuts",
                                                   "rate hike", "rate hikes", "interest rate", "interest rates", "ecb", "lagarde",
                                                   "boj", "bank of england", "boe", "rba", "snb", "central bank", "yields")),
    "makro": ("Makro ekonomi", ("inflation", "cpi", "pce", "gdp", "jobs", "payrolls", "nfp", "unemployment", "retail sales",
                                "pmi", "recession", "treasury", "dollar", "dxy", "economy", "economic", "growth", "deficit")),
    "geopolitik": ("Geopolitik", ("war", "israel", "iran", "russia", "ukraine", "china", "tariff", "tariffs", "sanction",
                                  "sanctions", "election", "missile", "missiles", "conflict", "tension", "tensions", "ceasefire",
                                  "military", "trade deal")),
    "komoditas": ("Komoditas", ("gold", "xau", "silver", "oil", "crude", "opec", "brent", "wti", "copper", "bullion", "commodities")),
}
_TOPIC_RE = {name: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
             for name, (_, words) in TOPICS.items()}
_GOOD = re.compile(r"\b(surge[sd]?|soar(s|ed)?|rall(y|ies|ied)|gain(s|ed)?|jump(s|ed)?|ris(e|es|ing)|rose|record high|bullish|"
                   r"beat(s)?|approv(e|es|ed|al)|upgrade[sd]?|inflows?|rebound(s|ed)?|climb(s|ed)?|boost(s|ed)?|adopt(s|ed|ion)?|"
                   r"partnership|optimism|recover(s|y|ed)?|cut(s)? rates?|rate cuts?|eas(e|es|ing))\b", re.IGNORECASE)
_BAD = re.compile(r"\b(plunge[sd]?|crash(es|ed)?|drop(s|ped)?|fall(s|ing)?|fell|slump(s|ed)?|bearish|miss(es|ed)?|reject(s|ed)?|"
                  r"ban(s|ned)?|hack(s|ed)?|exploit(s|ed)?|lawsuit|outflows?|fears?|sell-?off|warn(s|ing)?|declin(e|es|ed)|"
                  r"slid(e|es)?|tumbl(e|es|ed)|fraud|probe|liquidat(ion|ions|ed)|recession|default(s|ed)?|tension(s)?|"
                  r"hike(s|d)?|sticky|weak(er|ens)?|losses|loss)\b", re.IGNORECASE)

# Which topics move which market. Keys are matched against the upper-case symbol.
RELEVANCE: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "LTC", "CRYPTO"), ("blockchain", "influencer", "regulasi", "bank_sentral", "makro", "ai")),
    (("XAU", "GOLD", "XAG", "SILVER"), ("komoditas", "bank_sentral", "makro", "geopolitik")),
    (("WTI", "BRENT", "OIL", "XTI", "XBR"), ("komoditas", "geopolitik", "makro")),
    (("US30", "US100", "NAS", "SPX", "US500", "DJ", "NDX", "GER", "DAX"), ("teknologi", "ai", "bank_sentral", "makro")),
)
FX_TOPICS = ("bank_sentral", "makro", "geopolitik")
CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY"}


# ── fetching ─────────────────────────────────────────────────────────────────
Getter = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    import httpx

    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client, \
            client.stream("GET", url) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("response too large")
            chunks.append(chunk)
        return b"".join(chunks)


def _clean(text: str | None, limit: int = 240) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _when(text: str | None) -> float | None:
    if not text:
        return None
    with contextlib.suppress(TypeError, ValueError, IndexError):
        return email.utils.parsedate_to_datetime(text).timestamp()
    with contextlib.suppress(ValueError):
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    return None


def _safe_link(link: str) -> str:
    link = link.strip()
    return link[:500] if link.startswith(("https://", "http://")) else ""


def parse_feed(raw: bytes, source: str) -> list[dict[str, Any]]:
    """RSS 2.0 or Atom headlines. defusedxml refuses entity tricks."""
    root = ElementTree.fromstring(raw)
    items = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "link" and child.get("href"):
                fields.setdefault("link", child.get("href") or "")
            elif name in ("title", "link", "pubDate", "published", "updated", "description", "summary") and child.text:
                fields.setdefault(name, child.text)
        title = _clean(fields.get("title"), 200)
        if not title:
            continue
        # Google News appends " - Publisher" to every title; keep it as the source instead.
        shown_source = source
        if source.startswith("Google News") and " - " in title:
            title, publisher = title.rsplit(" - ", 1)
            shown_source = f"{publisher.strip()[:40]} (Google News)"
        items.append({"title": title, "source": shown_source, "link": _safe_link(fields.get("link", "")),
                      "published": _when(fields.get("pubDate") or fields.get("published") or fields.get("updated")),
                      "summary": _clean(fields.get("description") or fields.get("summary"), 240)})
    return items


def parse_calendar(raw: bytes) -> list[dict[str, Any]]:
    out = []
    for item in json.loads(raw):
        when = _when(str(item.get("date", "")))
        if when is None:
            continue
        out.append({"time": when, "currency": str(item.get("country", "")).upper()[:4],
                    "impact": str(item.get("impact", "")).lower()[:10], "title": _clean(str(item.get("title", "")), 120),
                    "forecast": _clean(str(item.get("forecast", "")), 20), "previous": _clean(str(item.get("previous", "")), 20)})
    return sorted(out, key=lambda e: e["time"])


def parse_coingecko(global_raw: bytes, trending_raw: bytes, price_raw: bytes) -> dict[str, Any]:
    data = json.loads(global_raw).get("data", {})
    trending = []
    for coin in json.loads(trending_raw).get("coins", [])[:7]:
        item = coin.get("item", {})
        change = ((item.get("data") or {}).get("price_change_percentage_24h") or {}).get("usd")
        trending.append({"name": _clean(str(item.get("name", "")), 40), "symbol": _clean(str(item.get("symbol", "")), 12).upper(),
                         "rank": item.get("market_cap_rank"),
                         "change_24h": round(float(change), 2) if isinstance(change, (int, float)) else None})
    prices = json.loads(price_raw)
    return {
        "market_cap_change_24h": round(float(data.get("market_cap_change_percentage_24h_usd") or 0), 2),
        "btc_dominance": round(float((data.get("market_cap_percentage") or {}).get("btc") or 0), 1),
        "btc_24h": round(float((prices.get("bitcoin") or {}).get("usd_24h_change") or 0), 2),
        "eth_24h": round(float((prices.get("ethereum") or {}).get("usd_24h_change") or 0), 2),
        "trending": trending,
    }


def parse_fng(raw: bytes) -> dict[str, Any]:
    row = (json.loads(raw).get("data") or [{}])[0]
    return {"value": int(row.get("value", 50)), "label": _clean(str(row.get("value_classification", "")), 30)}


# ── topics and tone ──────────────────────────────────────────────────────────
def topics_of(text: str) -> list[str]:
    return [name for name, pattern in _TOPIC_RE.items() if pattern.search(text)]


def tone_of(text: str) -> float:
    good, bad = len(_GOOD.findall(text)), len(_BAD.findall(text))
    return 0.0 if good == bad else max(-1.0, min(1.0, (good - bad) / max(good + bad, 1)))


def relevant_topics(symbol: str) -> tuple[str, ...]:
    name = symbol.upper()
    for keys, topics in RELEVANCE:
        if any(key in name for key in keys):
            return topics
    return FX_TOPICS


def currencies_of(symbol: str) -> list[str]:
    raw = symbol.upper()
    if any(key in raw for key in ("XAU", "XAG", "BTC", "ETH", "US30", "US100", "US500", "NAS", "SPX", "WTI", "OIL")):
        return ["USD"]
    name = re.sub(r"[^A-Z]", "", raw)
    return [c for c in (name[:3], name[3:6]) if c in CURRENCIES]


# ── the cache ────────────────────────────────────────────────────────────────
class NewsDesk:
    def __init__(self, get: Getter | None = None, folder: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.get = get or http_get
        self.folder = folder or FOLDER
        self.clock = clock
        self._lock = threading.Lock()
        self._fetching = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _path(self) -> Path:
        return self.folder / "cache.json"

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(self._path().read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._cache.update({k: v for k, v in data.items() if isinstance(v, dict)})

    def _save(self) -> None:
        with contextlib.suppress(OSError):
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self._path().with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path())

    def _jobs(self) -> dict[str, tuple[str, Callable[[], Any]]]:
        jobs: dict[str, tuple[str, Callable[[], Any]]] = {
            "calendar": ("calendar", lambda: parse_calendar(self.get(CALENDAR_URL))),
            "coingecko": ("coingecko", lambda: parse_coingecko(
                self.get(f"{COINGECKO}/global"), self.get(f"{COINGECKO}/search/trending"),
                self.get(f"{COINGECKO}/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"))),
            "fng": ("fng", lambda: parse_fng(self.get(FNG_URL))),
        }
        for name, url in FEEDS:
            jobs[f"feed:{name}"] = ("feed", self._feed_job(name, url))
        return jobs

    def _feed_job(self, name: str, url: str) -> Callable[[], list[dict[str, Any]]]:
        return lambda: parse_feed(self.get(url), name)

    def refresh(self, force: bool = False, only: tuple[str, ...] = ()) -> None:
        """Fetch the sources whose copy is older than its TTL, all at once. One refresh at a time.

        ``only`` limits it to some sources, such as ``("calendar",)`` for the order proposal.
        """
        if not self._fetching.acquire(blocking=False):
            return
        try:
            with self._lock:
                self._load()
                now = self.clock()
                due = {key: job for key, job in self._jobs().items()
                       if (not only or key in only)
                       and (force or now - float(self._cache.get(key, {}).get("at", 0)) >= TTL_S[job[0]])}
            if not due:
                return

            def run(item: tuple[str, tuple[str, Callable[[], Any]]]) -> tuple[str, Any, str]:
                key, (_, fn) = item
                try:
                    return key, fn(), ""
                except Exception as exc:  # noqa: BLE001 - one dead source must not hide the others
                    return key, None, str(exc)[:160]

            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(run, due.items()))
            with self._lock:
                for key, value, error in results:
                    old = self._cache.get(key, {})
                    if error:
                        self._cache[key] = {**old, "error": error, "tried": self.clock()}
                    else:
                        self._cache[key] = {"at": self.clock(), "data": value, "error": ""}
                        if key == "calendar" and value:
                            archive_calendar(value, self.folder)
                self._save()
        finally:
            self._fetching.release()

    def refresh_in_background(self) -> None:
        threading.Thread(target=self.refresh, name="news-refresh", daemon=True).start()

    def _data(self, key: str) -> Any:
        return self._cache.get(key, {}).get("data")

    # ── the snapshot ─────────────────────────────────────────────────────────
    def snapshot(self, symbol: str = "", *, fetch: bool = True, force: bool = False) -> dict[str, Any]:
        """Everything for one symbol. ``fetch=False`` answers from the cache at once (and refreshes behind)."""
        if fetch:
            self.refresh(force=force)
        else:
            self.refresh_in_background()
        with self._lock:
            self._load()
            now = self.clock()
            sources = [{"name": key.removeprefix("feed:"), "ok": bool(entry.get("data") is not None and not entry.get("error")),
                        "error": entry.get("error", ""), "age_s": round(now - float(entry["at"])) if entry.get("at") else None}
                       for key, entry in sorted(self._cache.items())]
            calendar = list(self._data("calendar") or [])
            crypto = dict(self._data("coingecko") or {})
            fng = self._data("fng")
            headlines = [h for key, entry in self._cache.items() if key.startswith("feed:") for h in (entry.get("data") or [])]
        return build(symbol, now, calendar, crypto, fng, headlines, sources)

    def calendar_events(self) -> list[dict[str, Any]]:
        """The week's events in the shape ``strategy.load_news`` reads, from the cache (fetching if stale)."""
        self.refresh(only=("calendar",))
        with self._lock:
            events = list(self._data("calendar") or [])
        return [{"time": dt.datetime.fromtimestamp(e["time"], dt.UTC).isoformat(), "currency": e["currency"],
                 "impact": e["impact"], "title": e["title"]} for e in events if e["currency"] in CURRENCIES]


# ── the calendar archive ───────────────────────────────────────────────────
# ForexFactory only serves this week. Every week fetched is kept, so the trading brain can learn
# what the market did around past releases; the history starts the week the panel first ran.
ARCHIVE_NAME = "calendar-archive.json"
_archive_lock = threading.Lock()


def _archive_path(folder: Path | None = None) -> Path:
    return (folder or FOLDER) / ARCHIVE_NAME


def archive_calendar(events: list[dict[str, Any]], folder: Path | None = None) -> int:
    """Merge a fetched week into the archive. Returns how many events it holds."""
    if not events:
        return 0
    with _archive_lock:
        data = load_calendar_archive(folder)
        known = {(e["time"], e["currency"], e["title"]): e for e in data["events"]}
        for e in events:
            known[(e["time"], e["currency"], e["title"])] = e
        times = [float(e["time"]) for e in events]
        coverage = [*data["coverage"], [min(times) - 86400.0, max(times) + 3600.0]]
        coverage.sort()
        merged: list[list[float]] = []
        for start, end in coverage:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        out = {"events": sorted(known.values(), key=lambda e: e["time"]), "coverage": merged}
        path = _archive_path(folder)
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        return len(out["events"])


def load_calendar_archive(folder: Path | None = None) -> dict[str, Any]:
    """Every archived event and the time spans the archive covers: {"events": [...], "coverage": [[start, end]]}."""
    try:
        data = json.loads(_archive_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    events = [e for e in data.get("events", []) if isinstance(e, dict) and "time" in e]
    coverage = [[float(a), float(b)] for a, b in data.get("coverage", []) if b > a]
    if not events:      # the archive started after the cache: seed it from the week already fetched
        with contextlib.suppress(OSError, ValueError):
            cached = json.loads(((folder or FOLDER) / "cache.json").read_text(encoding="utf-8"))
            week = (cached.get("calendar") or {}).get("data") or []
            if week:
                times = [float(e["time"]) for e in week]
                return {"events": week, "coverage": [[min(times) - 86400.0, max(times) + 3600.0]]}
    return {"events": events, "coverage": coverage}


def build(symbol: str, now: float, calendar: list[dict[str, Any]], crypto: dict[str, Any], fng: dict[str, Any] | None,
          headlines: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    rows = []
    for item in sorted(headlines, key=lambda h: -(h.get("published") or 0)):
        key = re.sub(r"\W+", "", item["title"].lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        text = f"{item['title']} {item.get('summary', '')}"
        rows.append({**item, "topics": topics_of(text), "tone": round(tone_of(item["title"]), 2),
                     "age_min": round((now - item["published"]) / 60) if item.get("published") else None})
    rows = rows[:KEEP_HEADLINES * 2]
    topics = {}
    for name, (label, _) in TOPICS.items():
        mine = [r for r in rows if name in r["topics"]]
        tones = [r["tone"] for r in mine if r["tone"]]
        topics[name] = {"label": label, "count": len(mine), "tone": round(statistics.fmean(tones), 2) if tones else 0.0,
                        "top": [r["title"] for r in mine[:3]]}
    relevant = [t for t in relevant_topics(symbol)] if symbol else list(TOPICS)
    currencies = currencies_of(symbol) if symbol else sorted(CURRENCIES)
    mine_events = [dict(e) for e in calendar if e["currency"] in currencies]
    upcoming = [e for e in mine_events if now - 3600 <= e["time"] <= now + 86400 and e["impact"] in ("high", "medium")][:8]
    next_high = next((e for e in mine_events if e["time"] >= now - 1800 and e["impact"] == "high"), None)
    for event in (*upcoming, *([next_high] if next_high else [])):
        event["in_min"] = round((event["time"] - now) / 60)
    mood_parts = []
    if fng:
        mood_parts.append((fng["value"] - 50) / 50)
    if crypto.get("market_cap_change_24h") is not None and crypto:
        mood_parts.append(max(-1.0, min(1.0, crypto["market_cap_change_24h"] / 5)))
    tone_all = [r["tone"] for r in rows[:40] if r["tone"]]
    if tone_all:
        mood_parts.append(statistics.fmean(tone_all))
    mood = round(statistics.fmean(mood_parts), 2) if mood_parts else 0.0
    return {
        "symbol": symbol, "at": now, "sources": sources,
        "calendar": {"currencies": currencies, "upcoming": upcoming, "next_high": next_high,
                     "near_high": bool(next_high and abs(next_high["time"] - now) <= 1800)},
        "crypto": {**crypto, "fear_greed": fng},
        "topics": topics, "relevant": relevant,
        "headlines": [r for r in rows if not symbol or set(r["topics"]) & set(relevant)][:KEEP_HEADLINES] or rows[:KEEP_HEADLINES],
        "all_headlines": len(rows),
        "mood": {"score": mood, "label": "risk-on" if mood > 0.15 else "risk-off" if mood < -0.15 else "netral"},
        "note": "MT4 tidak membuka berita ke EA (MQL4 tidak punya akses ke tab News). Berita dari sumber gratis; "
                "kondisi pasar dari harga MT4. Nada berita dari daftar kata: petunjuk, bukan sinyal.",
    }


def brief(snapshot: dict[str, Any], *, headlines: int = 8) -> str:
    """A short text of the snapshot for an LLM prompt."""
    lines = []
    cal = snapshot.get("calendar", {})
    if cal.get("next_high"):
        e = cal["next_high"]
        lines.append(f"Next high-impact {e['currency']} event: {e['title']} in {e['in_min']} min (forecast {e['forecast'] or '-'}, previous {e['previous'] or '-'}).")
    crypto = snapshot.get("crypto") or {}
    if crypto.get("fear_greed"):
        lines.append(f"Crypto Fear&Greed {crypto['fear_greed']['value']} ({crypto['fear_greed']['label']}); "
                     f"crypto market cap 24h {crypto.get('market_cap_change_24h', 0)}%, BTC 24h {crypto.get('btc_24h', 0)}%.")
    mood = snapshot.get("mood") or {}
    lines.append(f"Overall mood {mood.get('label', 'netral')} ({mood.get('score', 0)}).")
    for name in snapshot.get("relevant", [])[:5]:
        topic = snapshot["topics"].get(name, {})
        if topic.get("count"):
            lines.append(f"{topic['label']}: {topic['count']} headlines, tone {topic['tone']}.")
    for row in snapshot.get("headlines", [])[:headlines]:
        age = f"{row['age_min']}m ago" if row.get("age_min") is not None else ""
        lines.append(f"- {row['title']} [{', '.join(row['topics']) or 'other'}; tone {row['tone']}; {age}]")
    return "\n".join(lines)[:2500]


def market_condition(bars: list[dict[str, float]], tick: dict[str, Any]) -> dict[str, Any]:
    """What the MT4 price itself says: spread against the bar range, volatility now against before, and drift."""
    bid, ask = float(tick["bid"]), float(tick["ask"])
    spread = max(0.0, ask - bid)
    if len(bars) < 30:
        return {"spread": spread, "state": "data kurang"}
    ranges = [b["h"] - b["l"] for b in bars]
    recent, before = statistics.fmean(ranges[-10:]), statistics.fmean(ranges[-60:-10] or ranges[:-10])
    ratio = recent / before if before > 0 else 1.0
    change = (bars[-1]["c"] - bars[-30]["c"]) / bars[-30]["c"] * 100 if bars[-30]["c"] else 0.0
    spread_share = spread / recent if recent > 0 else 0.0
    state = "ramai" if ratio > 1.4 else "sepi" if ratio < 0.6 else "normal"
    return {"spread": round(spread, 6), "spread_vs_range": round(spread_share, 3), "volatility_ratio": round(ratio, 2),
            "state": state, "change_30_bars_pct": round(change, 3),
            "warning": "spread mahal dibanding gerak bar" if spread_share > 0.5 else ""}


_desk: NewsDesk | None = None
_desk_lock = threading.Lock()


def get_desk() -> NewsDesk:
    global _desk
    with _desk_lock:
        if _desk is None:
            _desk = NewsDesk()
        return _desk


__all__ = ["NewsDesk", "get_desk", "build", "brief", "market_condition", "parse_feed", "parse_calendar", "parse_coingecko",
           "parse_fng", "topics_of", "tone_of", "relevant_topics", "currencies_of", "TOPICS", "FEEDS"]
