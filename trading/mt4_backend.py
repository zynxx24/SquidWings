"""MT4 backend: the same interface as the MT5 backends, served by the EA bridge.

Prices, bars, account and positions come from what the EA last sent. Orders are queued
for the EA and wait for its answer. The EA runs the order in the terminal, so the
terminal's own rules (margin, stop levels, market hours) still apply.
"""

from __future__ import annotations

import time
from typing import Any

from trading.mt5_adapter import TradingError
from web.mt4_bridge import EA_TIMEOUT, RESULT_WAIT, store


def _clean(text: str) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ").replace("\r", " ")[:31]


class Mt4BridgeBackend:
    name = "mt4"

    def _live_or_fail(self) -> None:
        if not store.alive():
            raise TradingError(
                "EA MT4 belum terhubung (tidak ada kiriman dalam "
                f"{EA_TIMEOUT} detik). Pasang Mt4Bridge di chart dan cek MT4_BRIDGE_SECRET."
            )

    def connect(self, login: int, password: str, server: str, path: str | None) -> dict[str, Any]:
        # The terminal is already logged in; the EA reports which account it runs on.
        self._live_or_fail()
        account = self.account()
        if login and int(login) != int(account["login"]):
            raise TradingError(f"EA berjalan di akun {account['login']}, bukan {login}.")
        return account

    def disconnect(self) -> None:
        return None

    def account(self) -> dict[str, Any]:
        self._live_or_fail()
        a = dict(store.account or {})
        return {
            "login": a.get("login"), "server": a.get("server", ""), "name": "", "currency": a.get("currency", ""),
            "balance": a.get("balance", 0.0), "equity": a.get("equity", 0.0), "margin": a.get("margin", 0.0),
            "free_margin": a.get("free_margin", 0.0), "profit": a.get("profit", 0.0),
            "leverage": a.get("leverage", 0), "live": bool(a.get("live", False)),
        }

    def symbols(self, query: str = "", limit: int = 200) -> list[str]:
        self._live_or_fail()
        with store.lock:
            names = sorted(store.ticks)
        return [s for s in names if query.upper() in s.upper()][:limit]

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        self._live_or_fail()
        with store.lock:
            t = store.ticks.get(symbol)
        if t is None:
            raise TradingError(f"The EA does not stream {symbol}. Add it to SymbolsCsv in the EA.")
        return {"symbol": symbol, "digits": int(t["digits"]), "point": float(t["point"]),
                "volume_min": float(t["volume_min"]), "volume_max": float(t["volume_max"]),
                "volume_step": float(t["volume_step"]), "contract_size": float(t["contract_size"]),
                "description": ""}

    def tick(self, symbol: str) -> dict[str, Any]:
        self._live_or_fail()
        with store.lock:
            t = store.ticks.get(symbol)
        if t is None:
            raise TradingError(f"No price for {symbol} from the EA.")
        return {"symbol": symbol, "bid": float(t["bid"]), "ask": float(t["ask"]), "time": int(t["time"])}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        self._live_or_fail()
        if timeframe not in ("M1", "M5"):
            raise TradingError("The MT4 bridge streams M1 and M5 bars. The panel builds the higher timeframes.")
        with store.lock:
            bars = list((store.bars_m1 if timeframe == "M1" else store.bars).get(symbol, []))
        if not bars:
            raise TradingError(f"No bars for {symbol} from the EA yet.")
        return [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]} for b in bars[-count:]]

    def positions(self) -> list[dict[str, Any]]:
        self._live_or_fail()
        out = []
        for p in store.positions:
            out.append({
                "ticket": int(p["ticket"]), "symbol": p["symbol"], "side": p["side"], "volume": float(p["volume"]),
                "open_price": float(p["open_price"]), "price": float(p["price"]),
                "sl": float(p["sl"]) or None, "tp": float(p["tp"]) or None,
                "profit": float(p["profit"]), "swap": float(p.get("swap", 0)), "time": int(p["time"]),
                "comment": p.get("comment", ""),
            })
        return out

    def orders(self) -> list[dict[str, Any]]:
        self._live_or_fail()
        with store.lock:
            items = list(store.orders)
        return [{"ticket": int(o["ticket"]), "symbol": o["symbol"], "side": o["side"], "volume": float(o["volume"]),
                 "price": float(o["price"]), "sl": float(o["sl"]) or None, "tp": float(o["tp"]) or None,
                 "time": int(o["time"]), "comment": o.get("comment", "")} for o in items]

    def history(self, days: int) -> list[dict[str, Any]]:
        self._live_or_fail()
        with store.lock:
            items = list(store.deals)
        since = time.time() - days * 86400
        deals = [{"ticket": int(d["ticket"]), "symbol": d["symbol"], "side": d["side"], "volume": float(d["volume"]),
                  "price": float(d["price"]), "profit": float(d["profit"]), "time": int(d["time"]),
                  "comment": d.get("comment", "")} for d in items]
        return [d for d in deals if d["time"] >= since]

    def check(self, order: dict[str, Any]) -> dict[str, Any]:
        self._live_or_fail()
        return {"ok": True, "retcode": 0, "comment": "checked by the EA when it sends", "margin": 0.0}

    def send(self, order: dict[str, Any]) -> dict[str, Any]:
        self._live_or_fail()
        cid = store.enqueue({"action": "order", "symbol": order["symbol"], "side": order["side"],
                             "volume": order["volume"], "sl": order.get("sl") or "", "tp": order.get("tp") or "",
                             "comment": _clean(order.get("comment")), "ticket": ""})
        return self._outcome(cid)

    def close(self, ticket: int, volume: float) -> dict[str, Any]:
        self._live_or_fail()
        cid = store.enqueue({"action": "close", "symbol": "", "side": "", "volume": volume,
                             "sl": "", "tp": "", "comment": "", "ticket": ticket})
        return self._outcome(cid)

    def risk_per_lot(self, symbol: str, side: str, entry: float, sl: float) -> float:
        with store.lock:
            t = dict(store.ticks.get(symbol) or {})
        tick_value, tick_size = float(t.get("tick_value") or 0), float(t.get("tick_size") or 0)
        if tick_value > 0 and tick_size > 0:      # already in the account currency, so USDCHF is right too
            return abs(entry - sl) / tick_size * tick_value
        # An older EA sends no tick value: this assumes the quote currency is the account currency.
        return self.symbol_spec(symbol)["contract_size"] * abs(entry - sl)

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        self._live_or_fail()
        cid = store.enqueue({"action": "modify", "symbol": "", "side": "", "volume": "", "sl": sl or "", "tp": tp or "",
                             "comment": "", "ticket": ticket})
        return self._outcome(cid)

    def cancel(self, ticket: int) -> dict[str, Any]:
        self._live_or_fail()
        cid = store.enqueue({"action": "cancel", "symbol": "", "side": "", "volume": "", "sl": "", "tp": "",
                             "comment": "", "ticket": ticket})
        return self._outcome(cid)

    @staticmethod
    def _outcome(cid: str) -> dict[str, Any]:
        text = store.wait_result(cid, RESULT_WAIT)
        if text is None:
            return {"ok": False, "retcode": None, "comment": f"The EA did not answer within {RESULT_WAIT} seconds."}
        ok = text.startswith("ok:")
        ticket = int(text.split(":", 1)[1]) if ok and text.split(":", 1)[1].isdigit() else None
        return {"ok": ok, "retcode": 10009 if ok else None, "comment": text, "order": ticket, "deal": ticket}
