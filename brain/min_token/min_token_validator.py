"""
brain.min_token.min_token_validator — Validate response meets minimum threshold.

Checks if a model response has enough output tokens to be considered
a complete answer. Flags potentially incomplete responses.
"""

from __future__ import annotations

from dataclasses import dataclass

from brain.generate_efficient_token.token_counter import TokenCounter
from brain.min_token.min_token_config import MinTokenConfig
from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    """Result of min-token validation.

    Attributes:
        is_valid: Whether the response meets the minimum threshold.
        actual_tokens: Actual output token count.
        min_threshold: Required minimum tokens.
        task_type: The task type checked.
        message: Human-readable validation message.
    """
    is_valid: bool
    actual_tokens: int
    min_threshold: int
    task_type: TaskType
    message: str = ""


class MinTokenValidator:
    """Validates that responses meet minimum token thresholds.

    Flags responses that are suspiciously short for their task type,
    which may indicate truncation, API errors, or incomplete generation.
    """

    def __init__(
        self,
        config: MinTokenConfig | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize the validator.

        Args:
            config: MinTokenConfig instance.
            token_counter: TokenCounter instance.
        """
        self._config = config or MinTokenConfig()
        self._counter = token_counter or TokenCounter()

    def validate(
        self,
        response: ParsedResponse,
        task_type: TaskType,
    ) -> ValidationResult:
        """Validate that a response meets the minimum token threshold.

        Args:
            response: The parsed model response.
            task_type: The task type of the request.

        Returns:
            ValidationResult with pass/fail and details.
        """
        min_threshold = self._config.get_min_threshold(task_type)

        # Count actual output tokens
        actual_tokens = self._count_response_tokens(response)

        is_valid = actual_tokens >= min_threshold

        if not is_valid:
            message = (
                f"Response may be incomplete: {actual_tokens} tokens "
                f"< minimum {min_threshold} for {task_type.value}"
            )
            logger.warning(f"[min_token] {message}")
        else:
            message = (
                f"Response OK: {actual_tokens} tokens "
                f">= minimum {min_threshold}"
            )
            logger.debug(f"[min_token] {message}")

        return ValidationResult(
            is_valid=is_valid,
            actual_tokens=actual_tokens,
            min_threshold=min_threshold,
            task_type=task_type,
            message=message,
        )

    def _count_response_tokens(self, response: ParsedResponse) -> int:
        """Count tokens in a response.

        Uses actual usage data if available, otherwise estimates
        from content length.

        Args:
            response: The parsed response.

        Returns:
            int: Token count.
        """
        # Prefer actual usage data
        if response.completion_tokens > 0:
            return response.completion_tokens

        # Estimate from content
        return self._counter.count(response.content)

    def is_complete(
        self,
        response: ParsedResponse,
        task_type: TaskType,
    ) -> bool:
        """Quick check if response meets minimum.

        Args:
            response: The parsed response.
            task_type: The task type.

        Returns:
            True if response meets minimum threshold.
        """
        return self.validate(response, task_type).is_valid
