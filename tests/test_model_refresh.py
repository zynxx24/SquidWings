"""The model list is re-read from the providers on a timer, not only once at startup."""

from __future__ import annotations

import asyncio
import time

import web.server as server


class _Api:
    def __init__(self) -> None:
        self.calls = 0

    async def sync_all_models(self, registry: object) -> dict[str, int]:
        self.calls += 1
        return {"kg": 2}


class _Orch:
    def __init__(self, api: _Api) -> None:
        self.api_client = api
        self.registry = object()


def test_fresh_registry_is_not_fetched_again_but_a_stale_one_is(monkeypatch):
    api = _Api()
    monkeypatch.setattr(server, "get_orchestrator", lambda: _Orch(api))
    monkeypatch.setattr(server, "_registry_size", lambda: 2)
    monkeypatch.setattr(server, "MODEL_SYNC_EVERY_S", 60.0)
    monkeypatch.setattr(server, "_models_synced", False)
    monkeypatch.setattr(server, "_models_synced_at", 0.0)

    asyncio.run(server.ensure_models())   # first sync
    asyncio.run(server.ensure_models())   # just synced: no call
    assert api.calls == 1

    monkeypatch.setattr(server, "_models_synced_at", time.time() - 3600)
    asyncio.run(server.ensure_models())   # an hour old, interval 60 s: fetch again
    assert api.calls == 2


def test_interval_zero_turns_the_timer_off(monkeypatch):
    api = _Api()
    monkeypatch.setattr(server, "get_orchestrator", lambda: _Orch(api))
    monkeypatch.setattr(server, "_registry_size", lambda: 2)
    monkeypatch.setattr(server, "MODEL_SYNC_EVERY_S", 0.0)
    monkeypatch.setattr(server, "_models_synced", True)
    monkeypatch.setattr(server, "_models_synced_at", time.time() - 10**6)
    asyncio.run(server.ensure_models())
    assert api.calls == 0
