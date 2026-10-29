"""
model.parser.gemini_parser — Parser for Gemini thinking format.

Gemini uses 'gemini-level' thinking format where thinking
is returned in a dedicated field in the response.
"""

from __future__ import annotations

from typing import Any

from model.connection.response_handler import ParsedResponse, ResponseHandler
from model.parser.base_parser import BaseParser
from utils.logger import get_logger

logger = get_logger(__name__)


class GeminiParser(BaseParser):
    """Parser for Gemini model responses (gemini-level thinking).

    Gemini thinking is typically returned as a separate field
    or as part of the response metadata.
    """

    @property
    def format_name(self) -> str:
        return "gemini-level"

    def parse(self, raw_response: dict[str, Any]) -> ParsedResponse:
        """Parse Gemini response. Falls back to generic handler."""
        return ResponseHandler.handle(raw_response)

    def extract_thinking(self, message: dict[str, Any]) -> str:
        """Extract thinking from Gemini response.

        Gemini may use 'thinking' field or model-specific metadata.
        """
        # Check standard thinking field
        thinking = message.get("thinking", "")
        if isinstance(thinking, str) and thinking:
            return thinking

        # Check for Gemini-specific thought field
        thought = message.get("thought", "")
        if isinstance(thought, str) and thought:
            return thought

        return ""
