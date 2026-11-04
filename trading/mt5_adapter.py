"""MetaTrader 5 access for the trading panel, behind one interface with two backends.

* ``Mt5Backend`` talks to the MetaTrader 5 terminal through the official ``MetaTrader5``
  package. That package only runs on Windows, and the terminal must be installed and
  running there. It is imported lazily, so the rest of the app works without it.
* ``SimBackend`` is a deterministic offline market for trying the panel and for tests.
  It never touches a broker and fills orders at the simulated price.

Nothing in this module decides *whether* an order may go out. That is the job of
``trading.mt5_rules`` and the confirm flow in ``web.mt5_api``. This module only sends
what it is given.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random
import time
from typing import Any, Protocol

TIMEFRAMES: dict[str, str] = {"M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30", "H1": "H1", "H4": "H4", "D1": "D1"}
SIDES = ("buy", "sell")


class TradingError(RuntimeError):
    """A broker or connection problem the user should see as a message."""


class Backend(Protocol):
    name: str

    def connect(self, login: int, password: str, server: str, path: str | None) -> dict[str, Any]: ...
    def disconnect(self) -> None: ...
    def account(self) -> dict[str, Any]: ...
    def symbols(self, query: str = "", limit: int = 200) -> list[str]: ...
    def symbol_spec(self, symbol: str) -> dict[str, Any]: ...
    def tick(self, symbol: str) -> dict[str, Any]: ...
    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def orders(self) -> list[dict[str, Any]]: ...
    def history(self, days: int) -> list[dict[str, Any]]: ...
    def check(self, order: dict[str, Any]) -> dict[str, Any]: ...
    def send(self, order: dict[str, Any]) -> dict[str, Any]: ...
    def close(self, ticket: int, volume: float) -> dict[str, Any]: ...
    def risk_per_lot(self, symbol: str, side: str, entry: float, sl: float) -> float: ...
    def modify(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]: ...
    def cancel(self, ticket: int) -> dict[str, Any]: ...


# ---------------------------------------------------------------- MetaTrader 5


class Mt5Backend:
    """The real terminal. Needs Windows, the MT5 terminal, and the ``MetaTrader5`` package."""

    name = "mt5"

    def __init__(self) -> None:
        try:
            import MetaTrader5 as mt5  # noqa: N813
        except ImportError as exc:  # pragma: no cover - depends on the platform
            raise TradingError(
                "The MetaTrader5 package is not installed. It runs on Windows only: "
                "install the MT5 terminal there and run `pip install MetaTrader5`."
            ) from exc
        self._mt5 = mt5
        self._deviation = 20

    def _need(self) -> Any:
        return self._mt5

    def connect(self, login: int, password: str, server: str, path: str | None) -> dict[str, Any]:
        mt5 = self._need()
        kwargs: dict[str, Any] = {"login": login, "password": password, "server": server, "timeout": 30000}
        if path:
            kwargs["path"] = path
        if not mt5.initialize(**kwargs):
            raise TradingError(f"MT5 login failed: {mt5.last_error()}")
        return self.account()

    def disconnect(self) -> None:
        self._mt5.shutdown()

    def account(self) -> dict[str, Any]:
        info = self._need().account_info()
        if info is None:
            raise TradingError(f"No account information: {self._need().last_error()}")
        d = info._asdict()
        return {
            "login": d["login"], "server": d["server"], "name": d.get("name", ""), "currency": d["currency"],
            "balance": d["balance"], "equity": d["equity"], "margin": d["margin"],
            "free_margin": d["margin_free"], "profit": d["profit"], "leverage": d["leverage"],
            "live": d["trade_mode"] == 2,   # ACCOUNT_TRADE_MODE_REAL
        }

    def symbols(self, query: str = "", limit: int = 200) -> list[str]:
        mt5 = self._need()
        found = mt5.symbols_get(f"*{query}*" if query else None) or []
        return [s.name for s in found if getattr(s, "visible", True)][:limit]

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        mt5 = self._need()
        mt5.symbol_select(symbol, True)
        s = mt5.symbol_info(symbol)
        if s is None:
            raise TradingError(f"Unknown symbol {symbol}")
        # The broker's minimum distance from the price for a stop or a target, in price units.
        stops_level = float(getattr(s, "trade_stops_level", 0) or 0) * float(s.point)
        return {
            "symbol": symbol, "digits": s.digits, "point": s.point, "volume_min": s.volume_min,
            "volume_max": s.volume_max, "volume_step": s.volume_step, "contract_size": s.trade_contract_size,
            "description": getattr(s, "description", ""), "stops_level": stops_level,
            "filling": self._filling(symbol),
        }

    def _filling(self, symbol: str) -> int:
        """Fill policy the symbol allows. Brokers differ: a fixed IOC is refused by some of them."""
        mt5 = self._need()
        info = mt5.symbol_info(symbol)
        mask = int(getattr(info, "filling_mode", 0) or 0) if info is not None else 0
        if mask & 2:          # SYMBOL_FILLING_IOC
            return int(mt5.ORDER_FILLING_IOC)
        if mask & 1:          # SYMBOL_FILLING_FOK
            return int(mt5.ORDER_FILLING_FOK)
        return int(mt5.ORDER_FILLING_RETURN)

    def tick(self, symbol: str) -> dict[str, Any]:
        t = self._need().symbol_info_tick(symbol)
        if t is None:
            raise TradingError(f"No price for {symbol}")
        return {"symbol": symbol, "bid": t.bid, "ask": t.ask, "time": int(t.time)}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        mt5 = self._need()
        tf = getattr(mt5, f"TIMEFRAME_{TIMEFRAMES[timeframe]}")
        arr = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if arr is None:
            raise TradingError(f"No history for {symbol} {timeframe}: {mt5.last_error()}")
        return [
            {"t": int(r["time"]), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
             "c": float(r["close"]), "v": int(r["tick_volume"])}
            for r in arr
        ]

    def positions(self) -> list[dict[str, Any]]:
        out = []
        for p in self._need().positions_get() or []:
            out.append({
                "ticket": p.ticket, "symbol": p.symbol, "side": "buy" if p.type == 0 else "sell",
                "volume": p.volume, "open_price": p.price_open, "price": p.price_current,
                "sl": p.sl or None, "tp": p.tp or None, "profit": p.profit, "swap": p.swap,
                "time": int(p.time), "comment": p.comment,
            })
        return out

    def orders(self) -> list[dict[str, Any]]:
        out = []
        for o in self._need().orders_get() or []:
            out.append({
                "ticket": o.ticket, "symbol": o.symbol, "side": "buy" if o.type in (0, 2, 4) else "sell",
                "volume": o.volume_current, "price": o.price_open, "sl": o.sl or None, "tp": o.tp or None,
                "time": int(o.time_setup), "comment": o.comment,
            })
        return out

    def history(self, days: int) -> list[dict[str, Any]]:
        mt5 = self._need()
        now = dt.datetime.now()
        deals = mt5.history_deals_get(now - dt.timedelta(days=days), now) or []
        return [
            {"ticket": d.ticket, "symbol": d.symbol, "side": "buy" if d.type == 0 else "sell",
             "volume": d.volume, "price": d.price, "profit": d.profit, "time": int(d.time), "comment": d.comment}
            for d in deals if d.symbol
        ]

    def _request(self, order: dict[str, Any]) -> dict[str, Any]:
        mt5 = self._need()
        tick = mt5.symbol_info_tick(order["symbol"])
        if tick is None:
            raise TradingError(f"No price for {order['symbol']}")
        is_buy = order["side"] == "buy"
        req: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": order["symbol"], "volume": float(order["volume"]),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if is_buy else tick.bid, "deviation": self._deviation,
            "magic": 20261104, "comment": (order.get("comment") or "")[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(order["symbol"]),
        }
        if order.get("sl"):
            req["sl"] = float(order["sl"])
        if order.get("tp"):
            req["tp"] = float(order["tp"])
        return req

    @staticmethod
    def _result(r: Any) -> dict[str, Any]:
        if r is None:
            return {"ok": False, "retcode": None, "comment": "no response from the terminal"}
        return {"ok": r.retcode == 10009, "retcode": r.retcode, "comment": r.comment,
                "order": r.order, "deal": r.deal, "price": r.price, "volume": r.volume}

    def check(self, order: dict[str, Any]) -> dict[str, Any]:
        mt5 = self._need()
        r = mt5.order_check(self._request(order))
        res = self._result(r)
        if r is not None:
            res.update({"margin": r.margin, "balance_after": r.balance, "equity_after": r.equity})
        return res

    def send(self, order: dict[str, Any]) -> dict[str, Any]:
        return self._result(self._need().order_send(self._request(order)))

    def risk_per_lot(self, symbol: str, side: str, entry: float, sl: float) -> float:
        """Money the account loses per 1 lot if the stop is hit, from the broker's own calculation."""
        mt5 = self._need()
        otype = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        profit = mt5.order_calc_profit(otype, symbol, 1.0, entry, sl)
        if profit is None:
            raise TradingError(f"order_calc_profit failed for {symbol}: {mt5.last_error()}")
        return float(abs(profit))

    def close(self, ticket: int, volume: float) -> dict[str, Any]:
        mt5 = self._need()
        pos = next((p for p in mt5.positions_get(ticket=ticket) or []), None)
        if pos is None:
            raise TradingError(f"Position {ticket} is not open")
        is_buy = pos.type == 0
        tick = mt5.symbol_info_tick(pos.symbol)
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "position": ticket, "volume": float(volume),
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_buy else tick.ask, "deviation": self._deviation, "magic": 20261104,
            "comment": "close", "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(pos.symbol),
        }
        return self._result(mt5.order_send(req))

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        """Change the stop loss and take profit of an open position. None removes that level."""
        mt5 = self._need()
        pos = next((p for p in mt5.positions_get(ticket=ticket) or []), None)
        if pos is None:
            raise TradingError(f"Position {ticket} is not open")
        req = {
            "action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol, "position": ticket,
            "sl": float(sl) if sl else 0.0, "tp": float(tp) if tp else 0.0,
        }
        return self._result(mt5.order_send(req))

    def cancel(self, ticket: int) -> dict[str, Any]:
        """Remove a pending order that has not filled yet."""
        mt5 = self._need()
        if not any(o.ticket == ticket for o in mt5.orders_get() or []):
            raise TradingError(f"Order {ticket} is not pending")
        return self._result(mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}))


# ---------------------------------------------------------------- simulation


_SIM_START = {"EURUSD": 1.0850, "GBPUSD": 1.2700, "USDJPY": 151.20, "XAUUSD": 2350.0, "BTCUSD": 64000.0}
_SIM_DIGITS = {"EURUSD": 5, "GBPUSD": 5, "USDJPY": 3, "XAUUSD": 2, "BTCUSD": 2}


class SimBackend:
    """A deterministic offline market. Prices come from a seeded random walk per symbol,
    so the same request always gives the same candles. No orders reach any broker."""

    name = "sim"

    def __init__(self) -> None:
        self._balance = 10_000.0
        self._login = 0
        self._positions: list[dict[str, Any]] = []
        self._deals: list[dict[str, Any]] = []
        self._next = 1000
        self._connected = False

    def _price(self, symbol: str, t: int, step_seconds: int = 60) -> float:
        base = _SIM_START.get(symbol, 100.0)
        n = t // step_seconds
        rng = random.Random(f"{symbol}:{n}")
        drift = 0.0
        for k in range(0, 48):   # a bounded walk: the last 48 bars shape the price
            drift += random.Random(f"{symbol}:{n - k}").uniform(-1, 1) * base * 0.0004
        return round(base + drift + rng.uniform(-1, 1) * base * 0.0001, _SIM_DIGITS.get(symbol, 4))

    def connect(self, login: int, password: str, server: str, path: str | None) -> dict[str, Any]:
        self._login = int(login) or 1
        self._connected = True
        return self.account()

    def disconnect(self) -> None:
        self._connected = False

    def account(self) -> dict[str, Any]:
        profit = sum(self._pl(p) for p in self._positions)
        return {
            "login": self._login, "server": "simulation", "name": "Simulasi", "currency": "USD",
            "balance": round(self._balance, 2), "equity": round(self._balance + profit, 2),
            "margin": 0.0, "free_margin": round(self._balance + profit, 2), "profit": round(profit, 2),
            "leverage": 100, "live": False,
        }

    def symbols(self, query: str = "", limit: int = 200) -> list[str]:
        names = sorted(_SIM_START)
        return [s for s in names if query.upper() in s][:limit]

    def symbol_spec(self, symbol: str) -> dict[str, Any]:
        if symbol not in _SIM_START:
            raise TradingError(f"Unknown symbol {symbol}")
        d = _SIM_DIGITS[symbol]
        return {"symbol": symbol, "digits": d, "point": 10 ** -d, "volume_min": 0.01, "volume_max": 100.0,
                "volume_step": 0.01, "contract_size": 100_000.0 if symbol != "XAUUSD" else 100.0,
                "description": "simulated", "stops_level": 0.0, "filling": None}

    def tick(self, symbol: str) -> dict[str, Any]:
        self.symbol_spec(symbol)
        now = int(time.time())
        mid = self._price(symbol, now)
        spread = 10 ** -_SIM_DIGITS[symbol] * 12
        return {"symbol": symbol, "bid": round(mid - spread / 2, _SIM_DIGITS[symbol]),
                "ask": round(mid + spread / 2, _SIM_DIGITS[symbol]), "time": now}

    def rates(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        self.symbol_spec(symbol)
        step = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}[timeframe]
        end = int(time.time()) // step * step
        bars = []
        for i in range(count, 0, -1):
            t = end - i * step
            o = self._price(symbol, t, step)
            c = self._price(symbol, t + step, step)
            wick = abs(c - o) * 0.6 + 10 ** -_SIM_DIGITS[symbol] * 3
            bars.append({"t": t, "o": o, "h": round(max(o, c) + wick, _SIM_DIGITS[symbol]),
                         "l": round(min(o, c) - wick, _SIM_DIGITS[symbol]), "c": c,
                         "v": 100 + (t // step) % 400})
        return bars

    def _pl(self, p: dict[str, Any]) -> float:
        spec = self.symbol_spec(p["symbol"])
        now = self.tick(p["symbol"])
        price = now["bid"] if p["side"] == "buy" else now["ask"]
        direction = 1 if p["side"] == "buy" else -1
        return float((price - p["open_price"]) * direction * p["volume"] * spec["contract_size"])

    def positions(self) -> list[dict[str, Any]]:
        out = []
        for p in self._positions:
            q = dict(p)
            q["price"] = self.tick(p["symbol"])["bid" if p["side"] == "buy" else "ask"]
            q["profit"] = round(self._pl(p), 2)
            out.append(q)
        return out

    def orders(self) -> list[dict[str, Any]]:
        return []

    def history(self, days: int) -> list[dict[str, Any]]:
        return list(self._deals)

    def check(self, order: dict[str, Any]) -> dict[str, Any]:
        self.symbol_spec(order["symbol"])
        return {"ok": True, "retcode": 0, "comment": "simulation check", "margin": 0.0}

    def send(self, order: dict[str, Any]) -> dict[str, Any]:
        self.symbol_spec(order["symbol"])
        tick = self.tick(order["symbol"])
        price = tick["ask"] if order["side"] == "buy" else tick["bid"]
        self._next += 1
        pos = {"ticket": self._next, "symbol": order["symbol"], "side": order["side"],
               "volume": float(order["volume"]), "open_price": price, "sl": order.get("sl") or None,
               "tp": order.get("tp") or None, "swap": 0.0, "time": int(time.time()),
               "comment": order.get("comment") or ""}
        self._positions.append(pos)
        self._deals.append({"ticket": self._next, "symbol": pos["symbol"], "side": pos["side"],
                            "volume": pos["volume"], "price": price, "profit": 0.0, "time": pos["time"],
                            "comment": "open"})
        return {"ok": True, "retcode": 10009, "comment": "simulated fill", "order": self._next,
                "deal": self._next, "price": price, "volume": pos["volume"]}

    def risk_per_lot(self, symbol: str, side: str, entry: float, sl: float) -> float:
        # Approximation for the simulator: quote currency taken as the account currency.
        return float(self.symbol_spec(symbol)["contract_size"]) * abs(entry - sl)

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> dict[str, Any]:
        pos = next((p for p in self._positions if p["ticket"] == ticket), None)
        if pos is None:
            raise TradingError(f"Position {ticket} is not open")
        pos["sl"] = float(sl) if sl else None
        pos["tp"] = float(tp) if tp else None
        return {"ok": True, "retcode": 10009, "comment": "simulated modify", "deal": ticket}

    def cancel(self, ticket: int) -> dict[str, Any]:
        raise TradingError("The simulator has no pending orders to cancel.")

    def close(self, ticket: int, volume: float) -> dict[str, Any]:
        pos = next((p for p in self._positions if p["ticket"] == ticket), None)
        if pos is None:
            raise TradingError(f"Position {ticket} is not open")
        tick = self.tick(pos["symbol"])
        price = tick["bid"] if pos["side"] == "buy" else tick["ask"]
        pl = round(self._pl(pos), 2)
        self._balance += pl
        self._positions.remove(pos)
        self._deals.append({"ticket": ticket, "symbol": pos["symbol"], "side": "sell" if pos["side"] == "buy" else "buy",
                            "volume": pos["volume"], "price": price, "profit": pl, "time": int(time.time()),
                            "comment": "close"})
        return {"ok": True, "retcode": 10009, "comment": "simulated close", "deal": ticket, "price": price,
                "volume": pos["volume"], "profit": pl}


def make_backend(name: str) -> Backend:
    if name == "sim":
        return SimBackend()
    if name == "mt5":
        return Mt5Backend()
    if name == "mt4":
        from trading.mt4_backend import Mt4BridgeBackend  # lazy: it needs the web bridge
        return Mt4BridgeBackend()
    raise TradingError(f"Unknown backend '{name}'")


def fingerprint(payload: dict[str, Any]) -> str:
    """Stable id of an order preview. The confirm step must send back the same one."""
    raw = "|".join(f"{k}={payload[k]}" for k in sorted(payload))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


__all__ = ["Backend", "Mt5Backend", "SimBackend", "TradingError", "make_backend", "fingerprint", "SIDES", "TIMEFRAMES"]
