"""
model.parser.claude_parser — Parsers for Claude thinking formats.

Handles both claude-adaptive (Sonnet) and claude-budget (Opus)
thinking formats. Claude returns thinking in a dedicated field.
"""

from __future__ import annotations

from typing import Any

from model.connection.response_handler import ParsedResponse, ResponseHandler
from model.parser.base_parser import BaseParser
from utils.logger import get_logger

logger = get_logger(__name__)


class ClaudeAdaptiveParser(BaseParser):
    """Parser for Claude Sonnet (claude-adaptive thinking).

    Claude adaptive thinking can be toggled on/off.
    When enabled, thinking appears in the 'thinking' field.
    """

    @property
    def format_name(self) -> str:
        return "claude-adaptive"

    def parse(self, raw_response: dict[str, Any]) -> ParsedResponse:
        """Parse Claude Sonnet response."""
        parsed = ResponseHandler.handle(raw_response)

        # Claude may return thinking in content blocks
        choices = raw_response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content_blocks = message.get("content", [])

            # Handle content blocks format (list of dicts)
            if (
                isinstance(content_blocks, list)
                and content_blocks
                and isinstance(content_blocks[0], dict)
            ):
                thinking_parts = []
                text_parts = []
                for block in content_blocks:
                    if block.get("type") == "thinking":
                        thinking_parts.append(block.get("thinking", ""))
                    elif block.get("type") == "text":
                        text_parts.append(block.get("text", ""))

                if thinking_parts:
                    parsed.thinking = "\n".join(thinking_parts)
                if text_parts:
                    parsed.content = "\n".join(text_parts)

        return parsed

    def extract_thinking(self, message: dict[str, Any]) -> str:
        """Extract thinking from Claude Sonnet response."""
        thinking = message.get("thinking")
        if isinstance(thinking, str) and thinking:
            return thinking
        if thinking:
            return str(thinking)

        # Check content blocks
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    val = block.get("thinking", "")
                    return str(val) if val else ""

        return ""


class ClaudeBudgetParser(BaseParser):
    """Parser for Claude Opus (claude-budget thinking).

    Claude budget thinking uses a token budget for thinking.
    Thinking appears in the 'thinking' field with deeper reasoning.
    """

    @property
    def format_name(self) -> str:
        return "claude-budget"

    def parse(self, raw_response: dict[str, Any]) -> ParsedResponse:
        """Parse Claude Opus response."""
        # Use the same logic as adaptive parser
        adaptive = ClaudeAdaptiveParser()
        return adaptive.parse(raw_response)

    def extract_thinking(self, message: dict[str, Any]) -> str:
        """Extract thinking from Claude Opus response."""
        adaptive = ClaudeAdaptiveParser()
        return adaptive.extract_thinking(message)
