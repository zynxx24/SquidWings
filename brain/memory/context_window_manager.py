"""Fit prompts to model windows without losing essential instructions."""

from __future__ import annotations

from typing import Any

from brain.generate_efficient_token.token_counter import TokenCounter
from brain.generate_efficient_token.token_optimizer import TokenOptimizer
from config.constants import TOKEN_BUFFER
from utils.logger import get_logger

logger = get_logger(__name__)


class ContextWindowManager:
    """Trim optional history while protecting instructions and new requests."""

    def __init__(
        self,
        token_counter: TokenCounter | None = None,
        token_optimizer: TokenOptimizer | None = None,
    ) -> None:
        self._counter = token_counter or TokenCounter()
        self._optimizer = token_optimizer or TokenOptimizer(self._counter)

    @staticmethod
    def _protected_indices(
        messages: list[dict[str, Any]],
        preserve_last_messages: int,
    ) -> set[int]:
        if not messages:
            return set()

        protected = {
            index
            for index, message in enumerate(messages)
            if message.get("role") == "system"
        }
        latest_user = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            None,
        )
        if latest_user is not None:
            protected.add(latest_user)
        else:
            protected.add(len(messages) - 1)

        preserve_count = max(1, preserve_last_messages)
        protected.update(
            range(max(0, len(messages) - preserve_count), len(messages))
        )
        return protected

    def _fallback_fit(
        self,
        messages: list[dict[str, Any]],
        protected: set[int],
        input_budget: int,
        model: str,
    ) -> list[dict[str, Any]]:
        """Add optional messages newest-first around protected messages."""
        selected = set(protected)
        optional = [
            index
            for index in range(len(messages))
            if index not in protected
        ]
        optional.reverse()

        for index in optional:
            candidate = sorted([*selected, index])
            candidate_messages = [messages[i] for i in candidate]
            if self._counter.count_messages(
                candidate_messages, model
            ) <= input_budget:
                selected.add(index)

        return [messages[index] for index in sorted(selected)]

    def _compress_middle(
        self,
        messages: list[dict[str, Any]],
        protected: set[int],
        input_budget: int,
        model: str,
    ) -> list[dict[str, Any]]:
        """Drop middle history when even the protected set is over budget.

        ``protected`` and ``input_budget`` are kept for API compatibility.

        Sending an over-budget prompt is what kills a session: the request
        times out, the turn fails, and the conversation ends mid-task. Dropping
        old turns is strictly better than that, as long as the model is told
        what happened and keeps the system prompt, the newest user instruction,
        and the most recent tool exchange.

        Returns the compressed list, or the original when nothing can be
        dropped without breaking the request.
        """
        if not messages:
            return messages

        keep: set[int] = set()
        for index, message in enumerate(messages):
            if message.get("role") == "system":
                keep.add(index)

        latest_user = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            None,
        )
        if latest_user is not None:
            keep.add(latest_user)

        # Keep the newest tool exchange so the model does not lose the result
        # it was about to act on.
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "tool":
                keep.add(index)
                if index > 0 and messages[index - 1].get("role") == "assistant":
                    keep.add(index - 1)
                break

        dropped = len(messages) - len(keep)
        if dropped <= 0:
            return messages

        marker = {
            "role": "user",
            "content": (
                f"[context] {dropped} earlier message(s) were dropped to fit "
                "the model window. Re-read any file you still need before "
                "answering; do not assume what the dropped history said."
            ),
        }

        compressed = [messages[index] for index in sorted(keep)]
        # Insert the marker after the leading system messages.
        insert_at = 0
        for position, message in enumerate(compressed):
            if message.get("role") == "system":
                insert_at = position + 1
        compressed.insert(insert_at, marker)

        compressed_tokens = self._counter.count_messages(compressed, model)
        if compressed_tokens >= self._counter.count_messages(messages, model):
            return messages
        return compressed

    def fit_to_window(
        self,
        messages: list[dict[str, Any]],
        context_window: int,
        max_output_tokens: int = 0,
        model: str = "",
        buffer: int = TOKEN_BUFFER,
        preserve_last_messages: int = 1,
    ) -> list[dict[str, Any]]:
        """Fit messages to the available input budget.

        If protected messages alone are too large, they remain intact so the
        application never silently sends a truncated user instruction.
        """
        input_budget = (
            context_window
            - max(0, max_output_tokens)
            - max(0, buffer)
        )
        protected = self._protected_indices(
            messages, preserve_last_messages
        )
        essential = [
            message
            for index, message in enumerate(messages)
            if index in protected
        ]

        if input_budget <= 0:
            logger.error(
                "[ctx_window] No input budget: window=%s output=%s buffer=%s",
                context_window,
                max_output_tokens,
                buffer,
            )
            return essential

        current_tokens = self._counter.count_messages(messages, model)
        if current_tokens <= input_budget:
            return messages

        logger.info(
            "[ctx_window] Trimming: %s -> %s tokens",
            current_tokens,
            input_budget,
        )
        optimized = self._optimizer.optimize(
            messages=messages,
            max_tokens=input_budget,
            model=model,
        )
        optimized_tokens = self._counter.count_messages(optimized, model)
        optimized_ids = {id(message) for message in optimized}
        protects_essentials = all(
            id(messages[index]) in optimized_ids
            for index in protected
        )
        if optimized_tokens <= input_budget and protects_essentials:
            return optimized

        fallback = self._fallback_fit(
            messages, protected, input_budget, model
        )
        fallback_tokens = self._counter.count_messages(fallback, model)
        if fallback_tokens <= input_budget:
            return fallback

        compressed = self._compress_middle(
            messages, protected, input_budget, model
        )
        logger.warning(
            "[ctx_window] Over budget (%s/%s tokens) — dropped middle history "
            "instead of sending an oversized prompt",
            fallback_tokens,
            input_budget,
        )
        return compressed

    def get_utilization(
        self,
        messages: list[dict[str, Any]],
        context_window: int,
        model: str = "",
    ) -> dict[str, Any]:
        used = self._counter.count_messages(messages, model)
        percentage = (
            used / context_window * 100
            if context_window > 0
            else 0.0
        )
        return {
            "used_tokens": used,
            "total_window": context_window,
            "available_tokens": max(0, context_window - used),
            "utilization_pct": round(percentage, 1),
            "message_count": len(messages),
        }
