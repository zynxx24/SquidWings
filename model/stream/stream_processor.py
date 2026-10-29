"""
model.stream.stream_processor — Process individual stream chunks.

Processes each SSE chunk as it arrives, extracting delta content,
delta tool calls, and delta thinking. Emits typed events for
each kind of content received.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@unique
class ChunkType(StrEnum):
    """Type of content found in a stream chunk."""
    CONTENT = "content"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    FINISH = "finish"
    EMPTY = "empty"


@dataclass
class ProcessedChunk:
    """Result of processing a single SSE chunk.

    Attributes:
        chunk_type: What kind of content was found.
        content_delta: Text content delta (if any).
        thinking_delta: Thinking content delta (if any).
        tool_call_delta: Tool call delta dict (if any).
        finish_reason: Finish reason (if stream ended).
        usage: Usage stats dict (if present).
        model: Model ID from the chunk.
    """
    chunk_type: ChunkType
    content_delta: str = ""
    thinking_delta: str = ""
    tool_call_delta: dict[str, Any] | None = None
    finish_reason: str = ""
    usage: dict[str, int] | None = None
    model: str = ""


class StreamProcessor:
    """Processes individual SSE stream chunks.

    Takes raw SSE chunk dicts and extracts structured deltas,
    optionally invoking callbacks for each type of content.

    This is a stateless processor — it processes one chunk at a time.
    For accumulation, use StreamAggregator (in stream_handler.py).
    """

    def __init__(
        self,
        on_content: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_tool_call: Callable[[dict[str, Any]], None] | None = None,
        on_finish: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the processor with optional callbacks.

        Args:
            on_content: Called for each content delta.
            on_thinking: Called for each thinking delta.
            on_tool_call: Called for each tool call delta.
            on_finish: Called when stream finishes with reason.
        """
        self.on_content = on_content
        self.on_thinking = on_thinking
        self.on_tool_call = on_tool_call
        self.on_finish = on_finish

    def process(self, chunk: dict[str, Any]) -> list[ProcessedChunk]:
        """Process a single SSE chunk dict.

        A single chunk can contain multiple types of content
        (e.g., both content and tool call deltas), so this returns
        a list of ProcessedChunk objects.

        Args:
            chunk: Parsed SSE data dict.

        Returns:
            List of ProcessedChunk objects for each content type found.
        """
        results: list[ProcessedChunk] = []
        if not chunk or not isinstance(chunk, dict):
            return results

        model = chunk.get("model") or ""

        choices = chunk.get("choices")
        # Usage-only chunk (no choices, typically at stream end)
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                results.append(ProcessedChunk(
                    chunk_type=ChunkType.USAGE,
                    usage=usage,
                    model=model,
                ))
            else:
                results.append(ProcessedChunk(
                    chunk_type=ChunkType.EMPTY,
                    model=model,
                ))
            return results

        choice = choices[0]
        if not isinstance(choice, dict):
            return results

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}

        # Content delta
        content_delta = delta.get("content") or ""
        if content_delta:
            results.append(ProcessedChunk(
                chunk_type=ChunkType.CONTENT,
                content_delta=content_delta,
                model=model,
            ))
            if self.on_content:
                self.on_content(content_delta)

        # Thinking delta (multiple possible field names)
        thinking_delta = (
            delta.get("thinking")
            or delta.get("reasoning_content")
            or ""
        )
        if thinking_delta:
            results.append(ProcessedChunk(
                chunk_type=ChunkType.THINKING,
                thinking_delta=thinking_delta,
                model=model,
            ))
            if self.on_thinking:
                self.on_thinking(thinking_delta)

        # Tool call deltas
        tool_call_deltas = delta.get("tool_calls")
        if isinstance(tool_call_deltas, list):
            for tc_delta in tool_call_deltas:
                if not isinstance(tc_delta, dict):
                    continue
                results.append(ProcessedChunk(
                    chunk_type=ChunkType.TOOL_CALL,
                    tool_call_delta=tc_delta,
                    model=model,
                ))
                if self.on_tool_call:
                    self.on_tool_call(tc_delta)

        # Finish reason
        finish_reason = choice.get("finish_reason") or ""
        if finish_reason:
            results.append(ProcessedChunk(
                chunk_type=ChunkType.FINISH,
                finish_reason=finish_reason,
                model=model,
            ))
            if self.on_finish:
                self.on_finish(finish_reason)

        # Usage in chunk
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            results.append(ProcessedChunk(
                chunk_type=ChunkType.USAGE,
                usage=usage,
                model=model,
            ))

        if not results:
            results.append(ProcessedChunk(
                chunk_type=ChunkType.EMPTY,
                model=model,
            ))

        return results
