"""
utils — Shared utility modules.

Provides common functionality used across all layers:
logging, exceptions, retry logic, validation, event bus,
timing, and helper functions.
"""

from utils.exceptions import (
    AgentError,
    BrainError,
    ConnectionError,  # noqa: A004
    FallbackExhaustedError,
    ModelError,
    RateLimitError,
    RouterError,
    TimeoutError,  # noqa: A004
    ToolError,
    ValidationError,
)
from utils.logger import get_logger
from utils.retry import with_retry

__all__ = [
    # Exceptions
    "AgentError",
    "ModelError",
    "ConnectionError",
    "RouterError",
    "ToolError",
    "BrainError",
    "ValidationError",
    "TimeoutError",
    "RateLimitError",
    "FallbackExhaustedError",
    # Logging
    "get_logger",
    # Retry
    "with_retry",
]
