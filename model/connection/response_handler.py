"""
model.connection.response_handler — Handle and parse API responses.

Provides unified response parsing regardless of model format,
extracting content, thinking, tool calls, and usage stats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ParsedResponse:
    """Unified response structure from any model.

    All model responses are normalized into this format,
    regardless of the underlying thinking format.

    Attributes:
        content: The main text content of the response.
        thinking: The model's thinking/reasoning text (if any).
        tool_calls: List of tool calls requested by the model.
        finish_reason: Why the model stopped (stop, tool_calls, length).
        model: The model ID that generated this response.
        usage: Token usage statistics.
        raw: The raw API response dict.
    """

    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def has_tool_calls(self) -> bool:
        """Check if the response contains tool calls."""
        return len(self.tool_calls) > 0

    @property
    def has_content(self) -> bool:
        """Check if the response contains text content."""
        return bool(self.content.strip())

    @property
    def has_thinking(self) -> bool:
        """Check if the response contains thinking text."""
        return bool(self.thinking.strip())

    @property
    def prompt_tokens(self) -> int:
        """Number of input tokens used."""
        return (self.usage or {}).get("prompt_tokens", 0)

    @property
    def completion_tokens(self) -> int:
        """Number of output tokens generated."""
        return (self.usage or {}).get("completion_tokens", 0)

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return (self.usage or {}).get("total_tokens", 0)

    @property
    def cached_tokens(self) -> int:
        """Number of cached tokens read from prompt cache."""
        u = self.usage or {}
        details = u.get("prompt_tokens_details")
        if isinstance(details, dict) and "cached_tokens" in details:
            return details.get("cached_tokens") or 0
        return u.get("cached_tokens") or u.get("cache_read_input_tokens") or 0

    @property
    def cache_creation_tokens(self) -> int:
        """Number of input tokens written to prompt cache."""
        return (self.usage or {}).get("cache_creation_input_tokens") or 0

    @property
    def cache_hit_ratio(self) -> float:
        """Cache hit ratio between 0.0 and 1.0."""
        p = self.prompt_tokens
        if p <= 0:
            return 0.0
        return min(1.0, max(0.0, self.cached_tokens / p))

    @property
    def is_complete(self) -> bool:
        """Check if the response completed normally (not truncated)."""
        return self.finish_reason in ("stop", "end_turn")


class ResponseHandler:
    """Parses raw API responses into unified ParsedResponse objects.

    Handles extraction of content, thinking, tool calls, and usage
    from different model response formats.
    """

    @staticmethod
    def handle(raw_response: dict[str, Any]) -> ParsedResponse:
        """Parse a raw API response into a ParsedResponse.

        Args:
            raw_response: The raw JSON response from the API.

        Returns:
            ParsedResponse: Unified parsed response.
        """
        if not isinstance(raw_response, dict):
            return ParsedResponse(raw={"_raw": raw_response})

        choices = raw_response.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            logger.warning("[response] Empty choices in response")
            return ParsedResponse(raw=raw_response)

        choice = choices[0]
        if not isinstance(choice, dict):
            return ParsedResponse(raw=raw_response)

        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}

        # Extract content
        content = message.get("content") or ""

        # Extract thinking (varies by model format)
        thinking = ResponseHandler._extract_thinking(message, content)

        # If thinking was embedded in content, clean it out
        if thinking and "<think>" in content:
            content = ResponseHandler._remove_think_tags(content)

        # Extract tool calls
        tool_calls = ResponseHandler._extract_tool_calls(message)

        # Extract usage
        usage = raw_response.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        # Extract finish reason
        finish_reason = choice.get("finish_reason") or ""

        # Extract model
        model = raw_response.get("model") or ""

        parsed = ParsedResponse(
            content=content.strip(),
            thinking=thinking.strip(),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model=model,
            usage=usage,
            raw=raw_response,
        )

        logger.debug(
            f"[response] Parsed: content={len(parsed.content)} chars, "
            f"thinking={len(parsed.thinking)} chars, "
            f"tool_calls={len(parsed.tool_calls)}, "
            f"finish={parsed.finish_reason}"
        )

        return parsed

    @staticmethod
    def _extract_thinking(message: dict[str, Any], content: str) -> str:
        """Extract thinking text from the response.

        Checks multiple locations:
        1. message.thinking (OpenAI/Claude format)
        2. message.reasoning_content (some APIs)
        3. <think>...</think> tags in content (DeepSeek format)

        Args:
            message: The message dict from the response.
            content: The content string.

        Returns:
            str: Extracted thinking text, or empty string.
        """
        import re

        # Check dedicated thinking field
        thinking = message.get("thinking", "")
        if isinstance(thinking, str) and thinking:
            return thinking

        # Check reasoning_content field
        reasoning = message.get("reasoning_content", "")
        if isinstance(reasoning, str) and reasoning:
            return reasoning

        # Check for <think> tags in content (DeepSeek format)
        think_match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
        if think_match:
            return think_match.group(1).strip()

        return ""

    @staticmethod
    def _remove_think_tags(content: str) -> str:
        """Remove <think>...</think> tags from content.

        Args:
            content: Content string potentially containing think tags.

        Returns:
            str: Content with think tags removed.
        """
        import re
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    @staticmethod
    def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool calls from the response message.

        Args:
            message: The message dict from the response.

        Returns:
            list: List of tool call dicts with id, name, and arguments.
        """
        import json

        raw_calls = message.get("tool_calls")
        if not raw_calls or not isinstance(raw_calls, list):
            return []

        tool_calls = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                function = {}
            args_str = function.get("arguments", "{}")

            # Parse arguments (may be string or already dict)
            if isinstance(args_str, str):
                try:
                    arguments = json.loads(args_str)
                except json.JSONDecodeError:
                    logger.warning(
                        f"[response] Failed to parse tool args: {args_str[:100]}"
                    )
                    arguments = {"raw": args_str}
            else:
                arguments = args_str

            tool_calls.append({
                "id": call.get("id") or "",
                "name": function.get("name") or "",
                "arguments": arguments,
            })

        return tool_calls

    @staticmethod
    def handle_error(raw_response: dict[str, Any]) -> str:
        """Extract error message from an error response.

        Args:
            raw_response: The raw error response.

        Returns:
            str: Human-readable error message.
        """
        if not isinstance(raw_response, dict):
            return str(raw_response)
        error = raw_response.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            return message if isinstance(message, str) else str(error)
        return str(error) if error is not None else str(raw_response)
