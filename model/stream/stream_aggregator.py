"""
model.stream.stream_aggregator — Aggregate streamed chunks into full response.

Re-exports StreamAggregator from stream_handler for backward compatibility
and provides additional aggregation utilities.

NOTE: The primary StreamAggregator implementation lives in stream_handler.py.
This module provides a convenience re-export and adds the StatefulAggregator
class for use cases that need incremental aggregation without async iterators.
"""

from __future__ import annotations

from typing import Any

from model.connection.response_handler import ParsedResponse
from model.stream.stream_handler import StreamAggregator, StreamState
from utils.logger import get_logger

logger = get_logger(__name__)

# Re-export for convenience (plan references this module)
__all__ = ["StreamAggregator", "StreamState", "StatefulAggregator"]


class StatefulAggregator:
    """Synchronous stateful stream aggregator.

    Unlike StreamAggregator (async iterator based), this class
    allows feeding chunks one at a time and building the result
    incrementally. Useful for non-async contexts or when chunks
    arrive from a custom source.
    """

    def __init__(self) -> None:
        self._state = StreamState()
        self._chunk_count: int = 0

    @property
    def state(self) -> StreamState:
        """Current aggregation state."""
        return self._state

    @property
    def chunk_count(self) -> int:
        """Number of chunks processed so far."""
        return self._chunk_count

    def feed(self, chunk: dict[str, Any]) -> None:
        """Feed a single chunk into the aggregator.

        Args:
            chunk: A parsed SSE chunk dict.
        """
        self._chunk_count += 1

        if not chunk or not isinstance(chunk, dict):
            return

        choices = chunk.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                self._state.usage = usage
            return

        choice = choices[0]
        if not isinstance(choice, dict):
            return

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}

        self._state.model = chunk.get("model") or self._state.model

        # Finish reason
        if choice.get("finish_reason"):
            self._state.finish_reason = choice["finish_reason"]

        # Content
        content_delta = delta.get("content") or ""
        if content_delta:
            self._state.content_parts.append(content_delta)

        # Thinking
        thinking_delta = (
            delta.get("thinking")
            or delta.get("reasoning_content")
            or ""
        )
        if thinking_delta:
            self._state.thinking_parts.append(thinking_delta)

        # Tool calls
        tool_call_deltas = delta.get("tool_calls")
        if isinstance(tool_call_deltas, list):
            for tc_delta in tool_call_deltas:
                if not isinstance(tc_delta, dict):
                    continue
                idx = tc_delta.get("index", 0)
                if idx not in self._state.tool_calls_buffer:
                    self._state.tool_calls_buffer[idx] = {
                        "id": tc_delta.get("id") or "",
                        "name": "",
                        "arguments": "",
                    }

                buf = self._state.tool_calls_buffer[idx]
                if tc_delta.get("id"):
                    buf["id"] = tc_delta["id"]

                func = tc_delta.get("function")
                if isinstance(func, dict):
                    if func.get("name"):
                        buf["name"] = func["name"]
                    if func.get("arguments"):
                        buf["arguments"] += func["arguments"]

        # Usage
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self._state.usage = usage

    def build(self) -> ParsedResponse:
        """Build the final ParsedResponse from accumulated state.

        Returns:
            ParsedResponse with all content aggregated.
        """
        # Finalize tool calls (parse argument strings)
        tool_calls = StreamAggregator.finalize_tool_calls(
            self._state.tool_calls
        )

        response = ParsedResponse(
            content=self._state.content.strip(),
            thinking=self._state.thinking.strip(),
            tool_calls=tool_calls,
            finish_reason=self._state.finish_reason,
            model=self._state.model,
            usage=self._state.usage,
            raw={"_streamed": True, "_chunks": self._chunk_count},
        )

        logger.debug(
            f"[aggregator] Built response from {self._chunk_count} chunks: "
            f"content={len(response.content)} chars, "
            f"thinking={len(response.thinking)} chars, "
            f"tool_calls={len(response.tool_calls)}"
        )

        return response

    def reset(self) -> None:
        """Reset the aggregator for reuse."""
        self._state = StreamState()
        self._chunk_count = 0
