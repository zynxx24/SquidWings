"""
brain.generate_efficient_token.compression_strategy — Prompt compression strategies.

Provides pluggable strategies for compressing conversation history
when the context window is tight.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from brain.generate_efficient_token.token_counter import TokenCounter
from utils.logger import get_logger

logger = get_logger(__name__)


class CompressionStrategy(ABC):
    """Abstract base for message compression strategies."""

    @abstractmethod
    def compress(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
        model: str = "",
    ) -> list[dict[str, Any]]:
        """Compress messages to fit within target token count.

        Args:
            messages: Messages to compress.
            target_tokens: Target maximum token count.
            model: Model ID for token counting.

        Returns:
            Compressed message list.
        """
        ...


class TruncateStrategy(CompressionStrategy):
    """Simple truncation: remove oldest messages.

    Keeps the system message and the most recent messages,
    removing from the middle.
    """

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        self._counter = token_counter or TokenCounter()

    def compress(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
        model: str = "",
    ) -> list[dict[str, Any]]:
        """Truncate oldest messages until within target.

        Args:
            messages: Full message list.
            target_tokens: Target token budget.
            model: Model ID.

        Returns:
            Truncated message list.
        """
        if self._counter.count_messages(messages, model) <= target_tokens:
            return messages

        # Keep system + last message minimum
        has_system = messages and messages[0].get("role") == "system"
        system = [messages[0]] if has_system else []
        rest = messages[1:] if has_system else list(messages)

        # Binary search for how many recent messages we can keep
        for keep in range(len(rest), 0, -1):
            candidate = system + rest[-keep:]
            if self._counter.count_messages(candidate, model) <= target_tokens:
                removed = len(rest) - keep
                logger.info(
                    f"[truncate] Removed {removed} oldest messages, "
                    f"keeping {len(candidate)}"
                )
                return candidate

        # Absolute minimum: system prompt only
        return system


class SlidingWindowStrategy(CompressionStrategy):
    """Keep a sliding window of the most recent messages.

    Always keeps the system message and a fixed number of
    recent messages.
    """

    def __init__(
        self,
        window_size: int = 20,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize with window size.

        Args:
            window_size: Maximum number of non-system messages to keep.
            token_counter: Optional token counter.
        """
        self.window_size = window_size
        self._counter = token_counter or TokenCounter()

    def compress(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
        model: str = "",
    ) -> list[dict[str, Any]]:
        """Apply sliding window to messages.

        Args:
            messages: Full message list.
            target_tokens: Target token budget.
            model: Model ID.

        Returns:
            Windowed message list.
        """
        has_system = messages and messages[0].get("role") == "system"
        system = [messages[0]] if has_system else []
        rest = messages[1:] if has_system else list(messages)

        # Apply window
        windowed = rest[-self.window_size:]
        result = system + windowed

        # Further truncate if still over budget
        while (
            len(windowed) > 1
            and self._counter.count_messages(result, model) > target_tokens
        ):
            windowed.pop(0)
            result = system + windowed

        removed = len(messages) - len(result)
        if removed > 0:
            logger.info(
                f"[sliding_window] Removed {removed} messages, "
                f"window={len(windowed)}"
            )

        return result


class SummarizeStrategy(CompressionStrategy):
    """Replace old messages with a summary message.

    Replaces a batch of old messages with a single system-level
    summary. Requires an external summarizer function.

    NOTE: The actual summarization call (e.g., using a cheap LLM)
    is injected via the `summarize_fn` parameter.
    """

    def __init__(
        self,
        summarize_fn: Any = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize with a summarizer function.

        Args:
            summarize_fn: Async callable that takes a list of messages
                          and returns a summary string. If None, falls
                          back to simple concatenation.
            token_counter: Optional token counter.
        """
        self._summarize_fn = summarize_fn
        self._counter = token_counter or TokenCounter()

    def compress(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
        model: str = "",
    ) -> list[dict[str, Any]]:
        """Replace old messages with a summary.

        Keeps system prompt and recent messages. Old messages
        in the middle are replaced with a summary.

        Args:
            messages: Full message list.
            target_tokens: Target token budget.
            model: Model ID.

        Returns:
            Compressed message list with summary.
        """
        if self._counter.count_messages(messages, model) <= target_tokens:
            return messages

        has_system = messages and messages[0].get("role") == "system"
        system = [messages[0]] if has_system else []
        rest = messages[1:] if has_system else list(messages)

        # Keep last 4 messages, summarize the rest
        keep_recent = min(4, len(rest))
        recent = rest[-keep_recent:] if keep_recent else []
        to_summarize = rest[:-keep_recent] if keep_recent else rest

        if not to_summarize:
            return messages

        # Build summary text
        summary_text = self._build_summary(to_summarize)
        summary_msg = {
            "role": "system",
            "content": f"[Previous conversation summary]\n{summary_text}",
        }

        result = system + [summary_msg] + recent
        logger.info(
            f"[summarize] Compressed {len(to_summarize)} messages → summary, "
            f"keeping {len(recent)} recent"
        )

        return result

    def _build_summary(self, messages: list[dict[str, Any]]) -> str:
        """Build a summary from messages.

        If no external summarizer is available, creates a simple
        concatenation of key points.
        """
        points: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                # Take first 100 chars as a summary point
                snippet = content.strip()[:100]
                if len(content.strip()) > 100:
                    snippet += "..."
                points.append(f"- [{role}] {snippet}")

        return "\n".join(points[-10:])  # Keep last 10 points max
