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
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
from dotenv import load_dotenv

from model.connection.base_client import BaseClient
from utils.exceptions import (
    ConnectionError,  # noqa: A004
    ModelError,
    RateLimitError,
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
    - Bansos mode direct routing and non-stop fallback
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 120,
    ) -> None:
        super().__init__(base_url, api_key, timeout)
        self._client: httpx.AsyncClient | None = None
        self._bansos_client: httpx.AsyncClient | None = None
        self._bansos_url: str = ""
        self._bansos_key: str = ""

    def _is_bansos(self, model: str) -> bool:
        return model.startswith("bansos/") or model == "deepseek-v4.1-flash-1017"

    def _get_bansos_client(self) -> tuple[httpx.AsyncClient, str]:
        """Get or initialize client for bansos mode directly from .env."""
        load_dotenv(override=False)
        base_url = os.getenv("BANSOS_API_BASE_URL", "https://api.kagiro.net/v1").rstrip("/")
        api_key = os.getenv("BANSOS_API_KEY", "")
        if (
            self._bansos_client is None
            or self._bansos_client.is_closed
            or self._bansos_key != api_key
            or self._bansos_url != base_url
        ):
            self._bansos_url = base_url
            self._bansos_key = api_key
            self._bansos_client = httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
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
        return self._bansos_client, base_url

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
        is_bansos = self._is_bansos(model)
        api_model = model.replace("bansos/", "") if is_bansos else model

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
            if is_bansos:
                client, active_url = self._get_bansos_client()
            else:
                client = self.client
                active_url = self.base_url

            logger.debug(
                f"[api] POST /chat/completions | model={api_model} | url={active_url} | "
                f"msgs={len(messages)} | temp={temperature} | max_tok={max_tokens} | attempt={attempt}"
            )

            try:
                async with Timer(f"chat_completion({model})") as t:
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
                if is_bansos:
                    backoff = min(attempt * 1.5, 6.0)
                    logger.warning(
                        f"[bansos] API call error ({api_model} @ {active_url}): {e}. "
                        f"Non-stop fallback: retrying in {backoff:.1f}s (attempt {attempt})..."
                    )
                    await asyncio.sleep(backoff)
                    continue
                else:
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
        is_bansos = self._is_bansos(model)
        api_model = model.replace("bansos/", "") if is_bansos else model

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
        while True:
            attempt += 1
            if is_bansos:
                client, active_url = self._get_bansos_client()
            else:
                client = self.client
                active_url = self.base_url

            logger.debug(f"[api] POST /chat/completions (stream) | model={api_model} | attempt={attempt}")

            try:
                async with client.stream(
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
                                    yield json.loads(data_str)
                                except json.JSONDecodeError:
                                    logger.warning(f"[api] Failed to parse SSE chunk: {data_str[:100]}")
                    except GeneratorExit:
                        logger.debug(f"[api] Stream generator exited cleanly for {model}")
                        return
                return

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if is_bansos:
                    backoff = min(attempt * 1.5, 6.0)
                    logger.warning(
                        f"[bansos] Stream error ({api_model} @ {active_url}): {e}. "
                        f"Non-stop fallback: retrying in {backoff:.1f}s (attempt {attempt})..."
                    )
                    await asyncio.sleep(backoff)
                    continue
                else:
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
        if self._bansos_client and not self._bansos_client.is_closed:
            await self._bansos_client.aclose()
            logger.debug("[api] Bansos client closed")
