"""
brain.memory.message_builder — Build API message payloads.
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class MessageBuilder:
    """Builds complete message lists for the chat API.

    Assembles system prompt, thinking prompt, history, and user
    input into a properly ordered message list.
    """

    @staticmethod
    def build(
        system_prompt: str | None = None,
        think_prompt: str | None = None,
        user_input: str = "",
        history: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete messages list.

        Order:
        1. System instructions (base/task prompt plus thinking guidance)
        2. Conversation history
        3. User input
        4. Tool results (if any)

        Args:
            system_prompt: System instruction text.
            think_prompt: Additional privileged thinking guidance.
            user_input: User's message text.
            history: Previous conversation messages.
            tool_results: Tool call results to append.

        Returns:
            Ordered list of message dicts.
        """
        messages: list[dict[str, Any]] = []

        system_parts = [
            part.strip() for part in (system_prompt, think_prompt) if part and part.strip()
        ]
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        if history:
            messages.extend(history)

        if user_input:
            messages.append({"role": "user", "content": user_input})

        if tool_results:
            messages.extend(tool_results)

        logger.debug(f"[msg_builder] Built {len(messages)} messages")
        return messages
