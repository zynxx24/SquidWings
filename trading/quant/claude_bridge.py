"""Claude Code as the trading model: each cycle's question goes to a folder, and a Claude Code session answers it.

Nothing runs between cycles. ``decide`` writes ``req-<n>.json`` (the facts) into ``trading/data/claude_queue``
and waits for ``ans-<n>.json``. A Claude Code session watching that folder (``watch.sh``) wakes up for each
request, writes the answer and goes idle again, so it spends tokens only while a cycle is open. If no answer comes
within ``timeout_s`` the call fails like any model failure, and the sim skips that cycle.

Select it with the model name ``claude-code``. It costs no Router tokens, so ``cost_usd`` stays 0.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from trading.quant.llm_trader import LLMTrader

MODEL = "claude-code"
QUEUE = Path(__file__).resolve().parents[1] / "data" / "claude_queue"
POLL_S = 0.5


CLOSED_AFTER_S = 600.0     # no new tick for this long: the market is closed (weekend, holiday) or the feed is down


def market_closed(symbol: str, now: float | None = None) -> bool:
    """True when the EA has sent no fresh price for the symbol lately. Unknown (no bridge here) counts as open."""
    try:
        from web.mt4_bridge import store
    except Exception:  # noqa: BLE001 - outside the trading server there is no bridge to ask
        return False
    with store.lock:
        tick = store.ticks.get(symbol)
    seen = float((tick or {}).get("seen", 0.0))
    return (now or time.time()) - seen > CLOSED_AFTER_S


class ClaudeCodeTrader(LLMTrader):
    def __init__(self, *, max_calls: int = 120, timeout_s: float = 60.0, queue: Path | None = None) -> None:
        super().__init__(MODEL, max_calls=max_calls, timeout_s=timeout_s, session_tag="trading-claude-code")
        self.queue = queue or QUEUE
        self.queue.mkdir(parents=True, exist_ok=True)
        self._n = 0

    def decide(self, context: dict[str, Any], system: str | None = None) -> dict[str, Any] | None:
        """While the market is closed nobody is woken: the cycle answers 'none' and the new bars go to the M5 store."""
        symbol = str(context.get("symbol") or "")
        if symbol and market_closed(symbol):
            from trading.quant import m5store

            m5store.sync_in_background(symbol)
            self.last = {"side": "none", "confidence": 0.0, "why": "pasar tutup: tidak ada harga baru, data disimpan untuk belajar",
                         "latency_ms": 0, "at": time.time(), "closed": True}
            return dict(self.last)
        return super().decide(context, system)

    def _ask(self, prompt: str, system: str = "") -> tuple[str, dict[str, Any]]:
        self._n += 1
        sha = hashlib.sha1(system.encode()).hexdigest()[:8]
        sys_file = self.queue / f"system-{sha}.txt"
        if not sys_file.exists():
            sys_file.write_text(system, encoding="utf-8")
        name = f"{int(time.time())}-{self._n}"
        req, ans = self.queue / f"req-{name}.json", self.queue / f"ans-{name}.json"
        tmp = req.with_suffix(".tmp")
        tmp.write_text(json.dumps({"id": name, "system_file": sys_file.name, "prompt": prompt,
                                   "answer_file": ans.name, "deadline_s": self.timeout_s}, ensure_ascii=False), encoding="utf-8")
        tmp.rename(req)                                  # the watcher only sees complete files
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if ans.exists():
                text = ans.read_text(encoding="utf-8").strip()
                if text:
                    return text, {}
            time.sleep(POLL_S)
        req.rename(req.with_suffix(".expired"))
        raise TimeoutError(f"Claude Code tidak menjawab dalam {self.timeout_s:g} detik")
