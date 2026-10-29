"""
brain.generate_efficient_token.token_optimizer — Optimize token usage.

Reduces token usage by applying strategies to trim, compress,
or restructure messages to fit within model context windows.
"""

from __future__ import annotations

from typing import Any

from brain.generate_efficient_token.token_counter import TokenCounter
from utils.logger import get_logger

logger = get_logger(__name__)


class TokenOptimizer:
    """Optimizes message lists to fit within token budgets.

    Applies strategies in order of aggressiveness:
    1. Truncate oldest history messages
    2. Trim long individual messages
    3. Remove optional content (think prompts, examples)
    """

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        """Initialize the optimizer.

        Args:
            token_counter: TokenCounter instance. Creates one if not provided.
        """
        self._counter = token_counter or TokenCounter()

    def optimize(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        model: str = "",
        preserve_last_n: int = 4,
    ) -> list[dict[str, Any]]:
        """Optimize a message list to fit within a token budget.

        Preserves the system prompt (first message) and the most
        recent messages. Trims from the middle (oldest history).

        Args:
            messages: Full message list to optimize.
            max_tokens: Maximum total input tokens allowed.
            model: Model ID for accurate counting.
            preserve_last_n: Number of most recent messages to always keep.

        Returns:
            Optimized message list that fits within max_tokens.
        """
        current_tokens = self._counter.count_messages(messages, model)

        if current_tokens <= max_tokens:
            logger.debug(
                f"[optimizer] Messages fit within budget: "
                f"{current_tokens}/{max_tokens} tokens"
            )
            return messages

        logger.info(
            f"[optimizer] Optimizing: {current_tokens} → {max_tokens} tokens "
            f"({len(messages)} messages)"
        )

        # Strategy 1: Truncate oldest history messages
        result = self._truncate_old_messages(
            messages, max_tokens, model, preserve_last_n
        )

        result_tokens = self._counter.count_messages(result, model)
        if result_tokens <= max_tokens:
            logger.info(
                f"[optimizer] Truncation sufficient: "
                f"{result_tokens}/{max_tokens} tokens, "
                f"{len(result)} messages remaining"
            )
            return result

        # Strategy 2: Trim long individual messages
        result = self._trim_long_messages(result, max_tokens, model)

        result_tokens = self._counter.count_messages(result, model)
        logger.info(
            f"[optimizer] Final: {result_tokens}/{max_tokens} tokens, "
            f"{len(result)} messages"
        )

        return result

    def _truncate_old_messages(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        model: str,
        preserve_last_n: int,
    ) -> list[dict[str, Any]]:
        """Remove oldest history messages until within budget.

        Keeps:
        - System message (first, if role=system)
        - The last `preserve_last_n` messages

        Removes messages from the middle (oldest history first).
        """
        if len(messages) <= preserve_last_n + 1:
            return list(messages)

        # Separate system prompt from the rest
        has_system = messages and messages[0].get("role") == "system"
        system = [messages[0]] if has_system else []
        rest = messages[1:] if has_system else list(messages)

        # Always preserve last N
        tail = rest[-preserve_last_n:]
        middle = rest[:-preserve_last_n]

        # Remove from middle until we fit
        while middle:
            candidate = system + middle + tail
            tokens = self._counter.count_messages(candidate, model)
            if tokens <= max_tokens:
                return candidate
            middle.pop(0)  # Remove oldest

        return system + tail

    def _trim_long_messages(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        model: str,
        max_message_tokens: int = 2000,
    ) -> list[dict[str, Any]]:
        """Trim individual messages that exceed a length threshold.

        Args:
            messages: Message list.
            max_tokens: Total budget.
            model: Model ID.
            max_message_tokens: Max tokens per individual message.

        Returns:
            Messages with long ones trimmed.
        """
        trimmed = []

        for msg in messages:
            content = msg.get("content", "")
            if not isinstance(content, str):
                trimmed.append(msg)
                continue

            msg_tokens = self._counter.count(content, model)
            if msg_tokens > max_message_tokens:
                # Truncate content, keeping the beginning
                ratio = max_message_tokens / msg_tokens
                keep_chars = int(len(content) * ratio)
                new_content = content[:keep_chars] + "\n\n[... truncated ...]"
                trimmed.append({**msg, "content": new_content})
            else:
                trimmed.append(msg)

        return trimmed

    def estimate_remaining_budget(
        self,
        messages: list[dict[str, Any]],
        context_window: int,
        model: str = "",
        output_buffer: int = 512,
    ) -> int:
        """Estimate how many tokens are left for output.

        Args:
            messages: Current message list.
            context_window: Model's context window size.
            model: Model ID.
            output_buffer: Buffer tokens to reserve.

        Returns:
            int: Available tokens for output.
        """
        input_tokens = self._counter.count_messages(messages, model)
        remaining = context_window - input_tokens - output_buffer
        return max(0, remaining)
