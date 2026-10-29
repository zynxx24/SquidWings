"""
model.parser.deepseek_parser — Parser for DeepSeek thinking format.

Handles models that use the DeepSeek thinking format, where thinking
is either embedded in content within <think>...</think> tags or
returned in the 'reasoning_content' field.
Used by DeepSeek v4, mimo models (TH provider).
"""

from __future__ import annotations

import re
from typing import Any

from model.connection.response_handler import ParsedResponse, ResponseHandler
from model.parser.base_parser import BaseParser
from utils.logger import get_logger

logger = get_logger(__name__)

# Pattern to match <think>...</think> blocks in content
_THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


class DeepSeekParser(BaseParser):
    """Parser for DeepSeek-format thinking responses.

    DeepSeek models can output thinking in two ways:
    1. In a dedicated 'reasoning_content' field (preferred)
    2. Embedded in content within <think>...</think> tags

    This parser handles both formats and cleans the content
    by removing think tags when they appear inline.
    """

    @property
    def format_name(self) -> str:
        return "deepseek"

    def parse(self, raw_response: dict[str, Any]) -> ParsedResponse:
        """Parse a DeepSeek-format response.

        Extracts thinking from reasoning_content field or from
        <think> tags in content, and cleans the main content.

        Args:
            raw_response: Raw JSON response from the API.

        Returns:
            ParsedResponse with thinking and content separated.
        """
        # Use the generic handler as base
        parsed = ResponseHandler.handle(raw_response)

        choices = raw_response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "") or ""

            # Method 1: Check reasoning_content field
            reasoning = message.get("reasoning_content", "")
            if reasoning:
                parsed.thinking = reasoning
                logger.debug(
                    f"[deepseek_parser] Extracted thinking from "
                    f"reasoning_content ({len(reasoning)} chars)"
                )

            # Method 2: Check <think> tags in content
            if not parsed.thinking:
                think_match = _THINK_PATTERN.search(content)
                if think_match:
                    parsed.thinking = think_match.group(1).strip()
                    # Remove think tags from content
                    cleaned = _THINK_PATTERN.sub("", content).strip()
                    parsed.content = cleaned
                    logger.debug(
                        f"[deepseek_parser] Extracted thinking from "
                        f"<think> tags ({len(parsed.thinking)} chars)"
                    )

        return parsed

    def extract_thinking(self, message: dict[str, Any]) -> str:
        """Extract thinking from a DeepSeek-format response message.

        Args:
            message: The message dict from choices[0].message.

        Returns:
            str: Extracted thinking text.
        """
        # Check reasoning_content field first
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            return reasoning
        if reasoning:
            return str(reasoning)

        # Check for <think> tags in content
        content = message.get("content", "") or ""
        if isinstance(content, str):
            match = _THINK_PATTERN.search(content)
            if match:
                return match.group(1).strip()

        return ""
