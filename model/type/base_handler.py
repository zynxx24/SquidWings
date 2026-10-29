"""
model.type.base_handler — Abstract base for all task-type handlers.

Every task type (code, math, activity, etc.) has a handler that
customizes request parameters, system prompts, and post-processing
for optimal results with the selected model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from brain.max_token.max_token_config import MaxTokenConfig
from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.registry.model_info import ModelInfo
from utils.logger import get_logger

logger = get_logger(__name__)

# Shared token-limit config (plain dicts; no I/O when config_path=None).
_MAX_TOKEN_CONFIG = MaxTokenConfig()

# Thinking models spend max_tokens on reasoning_content BEFORE any visible
# content is produced. Without headroom, thinking alone can exhaust the whole
# budget (finish_reason="length" + empty content → agent stops silently).
REASONING_MIN_HEADROOM = 32768


class BaseTypeHandler(ABC):
    """Abstract base for task-type-specific handlers.

    Each handler is responsible for:
    1. Preparing the request (temperature, thinking, tools)
    2. Building the system prompt
    3. Post-processing the response

    Subclasses MUST implement:
    - task_type (property)
    - default_temperature (property)
    - get_system_prompt()
    - post_process()

    Subclasses MAY override:
    - prepare_request_params()
    - get_think_prompt()
    """

    @property
    @abstractmethod
    def task_type(self) -> TaskType:
        """The task type this handler serves."""
        ...

    @property
    @abstractmethod
    def default_temperature(self) -> float:
        """Default temperature for this task type."""
        ...

    @property
    def enable_thinking(self) -> bool:
        """Whether to enable thinking by default."""
        return True

    @property
    def enable_tools(self) -> bool:
        """Whether to enable tool calling by default."""
        return False

    @property
    def default_max_tokens(self) -> int:
        """Default max output tokens."""
        return 8192

    def prepare_request_params(self, model: ModelInfo, **overrides: Any) -> dict[str, Any]:
        """Prepare request parameters optimized for this task type.

        Args:
            model: The model that will process this request.
            **overrides: Optional parameter overrides.

        Returns:
            dict: Request parameters (temperature, max_tokens, etc.).
        """
        raw_max = overrides.get("max_tokens", self.default_max_tokens)
        requested_max = int(raw_max) if raw_max else self.default_max_tokens

        # Add thinking config if model supports it
        if self.enable_thinking and model.supports_reasoning:
            # Reasoning tokens share the same max_tokens budget as the
            # visible content. Raise the floor so thinking cannot starve
            # the content output, capped by the model's configured limit.
            registry_max = getattr(model, "max_output", 0)
            if isinstance(registry_max, int) and registry_max > 0:
                model_max = registry_max
            else:
                model_max = _MAX_TOKEN_CONFIG.get_model_max(model.id)
            effective_max = min(model_max, max(requested_max, REASONING_MIN_HEADROOM))
        else:
            effective_max = requested_max

        params: dict[str, Any] = {
            "temperature": overrides.get("temperature", self.default_temperature),
            "max_tokens": max(1, effective_max),
        }

        if self.enable_thinking and model.supports_reasoning:
            params["thinking_format"] = model.thinking_format
            params["enable_thinking"] = True

        return params

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Get the system prompt for this task type.

        Returns:
            str: The system prompt text.
        """
        ...

    def get_think_prompt(self) -> str:
        """Get the thinking instruction prompt.

        Returns:
            str: Thinking prompt text, or empty string.
        """
        return ""

    @abstractmethod
    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Post-process the model response.

        Args:
            response: The parsed response from the model.

        Returns:
            ParsedResponse: Processed response.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(task_type={self.task_type.value})"
