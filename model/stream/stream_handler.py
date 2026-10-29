"""
model.stream.stream_handler — Handle and aggregate SSE streams.

Processes Server-Sent Events (SSE) from the streaming API
and aggregates chunks into a complete ParsedResponse.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from model.connection.response_handler import ParsedResponse
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class StreamState:
    """Tracks the state of an ongoing stream.

    Accumulates content, thinking, and tool calls as chunks arrive.
    """

    content_parts: list[str] = field(default_factory=list)
    thinking_parts: list[str] = field(default_factory=list)
    tool_calls_buffer: dict[int, dict[str, Any]] = field(default_factory=dict)
    finish_reason: str = ""
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def content(self) -> str:
        return "".join(self.content_parts)

    @property
    def thinking(self) -> str:
        return "".join(self.thinking_parts)

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return list(self.tool_calls_buffer.values())


class StreamAggregator:
    """Aggregates streaming chunks into a complete ParsedResponse.

    Processes each SSE chunk, accumulates content and thinking,
    and builds the final response when the stream ends.
    """

    def __init__(
        self,
        on_content_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the aggregator.

        Args:
            on_content_chunk: Optional callback for each content chunk (for live display).
            on_thinking_chunk: Optional callback for each thinking chunk.
        """
        self.on_content_chunk = on_content_chunk
        self.on_thinking_chunk = on_thinking_chunk

    async def aggregate(
        self,
        stream: AsyncIterator[dict[str, Any]],
    ) -> ParsedResponse:
        """Aggregate a stream of chunks into a ParsedResponse.

        Args:
            stream: Async iterator of SSE chunk dicts.

        Returns:
            ParsedResponse with all accumulated content.
        """
        state = StreamState()

        async for chunk in stream:
            self._process_chunk(chunk, state)

        return ParsedResponse(
            content=state.content.strip(),
            thinking=state.thinking.strip(),
            tool_calls=self.finalize_tool_calls(state.tool_calls),
            finish_reason=state.finish_reason,
            model=state.model,
            usage=state.usage,
            raw={"_streamed": True},
        )

    def _process_chunk(self, chunk: dict[str, Any], state: StreamState) -> None:
        """Process a single SSE chunk.

        Args:
            chunk: Parsed SSE data chunk.
            state: Current stream state to update.
        """
        if not chunk or not isinstance(chunk, dict):
            return

        choices = chunk.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            # Usage-only chunk (at end of stream)
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                state.usage = usage
            return

        choice = choices[0]
        if not isinstance(choice, dict):
            return

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}

        state.model = chunk.get("model") or state.model

        # Finish reason
        if choice.get("finish_reason"):
            state.finish_reason = choice["finish_reason"]

        # Content delta
        content_delta = delta.get("content") or ""
        if content_delta:
            state.content_parts.append(content_delta)
            if self.on_content_chunk:
                try:
                    self.on_content_chunk(content_delta)
                except Exception as cb_err:
                    logger.debug(f"[stream] on_content_chunk display error: {cb_err}")

        # Thinking delta
        thinking_delta = delta.get("thinking") or delta.get("reasoning_content") or ""
        if thinking_delta:
            state.thinking_parts.append(thinking_delta)
            if self.on_thinking_chunk:
                try:
                    self.on_thinking_chunk(thinking_delta)
                except Exception as cb_err:
                    logger.debug(f"[stream] on_thinking_chunk display error: {cb_err}")

        # Tool call deltas
        tool_call_deltas = delta.get("tool_calls")
        if isinstance(tool_call_deltas, list):
            for tc_delta in tool_call_deltas:
                if not isinstance(tc_delta, dict):
                    continue
                idx = tc_delta.get("index", 0)
                if idx not in state.tool_calls_buffer:
                    state.tool_calls_buffer[idx] = {
                        "id": tc_delta.get("id") or "",
                        "name": "",
                        "arguments": "",
                    }

                buf = state.tool_calls_buffer[idx]
                if tc_delta.get("id"):
                    buf["id"] = tc_delta["id"]

                func = tc_delta.get("function")
                if isinstance(func, dict):
                    if func.get("name"):
                        buf["name"] = func["name"]
                    if func.get("arguments"):
                        buf["arguments"] += func["arguments"]

        # Usage in chunk
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            state.usage = usage

    @staticmethod
    def finalize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Finalize tool calls by parsing argument strings.

        Args:
            tool_calls: Raw accumulated tool calls.

        Returns:
            Tool calls with parsed JSON arguments.
        """
        finalized = []
        for tc in tool_calls:
            args = tc.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            finalized.append({
                "id": tc["id"],
                "name": tc["name"],
                "arguments": args,
            })
        return finalized
