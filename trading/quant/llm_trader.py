"""One LLM for trading: it takes virtual positions in the paper simulation, and the forward test can use it too.

The model is chosen once on the panel and saved in ``trading/data/llm.json``. Every call goes
through the same client as the chat, so Router-page providers, account rotation and the token
log apply, and the Router page shows what trading cost.

What the model sees each time: the symbol and session, the recent closes as percent moves, the
math (``confluence``: P(up), whether it passed its test on unseen bars, the measurements that
pushed hardest), every rule's live record in this run, its own last trades, the market
condition from the MT4 price, the news brief, and the trader's checklist (``playbook``): the
price levels around it (pivots, Fibonacci, where they meet), the market's clocks, what this
hour did before and whether a cycle passed its test, and its own record in R by condition. It answers JSON: buy, sell or none, a
confidence, and a short reason. "none" is a fine answer; the spread is paid on every trade.

Its own record goes back into the next prompt, which is how it adjusts: it sees which of its
calls lost and why it made them. It cannot change sizes, stops or risk limits; the caller does
what it says only inside its own limits.

Calls are capped per run (``max_calls``) and every call's tokens are priced with
``model.router.pricing``, so the panel shows the spend as it grows. A model that fails twice in
a row is not asked again in that run.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from trading.quant.playbook import LEVELS_AND_TIME
from utils.logger import get_logger

logger = get_logger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "llm.json"
FORWARD_MODES = ("off", "confirm", "decide")
DEFAULTS: dict[str, Any] = {"model": "", "sim_enabled": False, "max_calls": 120, "forward_mode": "off", "timeout_s": 25.0}
MAX_FAILURES = 2
MAX_TOKENS = 1200       # reasoning models spend part of this on thinking before the JSON
ATR_MULT_MIN, ATR_MULT_MAX = 0.5, 5.0     # the stop and target the model may set, in ATR

SYSTEM = (
    "You are the trading model of a paper-trading simulation on a live price. Nothing you say sends a real order. "
    "Each call you decide one virtual position that is opened now at the ask (buy) or bid (sell). It closes at the bid "
    "or ask after the hold time, or earlier at its stop or target when the facts give them, so the spread is always paid. "
    "Judge by expected profit after the spread, not by how often you are right. "
    "Style 'scalping' holds seconds on very short bars: 'none' is right when the spread is large against the bar moves. "
    "Style 'harian' is intraday trading on M15/H1/H4 bars held for hours: the spread is small against the ATR there, "
    "so mixed indicators alone are not a reason to stay out. Read the trend, the session and the news and take the "
    "side with the better expected value; say 'none' only when you see no edge at all, such as right before "
    "high-impact news. In 'harian' you may set stop_atr and target_atr (0.5-5, in ATR) instead of the defaults. "
    "The math block was graded on bars it had not seen: when it failed its test, ignore its direction and judge "
    "from the rest. Read your own last trades and change what lost. "
    "Work in this order: (1) risk: a stop and target of S and T ATR is a reward to risk of T/S, and it only breaks even "
    "when you are right more than (1 + c) / (1 + T/S) of the time, with c the spread as a share of the stop "
    "(spread_vs_atr / S); 'my_math' is your own record in R with its break-even rate and t-test, and "
    "'my_record_like_now' is how your earlier trades ended under the conditions that hold now. "
    + LEVELS_AND_TIME +
    "Answer with JSON only, no other text: "
    '{"side": "buy" | "sell" | "none", "confidence": 0.0-1.0, "why": "at most 25 words", '
    '"stop_atr": number (optional), "target_atr": number (optional)}'
)


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_PATH
    data: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw if isinstance(raw, dict) else {}
    out = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    out["model"] = out["model"] or os.getenv("TRADING_LLM_MODEL", "").strip()
    return out


def save_config(body: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_PATH
    current = load_config(path)
    if "model" in body:
        current["model"] = str(body["model"] or "").strip()[:200]
    if "sim_enabled" in body:
        current["sim_enabled"] = bool(body["sim_enabled"])
    if "max_calls" in body:
        try:
            current["max_calls"] = max(1, min(2000, int(body["max_calls"])))
        except (TypeError, ValueError) as exc:
            raise ValueError("max_calls harus angka 1 sampai 2000") from exc
    if "forward_mode" in body:
        if body["forward_mode"] not in FORWARD_MODES:
            raise ValueError(f"forward_mode: {', '.join(FORWARD_MODES)}")
        current["forward_mode"] = body["forward_mode"]
    if "timeout_s" in body:
        with contextlib.suppress(TypeError, ValueError):
            current["timeout_s"] = max(5.0, min(120.0, float(body["timeout_s"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=1), encoding="utf-8")
    return current


def default_client() -> Any:
    from config.secrets import get_secrets
    from config.settings import get_settings
    from model.connection.api_client import APIClient

    settings = get_settings()
    return APIClient(settings.api_base_url, get_secrets().api_key, int(settings.timeout_seconds))


def default_model() -> str:
    """The Router page's default model, else DEFAULT_MODEL, but only one the Router still serves."""
    return resolve_model("", list(_served["models"]))[0]


def parse_decision(text: str) -> dict[str, Any]:
    """The first JSON object in the reply, checked: side buy/sell/none, confidence 0-1, a short why."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    found = fenced.group(1) if fenced else None
    if found is None:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"balasan tanpa JSON: {text.strip()[:80]!r}")
        found = text[start:end + 1]
    data = json.loads(found)
    if not isinstance(data, dict):
        raise ValueError("JSON bukan objek")
    side = str(data.get("side", "none")).strip().lower()
    side = {"long": "buy", "short": "sell", "hold": "none", "skip": "none", "wait": "none"}.get(side, side)
    if side not in ("buy", "sell", "none"):
        raise ValueError(f"side tidak dikenal: {side}")
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    out: dict[str, Any] = {"side": side, "confidence": round(confidence, 3), "why": str(data.get("why", ""))[:200]}
    for key in ("stop_atr", "target_atr"):       # optional, used in the intraday style; a bad value is ignored
        with contextlib.suppress(TypeError, ValueError):
            value = float(data[key]) if data.get(key) is not None else None
            if value is not None and value == value:
                out[key] = round(max(ATR_MULT_MIN, min(ATR_MULT_MAX, value)), 2)
    with contextlib.suppress(TypeError, ValueError):     # trailing exit: how far the stop follows once the target is reached
        if data.get("trail_atr") is not None:
            value = float(data["trail_atr"])
            if value == value:
                out["trail_atr"] = round(max(0.2, min(5.0, value)), 2)
    with contextlib.suppress(TypeError, ValueError):     # the analyst's extras
        if data.get("plan") is not None:
            out["plan"] = int(data["plan"])
    with contextlib.suppress(TypeError, ValueError):
        if data.get("valid_bars") is not None:
            out["valid_bars"] = max(1, min(12, int(data["valid_bars"])))
    if data.get("risks"):
        out["risks"] = str(data["risks"])[:200]
    return out


def build_prompt(context: dict[str, Any]) -> str:
    """The user message: the facts, as compact JSON plus the news brief as text."""
    facts = {k: v for k, v in context.items() if k != "news_brief"}
    text = "Facts:\n" + json.dumps(facts, ensure_ascii=False, separators=(",", ":"), default=str)
    if context.get("news_brief"):
        text += "\n\nNews and market mood:\n" + str(context["news_brief"])
    return text + "\n\nDecide now. JSON only."


class LLMTrader:
    """Asks the model, counts the calls, tokens and dollars. Never raises out of ``decide``."""

    def __init__(self, model: str = "", *, max_calls: int = 120, timeout_s: float = 25.0, client: Any = None,
                 session_tag: str = "trading-llm") -> None:
        self.model = model or default_model()
        self.max_calls = max_calls
        self.timeout_s = timeout_s
        self._client = client
        self.session_tag = session_tag
        self.calls = self.failures = self.failures_in_row = 0
        self.input_tokens = self.output_tokens = self.cached_tokens = 0
        self.cost_usd = 0.0
        self.unpriced = False
        self.last_error = ""
        self.last_latency_ms = 0
        self.last: dict[str, Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.model) and self.calls < self.max_calls and self.failures_in_row < MAX_FAILURES

    @property
    def why_unavailable(self) -> str:
        if not self.model:
            return "belum ada model LLM: pilih di panel atau isi DEFAULT_MODEL"
        if self.calls >= self.max_calls:
            return f"batas {self.max_calls} panggilan tercapai"
        if self.failures_in_row >= MAX_FAILURES:
            return f"model gagal {MAX_FAILURES} kali berturut-turut: {self.last_error}"
        return ""

    def _ask(self, prompt: str, system: str = SYSTEM) -> tuple[str, dict[str, Any]]:
        from model.connection.response_handler import ResponseHandler
        from utils.workspace_context import set_active_session

        if self._client is None:
            self._client = default_client()
        if self._loop is None:       # the client's connections belong to the loop that opened them
            self._loop = asyncio.new_event_loop()

        async def call() -> Any:
            set_active_session(self.session_tag)
            result = self._client.chat_completion(model=self.model, temperature=0.2, max_tokens=MAX_TOKENS,
                                                  messages=[{"role": "system", "content": system},
                                                            {"role": "user", "content": prompt}])
            return await result if inspect.isawaitable(result) else result

        raw = self._loop.run_until_complete(asyncio.wait_for(call(), self.timeout_s))
        parsed = ResponseHandler.handle(raw)
        content = parsed.content
        if "{" not in content and parsed.thinking and "{" in parsed.thinking:   # answer cut off or left in the thinking
            content = parsed.thinking
        if not content.strip():
            raise ValueError("balasan kosong (token habis untuk berpikir?)")
        return content, dict(parsed.usage or {})

    def _count(self, usage: dict[str, Any]) -> None:
        from model.router import pricing
        from model.router.usage import normalize

        tokens = normalize(usage)
        if not tokens:
            return
        self.input_tokens += tokens["input"]
        self.output_tokens += tokens["output"]
        self.cached_tokens += tokens["cached"]
        cost = pricing.cost_of(self.model, prompt=tokens["input"], output=tokens["output"], cached=tokens["cached"],
                               cache_write=tokens["cache_write"])
        if cost is None:
            self.unpriced = True
        else:
            self.cost_usd += cost

    def decide(self, context: dict[str, Any], system: str | None = None) -> dict[str, Any] | None:
        """The model's call on these facts, or None when it is not available or failed (the reason is kept).

        ``system`` replaces the trading prompt, as the analyst does.
        """
        with self._lock:
            if not self.available:
                return None
            self.calls += 1
            started = time.monotonic()
            try:
                content, usage = self._ask(build_prompt(context), system or SYSTEM)
                self._count(usage)
                answer = parse_decision(content)
            except Exception as exc:  # noqa: BLE001 - a model failure must not stop the run
                self.failures += 1
                self.failures_in_row += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning(f"[llm-trader] {self.model}: {self.last_error}")
                return None
            finally:
                self.last_latency_ms = round((time.monotonic() - started) * 1000)
            self.failures_in_row = 0
            self.last = {**answer, "latency_ms": self.last_latency_ms, "at": time.time()}
            return dict(self.last)

    def report(self) -> dict[str, Any]:
        return {"model": self.model, "calls": self.calls, "max_calls": self.max_calls, "failures": self.failures,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "cached_tokens": self.cached_tokens,
                "cost_usd": round(self.cost_usd, 6), "unpriced": self.unpriced, "last": self.last,
                "last_error": self.last_error, "last_latency_ms": self.last_latency_ms, "available": self.available,
                "why_unavailable": self.why_unavailable}

    def close(self) -> None:
        if self._loop is not None:
            with contextlib.suppress(Exception):
                self._loop.close()
            self._loop = None


SERVED_TTL_S = 300.0
_served: dict[str, Any] = {"at": 0.0, "models": [], "groups": [], "errors": []}
_served_lock = threading.Lock()


async def served_models(*, force: bool = False, timeout_s: float = 12.0) -> dict[str, Any]:
    """The chat models the Router-page providers list right now, by provider. Cached for five minutes.

    These are the only ids a trading LLM can use: anything else (an old DEFAULT_MODEL, a model a
    provider has dropped) would be sent to the default provider and come back "not found".
    """
    from model.router import dispatch
    from model.router.store import get_store
    from model.settings.model_picker import is_chat_model

    with _served_lock:
        if not force and _served["models"] and time.time() - _served["at"] < SERVED_TTL_S:
            return dict(_served)
    providers = [p for p in get_store().providers() if p.enabled and any(a.enabled for a in p.accounts)]

    async def one(provider: Any) -> tuple[Any, list[str], str]:
        try:
            listed = await asyncio.wait_for(dispatch.list_models(provider), timeout_s)
        except Exception as exc:  # noqa: BLE001 - a dead provider leaves its models out and says why
            return provider, [], f"{provider.name}: {str(exc)[:120] or type(exc).__name__}"
        return provider, sorted({str(m["id"]) for m in listed if is_chat_model(str(m["id"]))}), ""

    results = await asyncio.gather(*(one(p) for p in providers))
    groups = [{"provider": p.id, "name": p.name, "prefix": p.prefix, "models": ids} for p, ids, _ in results if ids]
    found = {"at": time.time(), "models": [m for g in groups for m in g["models"]], "groups": groups,
             "errors": [err for _, _, err in results if err]}
    with _served_lock:
        _served.update(found)
    return dict(found)


def served_models_sync(*, force: bool = False) -> dict[str, Any]:
    """``served_models`` from plain (non-async) code, such as a FastAPI sync endpoint's thread."""
    with _served_lock:
        if not force and _served["models"] and time.time() - _served["at"] < SERVED_TTL_S:
            return dict(_served)
    return asyncio.run(served_models(force=force))


def resolve_model(wanted: str, served: list[str]) -> tuple[str, str]:
    """The model to use and, if it cannot be used, why. An empty list (providers unreachable) checks nothing."""
    wanted = (wanted or "").strip()
    if wanted:
        if served and wanted not in served:
            return wanted, (f"Model {wanted} tidak dilayani provider Router mana pun saat ini. "
                            "Pilih model dari daftar di kotak LLM trading.")
        return wanted, ""
    candidates = []
    with contextlib.suppress(Exception):
        from model.router.store import get_store
        candidates.append(str(get_store().meta("default_model", "") or ""))
    with contextlib.suppress(Exception):
        from config.settings import get_settings
        candidates.append(str(get_settings().default_model or ""))
    for candidate in candidates:
        if candidate and (not served or candidate in served):
            return candidate, ""
    stale = next((c for c in candidates if c), "")
    if stale:
        return "", f"Model default {stale} tidak dilayani Router lagi. Pilih model dari daftar di kotak LLM trading."
    return "", "Belum ada model LLM. Pilih model dari daftar di kotak LLM trading."


async def list_models(force: bool = False) -> dict[str, Any]:
    """What the panel's picker shows: every served model by provider, the ones used lately first."""
    from model.router.usage import get_usage_log

    served = await served_models(force=force)
    used = [row["model"] for row in get_usage_log().summary("30d", "model")["rows"]]
    recent = [m for m in used if m in set(served["models"])][:12]
    default, problem = resolve_model("", served["models"])
    return {"groups": served["groups"], "models": served["models"], "recent": recent, "default": default,
            "default_problem": problem, "errors": served["errors"]}


__all__ = ["LLMTrader", "load_config", "save_config", "parse_decision", "build_prompt", "list_models", "served_models",
           "served_models_sync", "resolve_model", "FORWARD_MODES", "SYSTEM", "DEFAULTS"]
