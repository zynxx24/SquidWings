"""Dynamic output-token calculation within real model limits."""

from __future__ import annotations

from brain.max_token.max_token_config import MaxTokenConfig
from config.constants import TOKEN_BUFFER, TaskType
from utils.logger import get_logger

logger = get_logger(__name__)


class MaxTokenCalculator:
    """Calculate output capacity from model, task, input, and safety limits."""

    def __init__(self, config: MaxTokenConfig | None = None) -> None:
        self._config = config or MaxTokenConfig()

    def calculate(
        self,
        model_id: str,
        context_window: int,
        input_tokens: int,
        task_type: TaskType = TaskType.ACTIVITY,
        buffer: int = TOKEN_BUFFER,
        override: int | None = None,
    ) -> int:
        """Return a positive output limit without exceeding available space.

        ``override`` is a requested preference, not permission to bypass the
        model's hard output limit or its remaining context capacity.
        """
        model_max = self._config.get_model_max(model_id)
        task_default = self._config.get_task_default_max(task_type)
        requested = (
            override
            if override is not None and override > 0
            else task_default
        )
        available = (
            context_window
            - max(0, input_tokens)
            - max(0, buffer)
        )
        result = min(model_max, requested, max(1, available))

        logger.debug(
            "[max_calc] %s: model_max=%s requested=%s available=%s "
            "result=%s input=%s buffer=%s",
            model_id,
            model_max,
            requested,
            available,
            result,
            input_tokens,
            buffer,
        )
        return max(1, result)

    def can_fit_request(
        self,
        context_window: int,
        input_tokens: int,
        min_output: int = 100,
        buffer: int = TOKEN_BUFFER,
    ) -> bool:
        """Return whether input leaves room for the requested minimum output."""
        available = (
            context_window
            - max(0, input_tokens)
            - max(0, buffer)
        )
        return available >= max(1, min_output)
