"""
model.connection.request_builder — Build API request payloads.

Constructs properly formatted request payloads for the chat
completions API, handling differences between thinking formats
(gemini-level, claude-adaptive, claude-budget, openai, deepseek).
"""

from __future__ import annotations

from typing import Any

from config.constants import ThinkingFormat
from utils.logger import get_logger

logger = get_logger(__name__)


class RequestBuilder:
    """Builds chat completion request payloads.

    Handles model-specific payload construction including
    different thinking format configurations.
    """

    @staticmethod
    def build_chat_request(
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int = 8192,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        thinking_format: ThinkingFormat | str | None = None,
        enable_thinking: bool = True,
        thinking_budget: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build a complete chat completion request payload.

        Adapts the payload based on the model's thinking format:
        - gemini-level: Uses thinking config with level
        - claude-adaptive: Uses thinking config toggle
        - claude-budget: Uses thinking with token budget
        - openai: Uses standard reasoning params
        - deepseek: Uses standard params (thinking in content)

        Args:
            model: Model ID.
            messages: Chat messages.
            temperature: Sampling temperature.
            max_tokens: Max output tokens.
            tools: Optional tool schemas.
            stream: Whether to stream.
            thinking_format: The model's thinking format.
            enable_thinking: Whether to enable thinking.
            thinking_budget: Token budget for thinking (claude-budget).
            **extra: Additional parameters passed through.

        Returns:
            dict: Complete request payload ready for API call.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        # Add tools if provided
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Add thinking configuration based on format
        if thinking_format:
            thinking_config = RequestBuilder._build_thinking_config(
                thinking_format=thinking_format,
                enable_thinking=enable_thinking,
                thinking_budget=thinking_budget,
            )
            if thinking_config:
                payload.update(thinking_config)

        # Merge any extra parameters
        payload.update(extra)

        logger.debug(
            f"[request_builder] Built request: model={model}, "
            f"thinking={thinking_format}, msgs={len(messages)}"
        )

        return payload

    @staticmethod
    def _build_thinking_config(
        thinking_format: ThinkingFormat | str,
        enable_thinking: bool = True,
        thinking_budget: int | None = None,
    ) -> dict[str, Any]:
        """Build thinking-specific parameters.

        Args:
            thinking_format: The thinking format to configure.
            enable_thinking: Whether thinking should be enabled.
            thinking_budget: Optional token budget for thinking.

        Returns:
            dict: Thinking config params to merge into request.
        """
        fmt = thinking_format if isinstance(thinking_format, str) else thinking_format.value

        if fmt == "gemini-level":
            # Gemini uses thinking level (cannot disable)
            return {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": thinking_budget or 8192,
                }
            }

        elif fmt == "claude-adaptive":
            # Claude Sonnet — can toggle thinking on/off
            if not enable_thinking:
                return {}
            return {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": thinking_budget or 10240,
                }
            }

        elif fmt == "claude-budget":
            # Claude Opus — budget-based thinking
            if not enable_thinking:
                return {}
            return {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": thinking_budget or 16384,
                }
            }

        elif fmt == "openai":
            # OpenAI format — uses reasoning_effort or similar
            if not enable_thinking:
                return {}
            config: dict[str, Any] = {}
            if thinking_budget:
                config["max_completion_tokens"] = thinking_budget
            return config

        elif fmt == "deepseek":
            # DeepSeek — thinking embedded in <think> tags
            if not enable_thinking:
                return {}
            return {}

        else:
            logger.warning(f"[request_builder] Unknown thinking format: {fmt}")
            return {}

    @staticmethod
    def build_messages(
        system_prompt: str | None = None,
        think_prompt: str | None = None,
        user_input: str = "",
        history: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build a complete messages list for the API.

        Message order:
        1. System instructions (base prompt plus thinking guidance)
        2. Conversation history (if any)
        3. User input
        4. Tool results (if any, appended at the end)

        Args:
            system_prompt: Optional system prompt text.
            think_prompt: Optional privileged thinking guidance.
            user_input: The user's input text.
            history: Optional previous conversation messages.
            tool_results: Optional tool call results to append.

        Returns:
            list: Ordered list of message dicts.
        """
        messages: list[dict[str, Any]] = []

        # 1. System instructions
        system_parts = [
            part.strip() for part in (system_prompt, think_prompt) if part and part.strip()
        ]
        if system_parts:
            messages.append(
                {
                    "role": "system",
                    "content": "\n\n".join(system_parts),
                }
            )

        # 2. History
        if history:
            messages.extend(history)

        # 3. User input
        if user_input:
            messages.append(
                {
                    "role": "user",
                    "content": user_input,
                }
            )

        # 4. Tool results
        if tool_results:
            messages.extend(tool_results)

        return messages
