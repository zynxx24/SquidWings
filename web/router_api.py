"""HTTP API for the Router page (``/router``): providers, accounts, models, rotation state, token usage.

The page works the same on this computer and through a tunnel: providers, URLs and API keys are
added, changed and removed there instead of in ``.env``. To keep strangers who find a tunnel URL
from changing them, set a PIN on the page; from then on changes from outside this computer need
it (sent as ``X-Router-Pin``). This computer never needs it. Full API keys are never sent back.

Adding a provider or a key fetches the provider's models at once and puts them in the model
registry, so they appear in the chat's model picker without a manual refresh.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from model.router import dispatch, pricing, sync
from model.router import usage as usage_log
from model.router.groups import grouped, provider_labels
from model.router.rotation import get_rotator
from model.router.store import AUTH_MODES, KINDS, STRATEGIES, StoreError, get_store
from security.web_access import is_local_request

router = APIRouter(prefix="/api/router", tags=["router"])


def can_edit(request: Request, pin: str | None = None) -> bool:
    if is_local_request(request) or not get_store().has_pin():
        return True
    return get_store().check_pin(pin)


def admin(request: Request, x_router_pin: str | None = Header(default=None)) -> None:
    if not can_edit(request, x_router_pin):
        raise HTTPException(403, "PIN router salah atau belum diisi.")


def _err(exc: StoreError) -> HTTPException:
    return HTTPException(422, str(exc))


def _view(provider_id: str | None = None) -> list[dict[str, Any]]:
    rotator = get_rotator()
    out = []
    for provider in get_store().providers():
        if provider_id and provider.id != provider_id:
            continue
        data = provider.public()
        state = rotator.snapshot(provider)
        for account in data["accounts"]:
            account["state"] = state.get(account["id"], {})
        data["ready"] = sum(1 for a in data["accounts"] if a["state"].get("status") == "siap")
        out.append(data)
    return out


def _one(provider_id: str) -> dict[str, Any]:
    found = _view(provider_id)
    if not found:
        raise HTTPException(404, "Provider tidak ditemukan")
    return found[0]


async def _refresh(provider_id: str) -> dict[str, Any]:
    provider = get_store().get(provider_id)
    return await sync.refresh(provider) if provider else {"ok": False, "count": 0, "error": "Provider tidak ditemukan"}


def _env_pending() -> bool:
    """Whether .env still has an endpoint the page has not imported."""
    import os
    store = get_store()
    urls = {p.base_url.rstrip("/") for p in store.providers()}
    for name, value in os.environ.items():
        if name.endswith("API_BASE_URL") and value.strip() and value.strip().rstrip("/") not in urls:
            stem = name[: -len("API_BASE_URL")]
            if os.getenv(f"{stem}API_KEY", "").strip() not in ("", "your-api-key-here"):
                return True
    return False


@router.get("/providers")
def providers(request: Request, x_router_pin: str | None = Header(default=None)) -> dict[str, Any]:
    store = get_store()
    return {"providers": _view(), "kinds": KINDS, "strategies": STRATEGIES, "auth_modes": AUTH_MODES,
            "can_edit": can_edit(request, x_router_pin), "has_pin": store.has_pin(), "local": is_local_request(request),
            "env_pending": _env_pending(), "default_model": store.meta("default_model", "")}


@router.post("/providers", dependencies=[Depends(admin)])
async def add_provider(body: dict[str, Any]) -> dict[str, Any]:
    try:
        provider = get_store().add_provider(body)
    except StoreError as exc:
        raise _err(exc) from exc
    models = await _refresh(provider.id)
    return {"provider": _one(provider.id), "models": models}


@router.patch("/providers/{provider_id}", dependencies=[Depends(admin)])
async def update_provider(provider_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        provider = get_store().update_provider(provider_id, body)
    except StoreError as exc:
        raise _err(exc) from exc
    models = None
    if {"base_url", "prefix", "kind", "default", "models", "auth", "enabled"} & body.keys():
        if provider.enabled:
            models = await _refresh(provider.id)
        else:
            sync.forget(provider.id)
    return {"provider": _one(provider.id), "models": models}


@router.delete("/providers/{provider_id}", dependencies=[Depends(admin)])
def delete_provider(provider_id: str) -> dict[str, Any]:
    try:
        get_store().delete_provider(provider_id)
    except StoreError as exc:
        raise _err(exc) from exc
    get_rotator().reset(provider_id)
    sync.forget(provider_id)
    return {"deleted": provider_id}


@router.post("/providers/{provider_id}/refresh", dependencies=[Depends(admin)])
async def refresh_provider(provider_id: str) -> dict[str, Any]:
    if get_store().get(provider_id) is None:
        raise HTTPException(404, "Provider tidak ditemukan")
    return {"models": await _refresh(provider_id), "provider": _one(provider_id)}


@router.post("/providers/{provider_id}/accounts", dependencies=[Depends(admin)])
async def add_accounts(provider_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        added = get_store().add_account(provider_id, body)
    except StoreError as exc:
        raise _err(exc) from exc
    models = await _refresh(provider_id)
    return {"added": [a.public() for a in added], "provider": _one(provider_id), "models": models}


@router.patch("/providers/{provider_id}/accounts/{account_id}", dependencies=[Depends(admin)])
def update_account(provider_id: str, account_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        get_store().update_account(provider_id, account_id, body)
    except StoreError as exc:
        raise _err(exc) from exc
    if body.get("enabled") is True:
        get_rotator().reset(provider_id, account_id)   # switched back on: start with a clean record
    return {"provider": _one(provider_id)}


@router.delete("/providers/{provider_id}/accounts/{account_id}", dependencies=[Depends(admin)])
def delete_account(provider_id: str, account_id: str) -> dict[str, Any]:
    try:
        get_store().delete_account(provider_id, account_id)
    except StoreError as exc:
        raise _err(exc) from exc
    get_rotator().reset(provider_id, account_id)
    return {"provider": _one(provider_id)}


@router.post("/providers/{provider_id}/reset", dependencies=[Depends(admin)])
def reset_state(provider_id: str, account_id: str | None = None) -> dict[str, Any]:
    if get_store().get(provider_id) is None:
        raise HTTPException(404, "Provider tidak ditemukan")
    get_rotator().reset(provider_id, account_id)
    return {"provider": _one(provider_id)}


@router.post("/providers/{provider_id}/test", dependencies=[Depends(admin)])
async def test_provider(provider_id: str, account_id: str | None = None) -> dict[str, Any]:
    """Ask the provider for its model list with one key. Spends no tokens."""
    provider = get_store().get(provider_id)
    if provider is None:
        raise HTTPException(404, "Provider tidak ditemukan")
    account = provider.account(account_id) if account_id else next((a for a in provider.accounts if a.enabled), None)
    if account is None:
        raise HTTPException(422, "Tambahkan API key dulu")
    started = time.monotonic()
    try:
        models = await dispatch.list_models(provider, account)
    except Exception as exc:  # noqa: BLE001 - the page shows what went wrong
        return {"ok": False, "account": account.label, "error": str(exc)[:300],
                "latency_ms": round((time.monotonic() - started) * 1000)}
    return {"ok": True, "account": account.label, "models": len(models),
            "sample": [m["id"] for m in models[:12]], "latency_ms": round((time.monotonic() - started) * 1000)}


@router.post("/import-env", dependencies=[Depends(admin)])
async def import_env() -> dict[str, Any]:
    """Copy the endpoints in .env to the page (again, if they were removed). Their models are fetched at once."""
    added = get_store().import_env(force=True)
    results = await asyncio.gather(*(_refresh(p.id) for p in added))
    return {"added": [{"provider": _one(p.id), "models": r} for p, r in zip(added, results, strict=True)]}


@router.post("/pin", dependencies=[Depends(admin)])
def set_pin(body: dict[str, Any]) -> dict[str, Any]:
    """Set the PIN that changes from outside this computer need. An empty PIN removes it."""
    try:
        get_store().set_pin(str(body.get("pin", "")))
    except StoreError as exc:
        raise _err(exc) from exc
    return {"has_pin": get_store().has_pin()}


@router.get("/models")
def models() -> dict[str, Any]:
    """Every chat model the registry knows, grouped by provider, and the default one."""
    from web.server import default_model_id, get_orchestrator, is_chat_model

    names, prefixes = provider_labels()
    found = grouped(((m.id, m.provider) for m in get_orchestrator().registry.get_all() if is_chat_model(m.id)),
                    names, prefixes)
    return {"groups": [{"key": g.key, "provider": g.provider, "family": g.family, "name": g.label, "models": ids}
                       for g, ids in found],
            "default_model": default_model_id(), "chosen": get_store().meta("default_model", "")}


@router.post("/default-model", dependencies=[Depends(admin)])
def set_default_model(body: dict[str, Any]) -> dict[str, Any]:
    """The model a new chat starts with. Empty = DEFAULT_MODEL from .env, or the first known model."""
    model = str(body.get("model", "")).strip()[:200]
    get_store().set_meta("default_model", model)
    return {"default_model": model}


@router.get("/usage")
def usage(period: str = Query("24h"), group: str = Query("provider")) -> dict[str, Any]:
    if period not in usage_log.PERIODS or group not in usage_log.GROUPS:
        raise HTTPException(400, f"period: {', '.join(usage_log.PERIODS)}; group: {', '.join(usage_log.GROUPS)}")
    book = pricing.get_price_book()
    data = usage_log.get_usage_log().summary(period, group, price=book.price)
    names = {p.id: p.name for p in get_store().providers()}
    for row in data["rows"]:
        if "provider" in row:
            row["provider_name"] = names.get(row["provider"], row["provider"])
    data["usd_idr"] = book.usd_idr()
    return data


@router.get("/prices")
def prices() -> dict[str, Any]:
    """The price used for every model that has tokens, plus the ones typed on the page. USD per 1M tokens."""
    book = pricing.get_price_book()
    seen = usage_log.get_usage_log().summary("all", "model")["rows"]
    tokens = {row["model"]: int(row["total"]) for row in seen}
    out = []
    for model in sorted(set(tokens) | set(book.custom_models()), key=lambda m: -tokens.get(m, 0)):
        price, source = book.lookup(model)
        out.append({"model": model, "tokens": tokens.get(model, 0), "source": source,
                    "price": price.public() if price else None})
    return {"models": out, "usd_idr": book.usd_idr(), "sources": list(pricing.SOURCES),
            "note": "Perkiraan. Harga bawaan hanya untuk Claude (harga Anthropic). Reseller sering lebih murah: isi harganya sendiri."}


@router.post("/prices", dependencies=[Depends(admin)])
def set_price(body: dict[str, Any]) -> dict[str, Any]:
    """Type a model's price (USD per 1M tokens), clear it with ``{"model": ..., "clear": true}``, or set ``usd_idr``."""
    book = pricing.get_price_book()
    try:
        if "usd_idr" in body:
            book.set_usd_idr(body.get("usd_idr"))
        model = str(body.get("model", "")).strip()
        if model and body.get("clear"):
            book.clear_custom(model)
        elif model:
            book.set_custom(model, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return prices()


__all__ = ["router", "can_edit"]
