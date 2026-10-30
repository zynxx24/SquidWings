"""
model.connection.api_client — Concrete API client using httpx.

Implements the BaseClient interface with full support for:
- Non-streaming and streaming chat completions
- Model listing
- Health checking
- Connection pooling and keep-alive
- Proper error mapping to custom exceptions
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from dotenv import load_dotenv

from model.connection.base_client import BaseClient
from model.connection.providers import Provider, load_providers
from model.settings.rate_limiter import RateLimiter, get_default_rate_limiter
from utils.exceptions import (
    ConnectionError,  # noqa: A004
    ModelError,
    RateLimitError,
    ReasoningBudgetError,
    TimeoutError,  # noqa: A004
)
from utils.logger import get_logger
from utils.timer import Timer

logger = get_logger(__name__)


class APIClient(BaseClient):
    """OpenAI-compatible API client using httpx.

    Features:
    - Async HTTP with connection pooling
    - Automatic error mapping (HTTP status → custom exceptions)
    - SSE streaming support
    - Request/response logging
    - Routes each model to its provider (see config/providers.yaml)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 120,
        rate_limiter: RateLimiter | None = None,
        max_retries: int | None = None,
        retry_backoff_factor: float | None = None,
    ) -> None:
        super().__init__(base_url, api_key, timeout)
        self._client: httpx.AsyncClient | None = None
        load_dotenv(override=False)
        self._main_provider = Provider(id="router", base_url=base_url, api_key=api_key)
        self._providers = load_providers()
        self._provider_clients: dict[str, tuple[httpx.AsyncClient, str, str]] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self.rate_limiter = rate_limiter or get_default_rate_limiter()
        if max_retries is not None:
            self.max_retries = max_retries
        else:
            try:
                self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
            except ValueError:
                self.max_retries = 3
        if retry_backoff_factor is not None:
            self.retry_backoff_factor = retry_backoff_factor
        else:
            try:
                self.retry_backoff_factor = float(os.getenv("RETRY_BACKOFF_FACTOR", "1.5"))
            except ValueError:
                self.retry_backoff_factor = 1.5

    def _route(self, model: str) -> tuple[Provider, str]:
        """Find the provider serving a model, and the id it expects."""
        for provider in self._providers:
            if provider.owns(model):
                return provider, provider.api_model(model)
        return self._main_provider, model

    @property
    def provider_ids(self) -> list[str]:
        """Providers that can serve requests right now."""
        return [self._main_provider.id, *(p.id for p in self._providers if p.key)]

    def provider_of(self, model: str) -> str:
        return self._route(model)[0].id

    def _client_for(self, provider: Provider) -> httpx.AsyncClient:
        if provider is self._main_provider:
            return self.client
        url, key = provider.url, provider.key
        client, cached_url, cached_key = self._provider_clients.get(provider.id, (None, "", ""))
        if client is None or client.is_closed or cached_url != url or cached_key != key:
            client = httpx.AsyncClient(
                base_url=url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
                follow_redirects=True,
            )
            self._provider_clients[provider.id] = (client, url, key)
        return client

    def _slot(self, provider: Provider) -> Any:
        """Concurrency limit for a provider, so a team cannot flood a free tier."""
        if provider.max_concurrency <= 0:
            return contextlib.nullcontext()
        semaphore = self._semaphores.get(provider.id)
        if semaphore is None:
            semaphore = asyncio.Semaphore(provider.max_concurrency)
            self._semaphores[provider.id] = semaphore
        return semaphore

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-initialized httpx AsyncClient with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
                follow_redirects=True,
            )
        return self._client

    @staticmethod
    def _should_retry_transient(provider: Provider, error: Exception, attempt: int) -> bool:
        """Retry what may heal by itself. A bad request or bad key never will."""
        status = getattr(error, "status_code", 0) or 0
        if 400 <= status < 500 and status not in {408, 425, 429}:
            return False
        return attempt < provider.max_retries

    def _handle_error(self, response: httpx.Response, model: str = "") -> None:
        """Map HTTP error responses to custom exceptions.

        Args:
            response: The HTTP response object.
            model: The model ID that was called (for error context).

        Raises:
            RateLimitError: If 429 status.
            TimeoutError: If 408 status.
            ModelError: For all other non-2xx status codes.
        """
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                message=f"Rate limited on model '{model}'",
                model_id=model,
                retry_after=float(retry_after) if retry_after else None,
            )

        if response.status_code == 408:
            raise TimeoutError(
                message=f"Request to model '{model}' timed out",
                model_id=model,
                timeout_seconds=self.timeout,
            )

        if response.status_code >= 400:
            error_msg = response.text
            try:
                error_body = response.json()
                if isinstance(error_body, dict):
                    err = error_body.get("error")
                    if isinstance(err, dict):
                        error_msg = err.get("message") or response.text
                    elif isinstance(err, str):
                        error_msg = err
                    elif "message" in error_body:
                        error_msg = str(error_body["message"])
            except Exception:
                error_msg = response.text

            # Routers report a temporary model lockout as "(reset after 29s)".
            reset = re.search(r"reset after (\d+)\s*s", error_msg)
            retry_after = float(reset.group(1)) if reset else None
            if "budget on reasoning" in error_msg:
                raise ReasoningBudgetError(
                    message=f"Model '{model}' returned error: {error_msg}",
                    retry_after=retry_after,
                    model_id=model,
                    status_code=response.status_code,
                )
            if reset and response.status_code >= 500:
                raise RateLimitError(
                    message=f"Model '{model}' is locked: {error_msg}",
                    model_id=model,
                    retry_after=retry_after,
                )

            raise ModelError(
                message=f"Model '{model}' returned error: {error_msg}",
                model_id=model,
                status_code=response.status_code,
            )

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int = 8192,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a non-streaming chat completion request.

        Args:
            model: Model ID (e.g., "ag/gemini-3.8-flash-high" or "bansos/deepseek-v4.1-flash-1017").
            messages: Chat messages list.
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Maximum output tokens.
            tools: Optional tool schemas for function calling.
            stream: Ignored (use chat_completion_stream for streaming).
            **kwargs: Extra params (thinking config, etc.).

        Returns:
            Full parsed response dict from the API.
        """
        provider, api_model = self._route(model)

        payload: dict[str, Any] = {
            "model": api_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        if tools:
            payload["tools"] = tools

        payload.update(kwargs)

        attempt = 0
        while True:
            attempt += 1
            client = self._client_for(provider)

            logger.debug(
                f"[api] POST /chat/completions | model={api_model} | provider={provider.id} | "
                f"msgs={len(messages)} | temp={temperature} | max_tok={max_tokens} | attempt={attempt}"
            )

            try:
                await self.rate_limiter.wait_and_acquire(api_model, max_wait=30.0)
                async with self._slot(provider), Timer(f"chat_completion({model})") as t:
                    response = await client.post(
                        "/chat/completions",
                        json=payload,
                    )
                self._handle_error(response, api_model)

                raw_json = response.json()
                result: dict[str, Any] = raw_json if isinstance(raw_json, dict) else {"_data": raw_json}

                usage = result.get("usage")
                if not isinstance(usage, dict):
                    usage = {}
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
                cached_tokens = (
                    usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                    if isinstance(usage.get("prompt_tokens_details"), dict)
                    else 0
                ) or usage.get("cached_tokens", 0) or usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)

                cache_info = ""
                if prompt_tokens > 0 and cached_tokens > 0:
                    hit_ratio = (cached_tokens / prompt_tokens) * 100
                    cache_info = f" | cache_hit={hit_ratio:.1f}% ({cached_tokens} cached)"
                elif cache_write > 0:
                    cache_info = f" | cache_write={cache_write}"

                logger.info(
                    f"[api] ✓ {model} | "
                    f"in={prompt_tokens} "
                    f"out={completion_tokens} "
                    f"total={total_tokens}{cache_info} | "
                    f"{t.elapsed_ms:.0f}ms"
                )
                return result

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if provider.max_retries:
                    if not self._should_retry_transient(provider, e, attempt):
                        raise
                    backoff = min(attempt * 1.5, 6.0)
                    logger.warning(
                        f"[{provider.id}] API call error ({api_model}): {e}. "
                        f"Retrying in {backoff:.1f}s (attempt {attempt}/{provider.max_retries})..."
                    )
                    await asyncio.sleep(backoff)
                    continue
                else:
                    if isinstance(e, RateLimitError):
                        retry_after = e.retry_after
                        if retry_after is not None and retry_after > 30.0:
                            logger.warning(
                                f"[api] Upstream quota exhausted or Retry-After too long ({retry_after:.0f}s). "
                                f"Triggering immediate model fallback."
                            )
                            raise
                        if retry_after is None or retry_after <= 0:
                            retry_after = min(2.0 * (self.retry_backoff_factor ** (attempt - 1)), 15.0)
                        self.rate_limiter.penalize(api_model, penalty_seconds=min(retry_after, 30.0))
                        if attempt <= self.max_retries:
                            logger.warning(
                                f"[api] Rate limited on {api_model} (HTTP 429). "
                                f"Retrying in {retry_after:.1f}s (attempt {attempt}/{self.max_retries})..."
                            )
                            await asyncio.sleep(retry_after)
                            continue

                    if isinstance(e, (ConnectionError, TimeoutError, RateLimitError, ModelError)):
                        raise
                    if isinstance(e, httpx.ConnectError):
                        raise ConnectionError(
                            message=f"Cannot connect to API at {self.base_url}",
                            url=self.base_url,
                            original_error=e,
                        ) from e
                    if isinstance(e, httpx.TimeoutException):
                        raise TimeoutError(
                            message=f"Request to model '{model}' timed out after {self.timeout}s",
                            model_id=model,
                            timeout_seconds=self.timeout,
                        ) from None
                    raise

    async def chat_completion_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int = 8192,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a streaming chat completion request (SSE).

        Yields parsed JSON chunks from the SSE stream.
        """
        provider, api_model = self._route(model)

        payload: dict[str, Any] = {
            "model": api_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools

        payload.update(kwargs)

        attempt = 0
        yielded = False
        while True:
            attempt += 1
            client = self._client_for(provider)

            logger.debug(f"[api] POST /chat/completions (stream) | model={api_model} | attempt={attempt}")

            try:
                await self.rate_limiter.wait_and_acquire(api_model, max_wait=30.0)
                async with self._slot(provider), client.stream(
                    "POST",
                    "/chat/completions",
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        self._handle_error(response, api_model)

                    try:
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line:
                                continue
                            if line == "data: [DONE]":
                                logger.debug(f"[api] Stream complete for {model}")
                                break
                            if line.startswith("data: "):
                                data_str = line[6:]
                                try:
                                    chunk = json.loads(data_str)
                                    yielded = True
                                    yield chunk
                                except json.JSONDecodeError:
                                    logger.warning(f"[api] Failed to parse SSE chunk: {data_str[:100]}")
                    except GeneratorExit:
                        logger.debug(f"[api] Stream generator exited cleanly for {model}")
                        return
                return

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Chunks already reached the caller, so a retry would duplicate them.
                if provider.max_retries and not yielded:
                    if not self._should_retry_transient(provider, e, attempt):
                        raise
                    backoff = min(attempt * 1.5, 6.0)
                    logger.warning(
                        f"[{provider.id}] Stream error ({api_model}): {e}. "
                        f"Retrying in {backoff:.1f}s (attempt {attempt}/{provider.max_retries})..."
                    )
                    await asyncio.sleep(backoff)
                    continue
                if provider.max_retries:
                    raise
                else:
                    if isinstance(e, RateLimitError):
                        retry_after = e.retry_after
                        if retry_after is not None and retry_after > 30.0:
                            logger.warning(
                                f"[api] Upstream quota exhausted or Retry-After too long ({retry_after:.0f}s). "
                                f"Triggering immediate model fallback."
                            )
                            raise
                        if retry_after is None or retry_after <= 0:
                            retry_after = min(2.0 * (self.retry_backoff_factor ** (attempt - 1)), 15.0)
                        self.rate_limiter.penalize(api_model, penalty_seconds=min(retry_after, 30.0))
                        if attempt <= self.max_retries:
                            logger.warning(
                                f"[api] Rate limited on stream {api_model} (HTTP 429). "
                                f"Retrying in {retry_after:.1f}s (attempt {attempt}/{self.max_retries})..."
                            )
                            await asyncio.sleep(retry_after)
                            continue

                    if isinstance(e, (ConnectionError, TimeoutError, RateLimitError, ModelError)):
                        raise
                    if isinstance(e, httpx.TimeoutException):
                        raise TimeoutError(
                            message=f"Stream to model '{model}' timed out after {self.timeout}s",
                            model_id=model,
                            timeout_seconds=self.timeout,
                        ) from None
                    raise

    async def list_models(self) -> list[dict[str, Any]]:
        """Fetch all available models from /v1/models.

        Returns:
            List of model dicts with id, capabilities, etc.

        Raises:
            ConnectionError: If the API is unreachable.
        """
        try:
            response = await self.client.get("/models")
        except httpx.ConnectError as e:
            raise ConnectionError(
                message=f"Cannot connect to API at {self.base_url}",
                url=self.base_url,
                original_error=e,
            ) from e

        if response.status_code >= 400:
            self._handle_error(response)

        raw_json = response.json()
        raw_data = raw_json.get("data", []) if isinstance(raw_json, dict) else []
        models: list[dict[str, Any]] = [m for m in raw_data if isinstance(m, dict)] if isinstance(raw_data, list) else []
        logger.info(f"[api] Fetched {len(models)} models from API")
        return models

    async def list_provider_models(self, provider: Provider) -> list[dict[str, Any]]:
        """Fetch a provider's /models and tag the ids so requests route back to it."""
        if not provider.key:
            return []
        try:
            response = await self._client_for(provider).get("/models")
        except httpx.HTTPError as e:
            raise ConnectionError(
                message=f"Cannot connect to {provider.id} at {provider.url}",
                url=provider.url,
                original_error=e,
            ) from e
        self._handle_error(response)
        raw_json = response.json()
        raw_data = raw_json.get("data", []) if isinstance(raw_json, dict) else []
        models: list[dict[str, Any]] = []
        for item in raw_data if isinstance(raw_data, list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            models.append({**item, "id": provider.tag_model(str(item["id"])), "owned_by": provider.id})
        logger.info(f"[api] Fetched {len(models)} models from {provider.id}")
        return models

    async def sync_all_models(self, registry: Any) -> dict[str, int]:
        """Fetch /models from every provider and register them.

        Each source is independent: one failing never blocks the others. Models a
        provider no longer lists are removed, unless it returned an empty list,
        which is an upstream hiccup rather than "every model was removed".

        Returns:
            Mapping of provider id to number of models registered.
        """
        counts: dict[str, int] = {}
        fetchers: list[tuple[Provider, Any]] = [(self._main_provider, self.list_models)]
        fetchers += [(p, lambda p=p: self.list_provider_models(p)) for p in self._providers]
        for provider, fetch in fetchers:
            try:
                items = [m for m in await fetch() if m.get("id")]
                if items and hasattr(registry, "model_ids") and hasattr(registry, "unregister"):
                    current_ids = {m["id"] for m in items}
                    for mid in list(registry.model_ids):
                        if mid not in current_ids and self._route(mid)[0].id == provider.id:
                            registry.unregister(mid)
                registry.register_from_api_models(items)
                counts[provider.id] = len(items)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[api] Auto-sync '{provider.id}' failed: {e}")
                counts[provider.id] = 0
        return counts

    async def list_gemini_models(self, provider: str | None = None) -> list[dict[str, Any]]:
        """Fetch all models from 9Router and filter Gemini/Gemma models.

        Args:
            provider: Optional provider filter (e.g. 'gemini' to select direct provider models).

        Returns:
            List of Gemini/Gemma model dicts from 9Router.
        """
        all_models = await self.list_models()
        gemini_models = []
        for m in all_models:
            mid = (m.get("id") or "").lower()
            owned = (m.get("owned_by") or "").lower()
            if not ("gemini" in mid or "gemma" in mid):
                continue
            if provider and owned != provider.lower() and not mid.startswith(f"{provider.lower()}/"):
                continue
            gemini_models.append(m)

        logger.info(
            f"[api] Selected {len(gemini_models)} Gemini models from {len(all_models)} total 9Router models (provider={provider})"
        )
        return gemini_models

    async def list_kg_models(self) -> list[dict[str, Any]]:
        """Fetch all models from 9Router and filter Kagiro (kg) marketplace models.

        Returns:
            List of Kagiro model dicts from 9Router.
        """
        all_models = await self.list_models()
        kg_models = [
            m for m in all_models
            if (m.get("id") or "").startswith("kg/") or (m.get("owned_by") or "").lower() == "kg"
        ]
        logger.info(
            f"[api] Selected {len(kg_models)} Kagiro models from {len(all_models)} total 9Router models"
        )
        return kg_models

    async def list_claude_models(self) -> list[dict[str, Any]]:
        """Fetch all models from 9Router and filter Claude models.

        Returns:
            List of Claude model dicts from 9Router.
        """
        all_models = await self.list_models()
        claude_models = [
            m for m in all_models
            if "claude" in (m.get("id") or "").lower()
        ]
        logger.info(
            f"[api] Selected {len(claude_models)} Claude models from {len(all_models)} total 9Router models"
        )
        return claude_models

    async def health_check(self) -> bool:
        """Check API health by hitting /models endpoint.

        Returns:
            True if API responds successfully, False otherwise.
        """
        try:
            response = await self.client.get("/models")
            healthy = response.status_code == 200
            if healthy:
                logger.debug("[api] Health check: OK")
            else:
                logger.warning(f"[api] Health check: status {response.status_code}")
            return healthy
        except Exception as e:
            logger.warning(f"[api] Health check failed: {e}")
            return False

    async def close(self) -> None:
        """Close the httpx client and release connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("[api] Client closed")
        for client, _, _ in self._provider_clients.values():
            if not client.is_closed:
                await client.aclose()
