"""Send one chat request through a provider from the Router page, choosing and failing over accounts.

For each request: pick an account (model.router.rotation), send it in the provider's own format
(OpenAI chat completions, or Claude messages translated by model.router.anthropic), record the
tokens (model.router.usage), and on a rate limit, a dead key, an empty quota or a server error
try the next account. A bad request (400, 404, 422) is the request's fault, not the account's,
so it is raised at once without punishing the key.

When every account is resting, a short wait (up to WAIT_FOR_ACCOUNT_S) is spent waiting for the
first one to come back; a longer one raises RateLimitError so the agent's model fallback moves on.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from model.router import anthropic as claude_format
from model.router import usage as usage_log
from model.router.rotation import NoAccountError, get_rotator
from model.router.store import Account, ProviderConfig, get_store
from utils.exceptions import ConnectionError as RouterConnectionError
from utils.exceptions import ModelError, RateLimitError
from utils.logger import get_logger
from utils.workspace_context import get_active_session

logger = get_logger(__name__)

WAIT_FOR_ACCOUNT_S = 8.0
MODELS_TIMEOUT_S = 12.0
MAX_ATTEMPTS = 6
_NO_RETRY = {400, 404, 413, 422}
_clients: dict[tuple[str, str, str, int], httpx.AsyncClient] = {}


def _headers(provider: ProviderConfig, key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if provider.auth_mode in ("bearer", "both"):
        headers["Authorization"] = f"Bearer {key}"
    if provider.auth_mode in ("x-api-key", "both"):
        headers["x-api-key"] = key
    if provider.kind == "anthropic":
        headers["anthropic-version"] = provider.anthropic_version
    return headers


def _client(provider: ProviderConfig, account: Account) -> httpx.AsyncClient:
    key = (provider.id, account.id, provider.base_url, provider.timeout_s)
    client = _clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            base_url=provider.base_url, headers=_headers(provider, account.api_key),
            timeout=httpx.Timeout(provider.timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0),
            follow_redirects=True,
        )
        _clients[key] = client
    return client


def _path(provider: ProviderConfig, what: str) -> str:
    """Endpoint paths. An OpenAI base already ends in /v1; a Claude base usually does not."""
    has_v1 = provider.base_url.rstrip("/").endswith("/v1")
    if what == "chat":
        if provider.kind == "anthropic":
            return "/messages" if has_v1 else "/v1/messages"
        return "/chat/completions"
    if provider.kind == "anthropic":
        return "/models" if has_v1 else "/v1/models"
    return "/models"


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:300]
        if error:
            return str(error)[:300]
        if body.get("message"):
            return str(body["message"])[:300]
    return response.text[:300]


def _retry_after(response: httpx.Response, message: str) -> float | None:
    for name in ("retry-after", "anthropic-ratelimit-requests-reset", "x-ratelimit-reset-requests"):
        raw = response.headers.get(name, "")
        try:
            value = float(raw)
            if value > 0:
                return min(value, 3600.0)
        except ValueError:
            continue
    match = re.search(r"(?:reset|retry) (?:after|in) (\d+(?:\.\d+)?)\s*s", message)
    return float(match.group(1)) if match else None


class _UpstreamError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def _payload(provider: ProviderConfig, model: str, messages: list[dict[str, Any]], temperature: float, max_tokens: int,
             tools: list[dict[str, Any]] | None, stream: bool, extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": provider.api_model(model), "messages": messages, "temperature": temperature,
                               "max_tokens": max_tokens, "stream": stream, **extra}
    if tools:
        payload["tools"] = tools
    if provider.kind == "anthropic":
        return claude_format.to_request(payload, provider.api_model(model), auto_cache=provider.auto_cache)
    if stream and provider.stream_usage:
        payload.setdefault("stream_options", {"include_usage": True})
    return payload


async def _choose(provider: ProviderConfig, session: str, tried: set[str]) -> Account:
    deadline = time.monotonic() + WAIT_FOR_ACCOUNT_S
    while True:
        try:
            return get_rotator().pick(provider, session, frozenset(tried)).account
        except NoAccountError as exc:
            if exc.retry_after is None or time.monotonic() + exc.retry_after > deadline:
                raise RateLimitError(message=str(exc), retry_after=exc.retry_after, model_id=provider.id) from None
            await asyncio.sleep(exc.retry_after)


def _final_error(provider: ProviderConfig, model: str, last: _UpstreamError | None) -> Exception:
    if last is None:
        return RateLimitError(message=f"No account of {provider.name} could serve {model}", model_id=model)
    if last.status == 429 or last.status in (401, 402, 403):
        return RateLimitError(message=f"{provider.name}: every account failed ({last.message})",
                              retry_after=last.retry_after, model_id=model)
    if last.status == 0:
        return RouterConnectionError(message=f"{provider.name}: {last.message}", url=provider.base_url)
    return ModelError(message=f"Model '{model}' returned error: {last.message}", model_id=model, status_code=last.status)


def _record(provider: ProviderConfig, account: Account, model: str, usage: dict[str, Any] | None, started: float,
            *, ok: bool, stream: bool, error: str = "") -> None:
    usage_log.record(provider=provider.id, account=account.id, account_label=account.label, model=model, usage=usage,
             latency_ms=(time.monotonic() - started) * 1000, ok=ok, error=error, stream=stream,
             session=get_active_session() or "")


async def chat(provider: ProviderConfig, model: str, messages: list[dict[str, Any]], *, temperature: float = 0.5,
               max_tokens: int = 8192, tools: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    rotator = get_rotator()
    session = get_active_session() or ""
    payload = _payload(provider, model, messages, temperature, max_tokens, tools, False, extra)
    tried: set[str] = set()
    last: _UpstreamError | None = None
    for _ in range(min(MAX_ATTEMPTS, max(1, len(provider.accounts)))):
        account = await _choose(provider, session, tried)
        tried.add(account.id)
        started = time.monotonic()
        try:
            try:
                response = await _client(provider, account).post(_path(provider, "chat"), json=payload)
            except httpx.TimeoutException as exc:
                raise _UpstreamError(408, f"timed out after {provider.timeout_s}s") from exc
            except httpx.HTTPError as exc:
                raise _UpstreamError(0, f"cannot connect to {provider.base_url}: {exc}") from exc
            if response.status_code >= 400:
                message = _message(response)
                raise _UpstreamError(response.status_code, message, _retry_after(response, message))
            data = response.json()
            result = claude_format.to_response(data, model) if provider.kind == "anthropic" else data
            rotator.success(provider.id, account.id, (time.monotonic() - started) * 1000)
            _record(provider, account, model, result.get("usage") if isinstance(result, dict) else None, started,
                    ok=True, stream=False)
            if isinstance(result, dict):
                result.setdefault("model", model)
            return result
        except _UpstreamError as failed:
            last = failed
            _record(provider, account, model, None, started, ok=False, stream=False, error=failed.message)
            if failed.status in _NO_RETRY:
                raise ModelError(message=f"Model '{model}' returned error: {failed.message}", model_id=model,
                                 status_code=failed.status) from None
            rest = rotator.failure(provider.id, account.id, status=failed.status, retry_after=failed.retry_after,
                                   message=failed.message)
            logger.warning(f"[router] {provider.id}/{account.label}: {failed.status} {failed.message[:120]}"
                           f"{f' (rest {rest:.0f}s)' if rest else ''}; trying another account")
        finally:
            rotator.release(provider.id, account.id)
    raise _final_error(provider, model, last)


async def _sse(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


async def chat_stream(provider: ProviderConfig, model: str, messages: list[dict[str, Any]], *, temperature: float = 0.5,
                      max_tokens: int = 8192, tools: list[dict[str, Any]] | None = None,
                      **extra: Any) -> AsyncIterator[dict[str, Any]]:
    """Stream chunks in the OpenAI shape. Fails over only before the first chunk reached the caller."""
    rotator = get_rotator()
    session = get_active_session() or ""
    payload = _payload(provider, model, messages, temperature, max_tokens, tools, True, extra)
    tried: set[str] = set()
    last: _UpstreamError | None = None
    for _ in range(min(MAX_ATTEMPTS, max(1, len(provider.accounts)))):
        account = await _choose(provider, session, tried)
        tried.add(account.id)
        started = time.monotonic()
        yielded = False
        usage: dict[str, Any] | None = None
        try:
            try:
                async with _client(provider, account).stream("POST", _path(provider, "chat"), json=payload) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        message = _message(response)
                        raise _UpstreamError(response.status_code, message, _retry_after(response, message))
                    translator = claude_format.StreamTranslator(model) if provider.kind == "anthropic" else None
                    async for event in _sse(response):
                        if translator is not None:
                            try:
                                chunks = translator.feed(event)
                            except claude_format.StreamError as exc:
                                status = 429 if "rate" in exc.kind or "overloaded" in exc.kind else 500
                                raise _UpstreamError(status, str(exc)) from exc
                        else:
                            chunks = [event]
                        for chunk in chunks:
                            if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
                                usage = chunk["usage"]
                            yielded = True
                            yield chunk
            except httpx.TimeoutException as exc:
                raise _UpstreamError(408, f"timed out after {provider.timeout_s}s") from exc
            except httpx.HTTPError as exc:
                raise _UpstreamError(0, f"cannot connect to {provider.base_url}: {exc}") from exc
            rotator.success(provider.id, account.id, (time.monotonic() - started) * 1000)
            _record(provider, account, model, usage, started, ok=True, stream=True)
            return
        except _UpstreamError as failed:
            last = failed
            _record(provider, account, model, usage, started, ok=False, stream=True, error=failed.message)
            rest = rotator.failure(provider.id, account.id, status=failed.status, retry_after=failed.retry_after,
                                   message=failed.message) if failed.status not in _NO_RETRY else 0
            if failed.status in _NO_RETRY or yielded:
                # Text already reached the user, so another account would repeat it.
                raise _final_error(provider, model, failed) from None
            logger.warning(f"[router] stream {provider.id}/{account.label}: {failed.status} {failed.message[:120]}"
                           f"{f' (rest {rest:.0f}s)' if rest else ''}; trying another account")
        finally:
            rotator.release(provider.id, account.id)
    raise _final_error(provider, model, last)


async def list_models(provider: ProviderConfig, account: Account | None = None) -> list[dict[str, Any]]:
    """The provider's models, tagged with its prefix. Extra ids from the page are added."""
    account = account or next((a for a in provider.accounts if a.enabled), None)
    ids: list[dict[str, Any]] = []
    if account is not None:
        try:
            # A short timeout: model lists are fetched at start-up, and a dead endpoint must not hold it.
            response = await _client(provider, account).get(_path(provider, "models"), timeout=MODELS_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise RouterConnectionError(message=f"Cannot connect to {provider.name} at {provider.base_url}",
                                        url=provider.base_url, original_error=exc) from exc
        if response.status_code >= 400:
            if not provider.models:
                raise ModelError(message=f"{provider.name} /models: {_message(response)}", status_code=response.status_code)
        else:
            body = response.json()
            data = body.get("data", []) if isinstance(body, dict) else body if isinstance(body, list) else []
            for item in data:
                if isinstance(item, dict) and item.get("id"):
                    ids.append({**item, "id": provider.prefix + str(item["id"]), "owned_by": provider.id,
                                "display_name": item.get("display_name") or str(item["id"])})
    known = {m["id"] for m in ids}
    for extra in provider.models:
        mid = provider.prefix + extra
        if mid not in known:
            ids.append({"id": mid, "owned_by": provider.id, "display_name": extra})
    return ids


def route(model: str) -> ProviderConfig | None:
    """The Router-page provider serving this model, if any. Never raises."""
    try:
        return get_store().route(model)
    except Exception:  # noqa: BLE001 - a broken store must not break other providers
        return None


async def close() -> None:
    for client in list(_clients.values()):
        if not client.is_closed:
            await client.aclose()
    _clients.clear()


__all__ = ["chat", "chat_stream", "list_models", "route", "close"]
