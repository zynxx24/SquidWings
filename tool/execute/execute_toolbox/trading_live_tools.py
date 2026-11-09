"""Trading risk maths (no orders, no advice), and live facts from free public APIs."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from . import _net
from .catalog import group, param, spec

group("tradecalc", "Trading risk calculators",
      "lot, lot size, ukuran lot, position size, risk, risiko, risk reward, rr, pip, pips, pip value, margin, leverage, "
      "stop loss, take profit, sl, tp, fibonacci, fibo, pivot, drawdown, money management, manajemen risiko")
group("live", "Live facts: weather, exchange rates, holidays, encyclopedia",
      "cuaca, weather, hujan, rain, suhu, temperature, prakiraan, forecast, kurs, exchange rate, nilai tukar, dollar, "
      "usd, eur, sgd, myr, jpy, valas, libur, holiday, tanggal merah, cuti bersama, wikipedia, wiki, ensiklopedia, "
      "definisi, definition, arti kata, meaning")


def _pip_size(symbol: str) -> float:
    s = symbol.upper().replace("/", "")
    if s.startswith(("XAU", "GOLD")):
        return 0.1
    if s.startswith(("XAG", "SILVER")):
        return 0.01
    if s.endswith("JPY"):
        return 0.01
    if s.startswith(("US30", "NAS", "SPX", "GER", "UK100", "BTC", "ETH")):
        return 1.0
    return 0.0001


def _contract(symbol: str) -> float:
    s = symbol.upper()
    if s.startswith(("XAU", "GOLD")):
        return 100
    if s.startswith(("XAG", "SILVER")):
        return 5000
    if s.startswith(("US30", "NAS", "SPX", "GER", "UK100", "BTC", "ETH")):
        return 1
    return 100_000


def _pip_value_per_lot(symbol: str, price: float | None, account_currency: str = "USD") -> tuple[float, bool]:
    """Pip value of one standard lot in the account currency, and whether it is exact.

    Exact when the quote currency is the account currency (EURUSD, XAUUSD on a USD account), or when the base is
    (USDJPY with its price). A cross pair like EURGBP needs a third rate, so the value is only approximate.
    """
    s = symbol.upper().replace("/", "")
    account = account_currency.upper()
    value_in_quote = _pip_size(s) * _contract(s)
    quote = s[3:6] if len(s) >= 6 and s[:3].isalpha() and s[3:6].isalpha() else account
    if quote == account:
        return value_in_quote, True
    if price and s.startswith(account):
        return value_in_quote / float(price), True
    return value_in_quote, False


@spec("position_size", "Lot size for a trade from account balance, risk % and stop-loss distance, with the money at risk "
      "and the pip value. Handles forex majors, JPY pairs, XAUUSD and indices (contract sizes vary by broker; check yours).",
      "tradecalc", [param("balance", "num", "Account balance."), param("risk_percent", "num", "e.g. 1 for 1%."),
                    param("stop_loss_pips", "num", "Distance to the stop in pips (gold: 1 pip = 0.1)."),
                    param("symbol", required=False, default="EURUSD"),
                    param("price", "num", "Current price, needed for USDJPY-style pairs.", required=False),
                    param("account_currency", required=False, default="USD")],
      keywords="lot, lot size, ukuran lot, position size, berapa lot, money management, mm, risiko per trade", lead=True)
def position_size(balance: float, risk_percent: float, stop_loss_pips: float, symbol: str = "EURUSD",
                  price: float | None = None, account_currency: str = "USD") -> dict[str, Any]:
    if float(stop_loss_pips) <= 0:
        raise ValueError("stop_loss_pips must be positive")
    risk = float(balance) * float(risk_percent) / 100
    pip_value, exact = _pip_value_per_lot(symbol, price, account_currency)
    lots = risk / (float(stop_loss_pips) * pip_value)
    lots_rounded = math.floor(lots * 100) / 100
    warn = []
    if float(risk_percent) > 2:
        warn.append("risk above 2% per trade is aggressive")
    if lots_rounded < 0.01:
        warn.append("below the 0.01 minimum lot; widen the account or tighten risk")
    if not exact:
        warn.append("pip value is approximate: give price for USD-base pairs; cross pairs need the quote currency's rate")
    return {"lots": lots_rounded, "lots_exact": round(lots, 4), "risk_amount": round(risk, 2),
            "pip_value_per_lot": round(pip_value, 4), "pip_size": _pip_size(symbol), "warnings": warn,
            "note": "contract size and pip value differ between brokers; confirm in the symbol specification"}


@spec("risk_reward", "Risk-to-reward ratio, pips to stop and target, money at risk and to gain for a lot size, and the win "
      "rate needed to break even.",
      "tradecalc", [param("entry", "num"), param("stop_loss", "num"), param("take_profit", "num"), param("symbol", required=False, default="EURUSD"),
                    param("lots", "num", required=False, default=0.1)],
      keywords="risk reward, rr, rasio, r:r, rrr, target profit, stop loss, take profit", lead=True)
def risk_reward(entry: float, stop_loss: float, take_profit: float, symbol: str = "EURUSD", lots: float = 0.1) -> dict[str, Any]:
    pip = _pip_size(symbol)
    risk_pips = abs(float(entry) - float(stop_loss)) / pip
    reward_pips = abs(float(take_profit) - float(entry)) / pip
    if risk_pips == 0:
        raise ValueError("stop loss equals entry")
    direction = "buy" if float(take_profit) > float(entry) else "sell"
    if (direction == "buy" and float(stop_loss) >= float(entry)) or (direction == "sell" and float(stop_loss) <= float(entry)):
        raise ValueError("stop loss is on the wrong side of entry for this take profit")
    ratio = reward_pips / risk_pips
    pv = _pip_value_per_lot(symbol, float(entry))[0] * float(lots)
    return {"direction": direction, "risk_pips": round(risk_pips, 1), "reward_pips": round(reward_pips, 1),
            "ratio": f"1:{ratio:.2f}", "risk_money": round(risk_pips * pv, 2), "reward_money": round(reward_pips * pv, 2),
            "breakeven_win_rate_percent": round(100 / (1 + ratio), 1)}


@spec("margin_calc", "Margin required to open a position for a lot size and leverage, and the free margin left.",
      "tradecalc", [param("lots", "num"), param("price", "num"), param("leverage", "num", "e.g. 100 for 1:100."),
                    param("symbol", required=False, default="EURUSD"), param("balance", "num", required=False)],
      keywords="margin, leverage, leverage 1:100, margin call, free margin, modal minimal")
def margin_calc(lots: float, price: float, leverage: float, symbol: str = "EURUSD", balance: float | None = None) -> dict[str, Any]:
    s = symbol.upper()
    notional = float(lots) * _contract(s) * (float(price) if not s.startswith("USD") else 1)
    margin = notional / float(leverage)
    out: dict[str, Any] = {"notional_usd": round(notional, 2), "margin_required": round(margin, 2)}
    if balance:
        out["free_margin"] = round(float(balance) - margin, 2)
        out["margin_level_percent"] = round(float(balance) / margin * 100, 1) if margin else None
    return out


@spec("fibonacci_levels", "Fibonacci retracement and extension prices between a swing high and low.",
      "tradecalc", [param("high", "num"), param("low", "num"), param("trend", enum=["up", "down"], required=False, default="up")],
      keywords="fibonacci, fibo, retracement, extension, golden ratio, 61.8, 38.2")
def fibonacci_levels(high: float, low: float, trend: str = "up") -> dict[str, Any]:
    h, lo = float(high), float(low)
    if h <= lo:
        raise ValueError("high must be above low")
    d = h - lo
    ratios = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
    if trend == "up":
        ret = {f"{r * 100:g}%": round(h - d * r, 5) for r in ratios}
        ext = {f"{r * 100:g}%": round(h + d * (r - 1), 5) for r in (1.272, 1.618, 2.0, 2.618)}
    else:
        ret = {f"{r * 100:g}%": round(lo + d * r, 5) for r in ratios}
        ext = {f"{r * 100:g}%": round(lo - d * (r - 1), 5) for r in (1.272, 1.618, 2.0, 2.618)}
    return {"retracement": ret, "extension": ext}


@spec("pivot_points", "Classic, Fibonacci, Camarilla and Woodie pivot points from the previous period's high, low and close.",
      "tradecalc", [param("high", "num"), param("low", "num"), param("close", "num"),
                    param("method", enum=["classic", "fibonacci", "camarilla", "woodie"], required=False, default="classic")],
      keywords="pivot, pivot point, support resistance, s1 r1, camarilla, woodie")
def pivot_points(high: float, low: float, close: float, method: str = "classic") -> dict[str, Any]:
    h, lo, c = float(high), float(low), float(close)
    r = h - lo
    p = (h + lo + 2 * c) / 4 if method == "woodie" else (h + lo + c) / 3
    if method == "fibonacci":
        lv = {"R3": p + r, "R2": p + 0.618 * r, "R1": p + 0.382 * r, "P": p, "S1": p - 0.382 * r, "S2": p - 0.618 * r, "S3": p - r}
    elif method == "camarilla":
        lv = {"R4": c + r * 1.1 / 2, "R3": c + r * 1.1 / 4, "R2": c + r * 1.1 / 6, "R1": c + r * 1.1 / 12, "P": p,
              "S1": c - r * 1.1 / 12, "S2": c - r * 1.1 / 6, "S3": c - r * 1.1 / 4, "S4": c - r * 1.1 / 2}
    else:
        lv = {"R3": h + 2 * (p - lo), "R2": p + r, "R1": 2 * p - lo, "P": p, "S1": 2 * p - h, "S2": p - r, "S3": lo - 2 * (h - p)}
    return {"method": method, "levels": {k: round(v, 5) for k, v in lv.items()}}


@spec("drawdown_stats", "Maximum drawdown, recovery, total return, win rate, profit factor and expectancy from an equity "
      "curve or a list of trade results.",
      "tradecalc", [param("values", "list", "Equity values over time, or per-trade profits.", items="num"),
                    param("kind", enum=["equity", "trades"], required=False, default="trades"),
                    param("starting_balance", "num", "For trades.", required=False, default=1000)],
      keywords="drawdown, dd, max drawdown, profit factor, win rate, winrate, expectancy, statistik trading, hasil trading")
def drawdown_stats(values: list[float], kind: str = "trades", starting_balance: float = 1000) -> dict[str, Any]:
    vals = [float(v) for v in values]
    if not vals:
        raise ValueError("give at least one value")
    out: dict[str, Any] = {}
    if kind == "trades":
        wins, losses = [v for v in vals if v > 0], [v for v in vals if v < 0]
        equity = [float(starting_balance)]
        for v in vals:
            equity.append(equity[-1] + v)
        out.update(trades=len(vals), win_rate_percent=round(100 * len(wins) / len(vals), 1),
                   profit_factor=round(sum(wins) / -sum(losses), 2) if losses else None,
                   expectancy=round(sum(vals) / len(vals), 4), average_win=round(sum(wins) / len(wins), 2) if wins else 0,
                   average_loss=round(sum(losses) / len(losses), 2) if losses else 0, net=round(sum(vals), 2))
        streak = worst = 0
        for v in vals:
            streak = streak + 1 if v < 0 else 0
            worst = max(worst, streak)
        out["max_losing_streak"] = worst
    else:
        equity = vals
    peak, max_dd, max_dd_pct = equity[0], 0.0, 0.0
    for e in equity:
        peak = max(peak, e)
        dd = peak - e
        if dd > max_dd:
            max_dd, max_dd_pct = dd, (dd / peak * 100 if peak else 0)
    out.update(max_drawdown=round(max_dd, 2), max_drawdown_percent=round(max_dd_pct, 2),
               total_return_percent=round((equity[-1] / equity[0] - 1) * 100, 2) if equity[0] else None,
               final_equity=round(equity[-1], 2))
    return out


@spec("pip_converter", "Convert between price difference, pips and points (5-digit brokers: 1 pip = 10 points) for a symbol.",
      "tradecalc", [param("value", "num"), param("from_unit", enum=["price", "pips", "points"]), param("symbol", required=False, default="EURUSD")],
      keywords="pip, pips, point, poin, berapa pip, selisih harga")
def pip_converter(value: float, from_unit: str, symbol: str = "EURUSD") -> dict[str, Any]:
    pip = _pip_size(symbol)
    price = float(value) if from_unit == "price" else float(value) * pip if from_unit == "pips" else float(value) * pip / 10
    return {"price_difference": round(price, 6), "pips": round(price / pip, 2), "points": round(price / pip * 10, 1), "pip_size": pip}


# ── Live public data ──────────────────────────────────────────────────────────
_WMO = {0: "cerah", 1: "cerah berawan", 2: "berawan sebagian", 3: "mendung", 45: "berkabut", 48: "kabut beku",
        51: "gerimis ringan", 53: "gerimis", 55: "gerimis lebat", 61: "hujan ringan", 63: "hujan sedang", 65: "hujan lebat",
        80: "hujan lokal ringan", 81: "hujan lokal", 82: "hujan lokal lebat", 95: "badai petir", 96: "badai petir + es",
        99: "badai petir hebat"}


@spec("weather", "Current weather and a daily forecast (up to 16 days) for a place name or coordinates, from Open-Meteo "
      "(free, no key): temperature, rain chance and amount, wind, UV. Needs internet.",
      "live", [param("place", description="City or 'lat,lon'."), param("days", "int", required=False, default=3)],
      keywords="cuaca, weather, hujan, rain, prakiraan cuaca, forecast, suhu hari ini, panas, mendung", lead=True, network=True)
async def weather(place: str, days: int = 3) -> dict[str, Any]:
    from .geo_maps_tools import parse_point

    try:
        lat, lon = parse_point(place)
        label = place
    except ValueError:
        geo = await _net.get_json("geocoding-api.open-meteo.com", "/v1/search", {"name": place, "count": 1, "language": "id"})
        hits = geo.get("results") or []
        if not hits:
            raise ValueError(f"place '{place}' not found") from None
        lat, lon = hits[0]["latitude"], hits[0]["longitude"]
        label = ", ".join(filter(None, [hits[0].get("name"), hits[0].get("admin1"), hits[0].get("country")]))
    data = await _net.get_json("api.open-meteo.com", "/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": max(1, min(int(days), 16)),
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,uv_index_max"})
    cur = data.get("current", {})
    daily = data.get("daily", {})
    forecast = [{"date": d, "condition": _WMO.get(code, str(code)), "min_c": tmin, "max_c": tmax, "rain_mm": rain,
                 "rain_chance_percent": chance, "uv_max": uv}
                for d, code, tmin, tmax, rain, chance, uv in zip(
                    daily.get("time", []), daily.get("weather_code", []), daily.get("temperature_2m_min", []),
                    daily.get("temperature_2m_max", []), daily.get("precipitation_sum", []),
                    daily.get("precipitation_probability_max", []), daily.get("uv_index_max", []), strict=False)]
    return {"place": label, "lat": lat, "lon": lon, "timezone": data.get("timezone"),
            "now": {"temp_c": cur.get("temperature_2m"), "feels_like_c": cur.get("apparent_temperature"),
                    "humidity_percent": cur.get("relative_humidity_2m"), "wind_kmh": cur.get("wind_speed_10m"),
                    "condition": _WMO.get(cur.get("weather_code"), str(cur.get("weather_code")))},
            "forecast": forecast, "source": "Open-Meteo"}


@spec("currency_convert", "Convert money between currencies at the latest European Central Bank reference rates "
      "(Frankfurter, free; updated on working days). Rupiah (IDR) included. Optionally on a past date. Needs internet.",
      "live", [param("amount", "num"), param("from_currency", description="e.g. USD."), param("to_currency", "list", "e.g. ['IDR', 'EUR'].", items="str"),
               param("on_date", description="YYYY-MM-DD for a historical rate.", required=False)],
      keywords="kurs, exchange rate, nilai tukar, konversi mata uang, currency, dollar ke rupiah, usd to idr, valas, rate hari ini",
      lead=True, network=True)
async def currency_convert(amount: float, from_currency: str, to_currency: list[str], on_date: str | None = None) -> dict[str, Any]:
    targets = [t.upper() for t in (to_currency if isinstance(to_currency, list) else [to_currency])]
    src = from_currency.upper()
    path = f"/{on_date}" if on_date else "/latest"
    data = await _net.get_json("api.frankfurter.app", path, {"from": src, "to": ",".join(t for t in targets if t != src)})
    rates = data.get("rates", {})
    if src in targets:
        rates[src] = 1.0
    return {"amount": float(amount), "from": src, "date": data.get("date"),
            "converted": {t: round(float(amount) * float(r), 4) for t, r in rates.items()},
            "rates": rates, "source": "ECB via Frankfurter (reference rates, not a bank's buy/sell price)"}


@spec("public_holidays", "Public holidays for a country and year (Indonesia by default) from Nager.Date, and which are still "
      "ahead. Indonesian cuti bersama is decided yearly by SKB and may be missing. Needs internet.",
      "live", [param("year", "int", required=False), param("country", required=False, default="ID")],
      keywords="libur nasional, hari libur, tanggal merah, holiday, public holiday, cuti bersama, long weekend, harpitnas",
      lead=True, network=True)
async def public_holidays(year: int | None = None, country: str = "ID") -> dict[str, Any]:
    y = int(year or date.today().year)
    data = await _net.get_json("date.nager.at", f"/api/v3/PublicHolidays/{y}/{country.upper()}")
    today = date.today().isoformat()
    items = [{"date": h["date"], "name": h.get("localName") or h.get("name"), "english": h.get("name"),
              "weekday": date.fromisoformat(h["date"]).strftime("%A")} for h in data]
    return {"year": y, "country": country.upper(), "holidays": items, "upcoming": [h for h in items if h["date"] >= today][:5],
            "long_weekends": [h for h in items if h["weekday"] in ("Monday", "Friday")],
            "note": "check the official SKB for cuti bersama"}


@spec("wiki_summary", "Short encyclopedia summary of a topic from Wikipedia (Indonesian first, English fallback), with the "
      "page link. Good for definitions and quick facts. Needs internet.",
      "live", [param("topic"), param("language", enum=["id", "en"], required=False, default="id")],
      keywords="wikipedia, wiki, ensiklopedia, encyclopedia, siapa itu, who is, apa itu, what is, sejarah singkat", lead=True,
      network=True)
async def wiki_summary(topic: str, language: str = "id") -> dict[str, Any]:
    from urllib.parse import quote

    for lang in dict.fromkeys([language, "en"]):
        host = f"{lang}.wikipedia.org"
        try:
            data = await _net.get_json(host, f"/api/rest_v1/page/summary/{quote(topic.strip().replace(' ', '_'))}")
        except _net.NetError:
            continue
        if data.get("type") == "disambiguation":
            return {"topic": topic, "disambiguation": True, "summary": data.get("extract"), "language": lang,
                    "url": data.get("content_urls", {}).get("desktop", {}).get("page")}
        if data.get("extract"):
            return {"title": data.get("title"), "summary": data.get("extract"), "description": data.get("description"),
                    "language": lang, "url": data.get("content_urls", {}).get("desktop", {}).get("page")}
    raise ValueError(f"no Wikipedia page found for '{topic}'; try web_research")


@spec("dictionary_en", "English word definitions, part of speech, examples, synonyms and pronunciation (Free Dictionary API). "
      "Needs internet.",
      "live", [param("word")], keywords="arti kata inggris, meaning of, definition of, kamus inggris, english dictionary, synonym, sinonim inggris",
      network=True)
async def dictionary_en(word: str) -> dict[str, Any]:
    from urllib.parse import quote

    data = await _net.get_json("api.dictionaryapi.dev", f"/api/v2/entries/en/{quote(word.strip().lower())}")
    if not isinstance(data, list) or not data:
        raise ValueError(f"no entry for '{word}'")
    entry = data[0]
    meanings = []
    for m in entry.get("meanings", [])[:4]:
        meanings.append({"part_of_speech": m.get("partOfSpeech"),
                         "definitions": [d.get("definition") for d in m.get("definitions", [])[:3]],
                         "examples": [d.get("example") for d in m.get("definitions", []) if d.get("example")][:2],
                         "synonyms": m.get("synonyms", [])[:8]})
    return {"word": entry.get("word"), "phonetic": entry.get("phonetic"), "meanings": meanings}
