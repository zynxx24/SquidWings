"""
model.parser.openai_parser — Parser for OpenAI thinking format.

Handles models that use the OpenAI thinking format, where thinking
is returned as a separate field in the response message.
Used by GPT-6, GPT-6.1 models (TH provider).
"""

from __future__ import annotations

from typing import Any

from model.connection.response_handler import ParsedResponse, ResponseHandler
from model.parser.base_parser import BaseParser
from utils.logger import get_logger

logger = get_logger(__name__)


class OpenAIParser(BaseParser):
    """Parser for OpenAI-format thinking responses.

    In the OpenAI format, thinking is returned as a separate field
    in the response message alongside the main content. The thinking
    field is typically called 'thinking' and can be toggled on/off.
    """

    @property
    def format_name(self) -> str:
        return "openai"

    def parse(self, raw_response: dict[str, Any]) -> ParsedResponse:
        """Parse an OpenAI-format response.

        Extracts both the main content and the thinking field
        from the response message.

        Args:
            raw_response: Raw JSON response from the API.

        Returns:
            ParsedResponse with content and thinking separated.
        """
        # Use the generic handler as base
        parsed = ResponseHandler.handle(raw_response)

        # OpenAI format: thinking is in message.thinking
        choices = raw_response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})

            # Extract thinking from dedicated field
            thinking = message.get("thinking", "")
            if thinking:
                parsed.thinking = thinking

            # Also check for reasoning field (some API variants)
            if not parsed.thinking:
                reasoning = message.get("reasoning", "")
                if reasoning:
                    parsed.thinking = reasoning

        return parsed

    def extract_thinking(self, message: dict[str, Any]) -> str:
        """Extract thinking from an OpenAI-format response message.

        Args:
            message: The message dict from choices[0].message.

        Returns:
            str: Extracted thinking text.
        """
        # Primary: dedicated thinking field
        thinking = message.get("thinking")
        if isinstance(thinking, str) and thinking:
            return thinking
        if thinking:
            return str(thinking)

        # Fallback: reasoning field
        reasoning = message.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            return reasoning
        if reasoning:
            return str(reasoning)

        return ""
