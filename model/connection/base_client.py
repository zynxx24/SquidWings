"""
model.connection.base_client — Abstract base HTTP client.

Defines the interface for all API client implementations.
Supports both synchronous and asynchronous operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Self


class BaseClient(ABC):
    """Abstract base class for API clients.

    All concrete API clients must implement these methods
    to ensure consistent behavior across different backends.

    Attributes:
        base_url: The base URL of the API endpoint.
        timeout: Request timeout in seconds.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        """Default headers for API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

    @abstractmethod
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
        """Send a chat completion request.

        Args:
            model: Model ID to use.
            messages: List of message dicts.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            tools: Optional list of tool schemas.
            stream: Whether to stream the response.
            **kwargs: Additional model-specific parameters.

        Returns:
            Parsed response dict.
        """
        ...

    @abstractmethod
    def chat_completion_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int = 8192,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a streaming chat completion request.

        Args:
            model: Model ID to use.
            messages: List of message dicts.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            tools: Optional list of tool schemas.
            **kwargs: Additional model-specific parameters.

        Yields:
            Response chunks as dicts.
        """
        ...

    @abstractmethod
    async def list_models(self) -> list[dict[str, Any]]:
        """List all available models.

        Returns:
            List of model info dicts from the API.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the API endpoint is reachable.

        Returns:
            True if healthy, False otherwise.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the client and release resources."""
        ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
