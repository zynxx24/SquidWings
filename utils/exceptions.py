"""
utils.exceptions — Custom exception hierarchy.

All exceptions inherit from AgentError, allowing granular
error handling while supporting broad catch-all patterns.

Hierarchy:
    AgentError (base)
    ├── ConnectionError      — HTTP/network failures
    ├── ModelError           — Model returned an error (4xx/5xx)
    │   ├── RateLimitError   — 429 Too Many Requests
    │   └── TimeoutError     — Request timed out
    ├── RouterError          — Routing logic failures
    │   └── FallbackExhaustedError — All fallbacks failed
    ├── BrainError           — Classification/prompt failures
    ├── ToolError            — Tool execution failures
    └── ValidationError      — Input/output validation failures
"""

from __future__ import annotations

from typing import Any


class AgentError(Exception):
    """Base exception for all agent errors.

    Attributes:
        message: Human-readable error description.
        details: Optional dict with additional context.
        original_error: Optional original exception that caused this.
    """

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        original_error: Exception | None = None,
    ) -> None:
        self.message = message
        self.details = details or {}
        self.original_error = original_error
        super().__init__(self.message)

    def __repr__(self) -> str:
        parts = [f"{self.__class__.__name__}('{self.message}'"]
        if self.details:
            parts.append(f", details={self.details}")
        if self.original_error:
            parts.append(f", caused_by={type(self.original_error).__name__}")
        return "".join(parts) + ")"


# --- Connection & Network ---

class ConnectionError(AgentError):  # noqa: A001
    """Failed to connect to the API endpoint.

    Raised when HTTP connection fails, DNS resolution fails,
    or the server is unreachable.
    """

    def __init__(
        self,
        message: str = "Failed to connect to API endpoint",
        url: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(message, details={"url": url, **kwargs.get("details", {})})
        self.url = url


# --- Model Errors ---

class ModelError(AgentError):
    """Model returned an error response.

    Raised when the API returns a non-2xx status code,
    excluding rate limiting (which has its own exception).
    """

    def __init__(
        self,
        message: str = "Model returned an error",
        model_id: str = "",
        status_code: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            details={
                "model_id": model_id,
                "status_code": status_code,
                **kwargs.get("details", {}),
            },
        )
        self.model_id = model_id
        self.status_code = status_code


class RateLimitError(ModelError):
    """Rate limit exceeded (HTTP 429).

    Attributes:
        retry_after: Seconds to wait before retrying (from Retry-After header).
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, status_code=429, **kwargs)
        self.retry_after = retry_after
        self.details["retry_after"] = retry_after


class TimeoutError(ModelError):  # noqa: A001
    """Request timed out.

    Attributes:
        timeout_seconds: The timeout duration that was exceeded.
    """

    def __init__(
        self,
        message: str = "Request timed out",
        timeout_seconds: float = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, status_code=408, **kwargs)
        self.timeout_seconds = timeout_seconds
        self.details["timeout_seconds"] = timeout_seconds


# --- Router Errors ---

class RouterError(AgentError):
    """Routing logic failure.

    Raised when the router cannot determine which model to use
    for a given task type.
    """

    def __init__(
        self,
        message: str = "Routing failed",
        task_type: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message, details={"task_type": task_type, **kwargs.get("details", {})}
        )
        self.task_type = task_type


class FallbackExhaustedError(RouterError):
    """All fallback models have been tried and failed.

    This is the last resort error — no more models to try.
    """

    def __init__(
        self,
        message: str = "All fallback models exhausted",
        tried_models: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.tried_models = tried_models or []
        self.details["tried_models"] = self.tried_models


# --- Brain Errors ---

class BrainError(AgentError):
    """Brain/classification/prompt error.

    Raised when intent classification fails, prompt loading fails,
    or context assembly encounters an error.
    """

    pass


# --- Tool Errors ---

class ToolError(AgentError):
    """Tool execution failure.

    Raised when a tool call fails to execute, returns an error,
    or violates safety constraints.
    """

    def __init__(
        self,
        message: str = "Tool execution failed",
        tool_name: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message, details={"tool_name": tool_name, **kwargs.get("details", {})}
        )
        self.tool_name = tool_name


# --- Validation Errors ---

class ValidationError(AgentError):
    """Input or output validation failure.

    Raised when user input, API request/response, or tool call
    arguments fail validation.
    """

    def __init__(
        self,
        message: str = "Validation failed",
        field: str = "",
        value: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            details={"field": field, "value": repr(value), **kwargs.get("details", {})},
        )
        self.field = field
        self.value = value
