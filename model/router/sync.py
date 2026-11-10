"""Fetch a provider's models the moment it is added or changed, and put them in the model registry.

The web server registers a sink (``set_registry_sink``) that writes into the orchestrator's
registry. Without one (tests, the CLI) the fetch still runs and reports what it found.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from model.router import dispatch
from model.router.store import ProviderConfig
from utils.logger import get_logger

logger = get_logger(__name__)

# (provider id, models or None to forget them all) -> number registered
Sink = Callable[[str, list[dict[str, Any]] | None], int]
_sink: Sink | None = None


def set_registry_sink(sink: Sink | None) -> None:
    global _sink
    _sink = sink


async def refresh(provider: ProviderConfig) -> dict[str, Any]:
    """Ask the provider for its models now. Never raises: the page shows the error instead."""
    if not provider.accounts and not provider.models:
        return {"ok": False, "count": 0, "error": "Belum ada API key untuk mengambil daftar model."}
    try:
        models = await dispatch.list_models(provider)
    except Exception as exc:  # noqa: BLE001 - any failure is reported, not raised
        logger.warning(f"[router] model fetch for {provider.id} failed: {exc}")
        return {"ok": False, "count": 0, "error": str(exc)[:300]}
    with contextlib.suppress(Exception):   # prices are a bonus: a listing without them is normal
        from model.router.pricing import get_price_book
        get_price_book().remember_listed(models)
    registered = 0
    if _sink is not None:
        try:
            registered = _sink(provider.id, models)
        except Exception as exc:  # noqa: BLE001 - the registry is best effort; the fetch worked
            logger.warning(f"[router] registering models of {provider.id} failed: {exc}")
    logger.info(f"[router] {provider.id}: {len(models)} models fetched, {registered} registered")
    return {"ok": True, "count": len(models), "registered": registered, "sample": [m["id"] for m in models[:15]]}


def forget(provider_id: str) -> None:
    """Remove a deleted provider's models from the registry."""
    if _sink is not None:
        with contextlib.suppress(Exception):
            _sink(provider_id, None)


__all__ = ["set_registry_sink", "refresh", "forget"]
