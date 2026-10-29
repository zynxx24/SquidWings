"""
model.parser.content_extractor — Extract structured content from responses.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any


class ContentExtractor:
    """Extracts structured content blocks from model output.

    Supports extraction of code blocks, math expressions,
    lists, and other structured content.
    """

    @staticmethod
    def extract_code_blocks(text: str) -> list[dict[str, str]]:
        """Extract fenced code blocks.

        Returns:
            list of dicts with 'language' and 'code' keys.
        """
        blocks = []
        for match in re.finditer(r"```(\w*)\n(.*?)```", text, re.DOTALL):
            blocks.append({
                "language": match.group(1) or "text",
                "code": match.group(2).strip(),
            })
        return blocks

    @staticmethod
    def extract_math_expressions(text: str) -> list[str]:
        """Extract LaTeX math expressions ($...$ or $$...$$).

        Returns:
            list of math expression strings.
        """
        expressions = []
        # Block math: $$...$$
        for match in re.finditer(r"\$\$(.*?)\$\$", text, re.DOTALL):
            expressions.append(match.group(1).strip())
        # Inline math: $...$
        for match in re.finditer(r"(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)", text):
            expressions.append(match.group(1).strip())
        return expressions

    @staticmethod
    def extract_numbered_list(text: str) -> list[str]:
        """Extract numbered list items.

        Returns:
            list of list item strings.
        """
        items = []
        for match in re.finditer(r"^\s*\d+[\.\)]\s+(.+)$", text, re.MULTILINE):
            items.append(match.group(1).strip())
        return items

    @staticmethod
    def extract_bullet_list(text: str) -> list[str]:
        """Extract bullet list items.

        Returns:
            list of list item strings.
        """
        items = []
        for match in re.finditer(r"^\s*[-*•]\s+(.+)$", text, re.MULTILINE):
            items.append(match.group(1).strip())
        return items

    @staticmethod
    def extract_headers(text: str) -> list[dict[str, Any]]:
        """Extract markdown headers.

        Returns:
            list of dicts with 'level' and 'text' keys.
        """
        headers = []
        for match in re.finditer(r"^(#{1,6})\s+(.+)$", text, re.MULTILINE):
            headers.append({
                "level": len(match.group(1)),
                "text": match.group(2).strip(),
            })
        return headers

    @staticmethod
    def remove_code_blocks(text: str) -> str:
        """Remove all fenced code blocks from text."""
        return re.sub(r"```\w*\n.*?```", "", text, flags=re.DOTALL).strip()

    @staticmethod
    def extract_json_blocks(text: str) -> list[dict[str, Any]]:
        """Extract and parse JSON blocks from text.

        Returns:
            list of parsed JSON objects.
        """
        import json
        blocks = []
        for match in re.finditer(r"```json\n(.*?)```", text, re.DOTALL):
            with contextlib.suppress(json.JSONDecodeError):
                blocks.append(json.loads(match.group(1).strip()))
        return blocks
