"""
model.parser.base_parser — Abstract base response parser.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from model.connection.response_handler import ParsedResponse


class BaseParser(ABC):
    """Abstract base for format-specific response parsers.

    Each thinking format (gemini-level, claude-adaptive, claude-budget,
    openai, deepseek) has its own parser that knows how to extract
    thinking content from the response.
    """

    @property
    @abstractmethod
    def format_name(self) -> str:
        """The thinking format this parser handles."""
        ...

    @abstractmethod
    def parse(self, raw_response: dict[str, Any]) -> ParsedResponse:
        """Parse a raw API response into a ParsedResponse.

        Args:
            raw_response: Raw JSON response from the API.

        Returns:
            ParsedResponse with content and thinking extracted.
        """
        ...

    @abstractmethod
    def extract_thinking(self, message: dict[str, Any]) -> str:
        """Extract thinking text from a response message.

        Args:
            message: The message dict from choices[0].message.

        Returns:
            str: Extracted thinking text.
        """
        ...
