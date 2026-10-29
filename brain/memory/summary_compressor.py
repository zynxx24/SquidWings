"""
brain.memory.summary_compressor — Compress old conversation history.

Uses a cheap/fast model to summarize old conversation messages
into a compact summary, freeing context window space for new messages.
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class SummaryCompressor:
    """Compresses old conversation messages into a summary.

    When conversation history grows too long, this compressor
    takes a batch of old messages and produces a concise summary
    that preserves key context.

    The actual LLM call is optional — if no API client is provided,
    it falls back to a simple extractive summary.
    """

    # Prompt used to ask the LLM to summarize conversation
    SUMMARY_PROMPT: str = (
        "Summarize the following conversation concisely. "
        "Preserve key decisions, facts, and context. "
        "Keep the summary under 200 words.\n\n"
        "Conversation:\n{conversation}"
    )

    def __init__(
        self,
        api_client: Any = None,
        summary_model: str = "ag/gemini-3.8-flash-high",
    ) -> None:
        """Initialize the compressor.

        Args:
            api_client: Optional APIClient for LLM-based summarization.
                        If None, uses extractive fallback.
            summary_model: Model ID to use for summarization
                           (should be the cheapest/fastest).
        """
        self._client = api_client
        self._summary_model = summary_model

    def compress_history(
        self,
        messages: list[dict[str, Any]],
        max_summary_length: int = 500,
    ) -> dict[str, Any]:
        """Compress a list of messages into a single summary message.

        Args:
            messages: Old messages to summarize.
            max_summary_length: Max character length for the summary.

        Returns:
            A single message dict with role='system' containing the summary.
        """
        if not messages:
            return {"role": "system", "content": "[No previous context]"}

        # Build extractive summary (always available, no API needed)
        summary = self._extractive_summary(messages, max_summary_length)

        logger.info(
            f"[compressor] Compressed {len(messages)} messages → "
            f"{len(summary)} chars summary"
        )

        return {
            "role": "system",
            "content": f"[Summary of earlier conversation]\n{summary}",
        }

    async def compress_history_async(
        self,
        messages: list[dict[str, Any]],
        max_summary_length: int = 500,
    ) -> dict[str, Any]:
        """Compress using LLM summarization (async).

        Falls back to extractive summary if API call fails.

        Args:
            messages: Old messages to summarize.
            max_summary_length: Max character length for the summary.

        Returns:
            A single summary message dict.
        """
        if not self._client or not messages:
            return self.compress_history(messages, max_summary_length)

        try:
            conversation_text = self._format_for_llm(messages)
            prompt = self.SUMMARY_PROMPT.format(conversation=conversation_text)

            response = await self._client.chat_completion(
                model=self._summary_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300,
            )

            summary = response.get("choices", [{}])[0].get(
                "message", {}
            ).get("content", "")

            if summary:
                logger.info(
                    f"[compressor] LLM summary: {len(messages)} messages → "
                    f"{len(summary)} chars"
                )
                return {
                    "role": "system",
                    "content": f"[Summary of earlier conversation]\n{summary}",
                }

        except Exception as e:
            logger.warning(f"[compressor] LLM summarization failed: {e}")

        # Fallback to extractive
        return self.compress_history(messages, max_summary_length)

    def _extractive_summary(
        self,
        messages: list[dict[str, Any]],
        max_length: int,
    ) -> str:
        """Build a simple extractive summary from messages.

        Takes key sentences from each message to build a summary.

        Args:
            messages: Messages to summarize.
            max_length: Maximum summary length in characters.

        Returns:
            Summary string.
        """
        points: list[str] = []

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if not isinstance(content, str) or not content.strip():
                continue

            # Take the first meaningful line from each message
            lines = content.strip().split("\n")
            first_line = ""
            for line in lines:
                stripped = line.strip()
                if stripped and len(stripped) > 10:
                    first_line = stripped
                    break

            if not first_line:
                first_line = content.strip()[:80]

            # Truncate long lines
            if len(first_line) > 120:
                first_line = first_line[:117] + "..."

            points.append(f"[{role}] {first_line}")

        # Join and truncate to max length
        summary = "\n".join(points)
        if len(summary) > max_length:
            summary = summary[:max_length - 3] + "..."

        return summary

    def _format_for_llm(self, messages: list[dict[str, Any]]) -> str:
        """Format messages as a conversation for LLM summarization.

        Args:
            messages: Messages to format.

        Returns:
            Formatted conversation string.
        """
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                # Limit each message to prevent prompt from being too long
                text = content.strip()[:300]
                parts.append(f"{role}: {text}")

        return "\n".join(parts)
