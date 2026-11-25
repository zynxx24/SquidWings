"""Answers that keep running when the browser goes away.

On a phone the page loses its WebSocket whenever the screen turns off or another app comes to
the front. A turn used to be cancelled then, so a question asked before lunch had no answer after
it. Now every turn runs as its own task, independent of any connection:

* everything the turn sends (text chunks, thinking, tool steps, the final ``done``) is recorded,
* any number of connections can follow it; a new one first gets the record replayed,
* when nobody is connected the turn simply keeps going and its answer is saved to the chat,
* only the stop button (``{"type": "stop"}``) cancels it.

A finished turn stays here for ``KEEP_DONE_S`` seconds, so a page that reconnects just after the
end still receives the ending instead of a half-finished answer.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

KEEP_DONE_S = 300.0


@dataclass
class LiveTurn:
    session_id: str
    message: str = ""
    started: float = field(default_factory=time.time)
    log: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)
    task: asyncio.Task[Any] | None = None
    finished: bool = False

    def emit(self, item: dict[str, Any]) -> None:
        """Record one message and pass it to everyone following."""
        self.log.append(item)
        for q in list(self.subscribers):
            q.put_nowait(item)

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        """A queue with everything so far, then what comes next, then None at the end."""
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        for item in self.log:
            q.put_nowait(item)
        if self.finished:
            q.put_nowait(None)
        else:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any] | None]) -> None:
        self.subscribers.discard(q)

    def finish(self) -> None:
        self.finished = True
        for q in list(self.subscribers):
            q.put_nowait(None)
        self.subscribers.clear()
        _forget_later(self)

    @property
    def running(self) -> bool:
        return not self.finished and self.task is not None and not self.task.done()


_turns: dict[str, LiveTurn] = {}


def start(session_id: str, message: str) -> LiveTurn:
    turn = LiveTurn(session_id=session_id, message=message)
    _turns[session_id] = turn
    return turn


def get(session_id: str) -> LiveTurn | None:
    return _turns.get(session_id)


def running(session_id: str) -> bool:
    turn = _turns.get(session_id)
    return bool(turn and turn.running)


def _forget_later(turn: LiveTurn) -> None:
    def forget() -> None:
        if _turns.get(turn.session_id) is turn:
            del _turns[turn.session_id]

    try:
        asyncio.get_running_loop().call_later(KEEP_DONE_S, forget)
    except RuntimeError:
        forget()


__all__ = ["KEEP_DONE_S", "LiveTurn", "get", "running", "start"]
