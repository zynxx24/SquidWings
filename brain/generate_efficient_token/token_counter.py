"""
brain.generate_efficient_token.token_counter — Count tokens for messages.

Uses tiktoken for OpenAI-compatible models and a character-based
approximation for other models (DeepSeek, Gemini).
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# Approximate chars-per-token ratios by model family
_CHARS_PER_TOKEN: dict[str, float] = {
    "openai": 4.0,
    "claude": 3.5,
    "gemini": 4.0,
    "deepseek": 3.8,
    "default": 4.0,
}


class TokenCounter:
    """Count tokens for messages and text.

    Uses tiktoken when available for OpenAI models,
    falls back to character-based approximation otherwise.
    """

    def __init__(self) -> None:
        self._tiktoken_encoder: Any = None
        self._tiktoken_available = False
        self._try_load_tiktoken()

    def _try_load_tiktoken(self) -> None:
        """Attempt to load tiktoken for accurate counting."""
        try:
            import tiktoken
            self._tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
            self._tiktoken_available = True
            logger.debug("[token_counter] tiktoken loaded (cl100k_base)")
        except (ImportError, Exception):
            self._tiktoken_available = False
            logger.debug("[token_counter] tiktoken not available, using approximation")

    def count(self, text: str, model: str = "") -> int:
        """Count tokens in a text string.

        Args:
            text: The text to count tokens for.
            model: Optional model ID for model-specific counting.

        Returns:
            int: Estimated token count.
        """
        if not text:
            return 0

        # Use tiktoken for accurate counting when available
        if self._tiktoken_available and self._tiktoken_encoder:
            try:
                return len(self._tiktoken_encoder.encode(text))
            except Exception:
                pass

        # Fallback: character-based approximation
        ratio = self._get_ratio(model)
        return max(1, int(len(text) / ratio))

    def count_messages(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
    ) -> int:
        """Count total tokens across a list of API messages.

        Accounts for message overhead (role, formatting tokens).

        Args:
            messages: List of API message dicts.
            model: Optional model ID.

        Returns:
            int: Estimated total token count.
        """
        total = 0

        for msg in messages:
            # Overhead per message (role tokens, delimiters)
            total += 4  # <im_start>, role, \n, <im_end>

            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.count(content, model)
            elif isinstance(content, list):
                # Content blocks (e.g., multimodal)
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "")
                        if text:
                            total += self.count(text, model)
                        # Image tokens are estimated separately
                        if block.get("type") == "image_url":
                            total += 85  # Base image token cost

            # Tool calls overhead
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function")
                    if isinstance(func, dict):
                        total += self.count(func.get("name") or "", model)
                        total += self.count(func.get("arguments") or "", model)

        # Final overhead
        total += 2  # priming tokens

        return total

    def count_conversation_tokens(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        user_input: str,
        model: str = "",
    ) -> int:
        """Count tokens for a full conversation context.

        Args:
            system_prompt: System prompt text.
            history: Conversation history messages.
            user_input: Current user input.
            model: Optional model ID.

        Returns:
            int: Total estimated tokens.
        """
        messages: list[dict[str, Any]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        if user_input:
            messages.append({"role": "user", "content": user_input})

        return self.count_messages(messages, model)

    def _get_ratio(self, model: str) -> float:
        """Get chars-per-token ratio for a model.

        Args:
            model: Model ID string.

        Returns:
            float: Characters per token ratio.
        """
        model_lower = model.lower()
        for prefix, ratio in _CHARS_PER_TOKEN.items():
            if prefix in model_lower:
                return ratio
        return _CHARS_PER_TOKEN["default"]
