"""
agent.agent_loop — Core agent loop for multi-turn tool calling.

Handles the iterative cycle of:
1. Send messages → API
2. Parse response
3. If tool_calls → execute tools → append results → loop
4. If no tool_calls → return final response

Includes safety guards: max iteration limit and convergence detection.

Usage:
    loop = AgentLoop(api_client, tool_registry, max_iterations=10)
    result = await loop.run(model, messages, temperature, max_tokens)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from config.constants import (
    MAX_THINKING_ONLY_ITERATIONS,
    MAX_TOOL_CALL_ITERATIONS,
)
from model.connection.api_client import APIClient
from model.connection.response_handler import ParsedResponse, ResponseHandler
from model.stream.stream_handler import StreamAggregator
from tool.registry.tool_registry import ToolRegistry
from utils.exceptions import ToolError
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LoopResult:
    """Result from a complete agent loop execution.

    Attributes:
        content: The final text response.
        thinking: Accumulated thinking text.
        tool_calls_history: All tool calls that were executed.
        iterations: Number of loop iterations taken.
        total_prompt_tokens: Cumulative prompt tokens across all iterations.
        total_completion_tokens: Cumulative completion tokens.
        finish_reason: Final finish reason from the last API call.
        model: Model ID used.
        content_parts: Raw content parts (for streaming reconstruction).
        parsed_response: The final ParsedResponse from the last iteration.
    """
    content: str = ""
    thinking: str = ""
    tool_calls_history: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    finish_reason: str = ""
    model: str = ""
    content_parts: list[str] = field(default_factory=list)
    parsed_response: ParsedResponse | None = None

    @property
    def total_tokens(self) -> int:
        """Total tokens used across all iterations."""
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def had_tool_calls(self) -> bool:
        """Whether any tool calls were executed."""
        return len(self.tool_calls_history) > 0


class AgentLoop:
    """Core agent loop handling multi-turn tool calling.

    Executes the iterative process of calling the model, checking for
    tool calls, executing tools, appending results, and re-calling
    the model until a final response is produced or the max iteration
    limit is reached.

    Safety features:
    - Max iteration guard (prevents infinite loops)
    - Convergence detection (stops if no new tool calls after a tool round)
    - Per-tool error handling (tool failures don't crash the loop)
    - Empty content detection for thinking-only responses (DeepSeek pattern)
    """

    def __init__(
        self,
        api_client: APIClient,
        tool_registry: ToolRegistry | None = None,
        max_iterations: int = MAX_TOOL_CALL_ITERATIONS,
    ) -> None:
        """Initialize the agent loop.

        Args:
            api_client: API client for model calls.
            tool_registry: Registry of available tools.
            max_iterations: Maximum tool-call iterations (safety guard).
        """
        self._api_client = api_client
        self._tool_registry = tool_registry or ToolRegistry()
        self._max_iterations = max_iterations

    async def run(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int = 8192,
        stream: bool = True,
        on_content_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> LoopResult:
        """Run the full agent loop.

        Args:
            model_id: Model ID to call.
            messages: Initial message list.
            temperature: Sampling temperature.
            max_tokens: Max output tokens.
            stream: Whether to stream responses.
            on_content_chunk: Callback for streaming content chunks.
            on_thinking_chunk: Callback for streaming thinking chunks.

        Returns:
            LoopResult with the final response and execution metadata.
        """
        result = LoopResult(model=model_id)
        current_messages = list(messages)
        iteration = 0
        # A reasoning model often emits thinking before it has read anything.
        # Forcing a final answer on the first thinking-only turn makes it
        # invent file contents it never opened, so track the streak instead.
        thinking_only_streak = 0
        empty_streak = 0  # consecutive fully-empty responses (no content, no thinking, no tool calls)

        iteration_limit = self._max_iterations + MAX_THINKING_ONLY_ITERATIONS
        while iteration < iteration_limit:
            iteration += 1
            result.iterations = iteration

            logger.debug(
                f"[agent_loop] Iteration {iteration}/{iteration_limit}, "
                f"messages={len(current_messages)}"
            )

            # Call the model
            parsed = await self._call_model(
                model_id=model_id,
                messages=current_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                on_content_chunk=on_content_chunk if iteration == 1 else None,
                on_thinking_chunk=on_thinking_chunk if iteration == 1 else None,
                result=result,
            )

            # Accumulate token counts
            result.total_prompt_tokens += parsed.prompt_tokens
            result.total_completion_tokens += parsed.completion_tokens
            result.finish_reason = parsed.finish_reason
            result.parsed_response = parsed

            # Check for tool calls
            if parsed.has_tool_calls:
                logger.info(
                    f"[agent_loop] {len(parsed.tool_calls)} tool call(s) "
                    f"in iteration {iteration}"
                )

                # Append assistant message with tool calls
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": parsed.content or None,
                }
                if parsed.tool_calls:
                    # ParsedResponse uses a flat internal representation. The
                    # model API requires the nested OpenAI-compatible format.
                    assistant_msg["tool_calls"] = [
                        {
                            "id": call.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": call.get("name", ""),
                                "arguments": json.dumps(
                                    call.get("arguments", {}), ensure_ascii=False
                                ),
                            },
                        }
                        for call in parsed.tool_calls
                    ]
                current_messages.append(assistant_msg)

                # Execute each tool call
                for tool_call in parsed.tool_calls:
                    tool_result = await self._execute_tool_call(tool_call)
                    result.tool_calls_history.append({
                        "tool_call": tool_call,
                        "result": tool_result,
                        "iteration": iteration,
                    })

                    # Append tool result message
                    tool_call_id = tool_call.get("id", "")
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": self._serialize_tool_result(tool_result),
                    })

                # Tool work is progress: reset the thinking-only streak.
                thinking_only_streak = 0
                # Continue loop for model to process tool results
                continue

            # No tool calls — we have a final response
            # Accumulate content
            if parsed.content:
                if not result.content_parts:
                    result.content_parts.append(parsed.content)
                result.content = parsed.content

            if parsed.thinking:
                result.thinking = parsed.thinking

            # Handle reasoning-model pattern: thinking present but no content.
            # Do NOT force a final answer on the first occurrence — the model
            # has not read anything yet. Nudge it to keep working instead.
            if not parsed.content and parsed.thinking:
                thinking_only_streak += 1
                empty_streak = 0  # thinking is non-empty; reset the empty guard
                logger.info(
                    "[agent_loop] Thinking present but no content "
                    f"(streak={thinking_only_streak}) — asking model to continue"
                )
                current_messages.append({
                    "role": "assistant",
                    "content": f"<thinking>\n{parsed.thinking.strip()}\n</thinking>",
                })
                if thinking_only_streak >= MAX_THINKING_ONLY_ITERATIONS:
                    # Budget exhausted; record the finish reason and let the
                    # while-loop condition (iteration < iteration_limit) terminate
                    # the loop naturally so all allocated iterations are used.
                    logger.warning(
                        "[agent_loop] Thinking-only after "
                        f"{thinking_only_streak} nudge(s) — budget exhausted"
                    )
                    result.finish_reason = "thinking_only"
                if thinking_only_streak >= 2:
                    nudge = (
                        "Lanjutkan pekerjaan: panggil tool yang diperlukan, atau "
                        "tulis hasil bila sudah benar-benar selesai. Jangan "
                        "mengarang isi berkas yang belum kamu baca."
                    )
                else:
                    nudge = (
                        "Lanjutkan. Jika masih ada berkas yang belum kamu baca, "
                        "baca dulu. Jangan menyimpulkan sebelum membaca."
                    )
                current_messages.append({"role": "user", "content": nudge})
                continue

            # Guard: fully-empty response (no content, no thinking, no tool calls).
            # This can happen when the model returns a stop token with an empty
            # body.  Nudge once; on the second empty in a row raise so the
            # orchestrator can fall back to a different model.
            # NOTE: whitespace-only content (e.g. "\n\n") is treated as empty —
            # strip() before the check so those cases enter the nudge path here
            # rather than escaping to the orchestrator's EmptyResponseError
            # without retry.
            content_empty = not parsed.content or not parsed.content.strip()
            if content_empty and not parsed.thinking:
                empty_streak += 1
                logger.warning(
                    f"[agent_loop] Fully-empty response (streak={empty_streak})"
                )
                if empty_streak >= 2:
                    raise RuntimeError(
                        f"Model returned {empty_streak} consecutive empty responses"
                    )
                current_messages.append({
                    "role": "user",
                    "content": "Respons kosong. Lanjutkan dan berikan jawaban.",
                })
                continue

            empty_streak = 0  # reset on any non-empty turn

            # Final response obtained
            logger.info(
                f"[agent_loop] Completed in {iteration} iteration(s), "
                f"tokens={result.total_tokens}"
            )
            break

        else:
            # Max iterations reached
            logger.warning(
                f"[agent_loop] Max iterations ({iteration_limit}) reached "
                f"without convergence"
            )

        return result

    async def _call_model(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        stream: bool,
        on_content_chunk: Callable[[str], None] | None,
        on_thinking_chunk: Callable[[str], None] | None,
        result: LoopResult,
    ) -> ParsedResponse:
        """Call the model with given parameters.

        Args:
            model_id: Model to call.
            messages: Messages to send.
            temperature: Temperature setting.
            max_tokens: Max tokens.
            stream: Whether to stream.
            on_content_chunk: Content chunk callback.
            on_thinking_chunk: Thinking chunk callback.
            result: LoopResult to accumulate streaming parts.

        Returns:
            ParsedResponse from the model.
        """
        if stream:
            content_parts: list[str] = []

            def _on_content(chunk: str) -> None:
                content_parts.append(chunk)
                if on_content_chunk:
                    on_content_chunk(chunk)

            def _on_thinking(chunk: str) -> None:
                if on_thinking_chunk:
                    on_thinking_chunk(chunk)

            aggregator = StreamAggregator(
                on_content_chunk=_on_content,
                on_thinking_chunk=_on_thinking,
            )
            stream_iter = self._api_client.chat_completion_stream(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            parsed = await aggregator.aggregate(stream_iter)

            # Store streaming content parts
            if content_parts:
                result.content_parts.extend(content_parts)

            return parsed
        else:
            raw_response = await self._api_client.chat_completion(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return ResponseHandler.handle(raw_response)

    async def _execute_tool_call(
        self,
        tool_call: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single tool call.

        Args:
            tool_call: Tool call dict from the model response.

        Returns:
            Dict with 'success', 'result'/'error' keys.
        """
        # Accept the flat ParsedResponse format and raw OpenAI-style calls.
        func = tool_call.get("function")
        if not isinstance(func, dict):
            func = {}
        tool_name = func.get("name") or tool_call.get("name") or "unknown"
        arguments_str = func.get("arguments", tool_call.get("arguments", {}))

        # Parse arguments
        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            logger.warning(f"[agent_loop] Invalid tool arguments for {tool_name}: {e}")
            return {
                "success": False,
                "error": f"Invalid arguments JSON: {e}",
                "tool_name": tool_name,
            }

        # Ensure workspace-aware tools receive an explicit workspace
        _workspace_tools = {
            "web_search",
            "file_search",
            "file_write",
            "file_patch",
            "file_move",
            "file_delete",
            "dir_create",
            "file_stat",
            "count_lines",
            "git_status",
            "git_diff",
            "json_query",
            "graphify_ingest",
            "graphify_query",
            "memory_recall",
            "memory_remember",
            "memory_capture",
            "memory_rank_files",
            "vault_boot",
            "vault_graph_build",
            "vault_neighbours",
            "vault_explain",
            "vault_health",
            "vault_search",
            "vault_read",
            "vault_recall",
            "vault_write",
        }
        if (
            tool_name in _workspace_tools
            and isinstance(arguments, dict)
            and not arguments.get("workspace_path")
        ):
            import os as _os
            from utils.workspace_context import get_active_workspace as _gaw
            _ws = _gaw() or _os.getenv("SQUIDWINGS_WORKSPACE", "").strip()
            if _ws:
                arguments["workspace_path"] = _ws

        # Execute
        logger.info(f"[agent_loop] Executing tool: {tool_name}")
        start_time = time.time()

        try:
            tool_result = await self._tool_registry.execute(tool_name, arguments)
            elapsed = (time.time() - start_time) * 1000

            logger.info(
                f"[agent_loop] Tool {tool_name} completed in {elapsed:.0f}ms"
            )
            return {
                "success": True,
                "result": tool_result,
                "tool_name": tool_name,
                "latency_ms": elapsed,
            }
        except ToolError as e:
            elapsed = (time.time() - start_time) * 1000
            logger.warning(
                f"[agent_loop] Tool {tool_name} failed ({elapsed:.0f}ms): {e.message}"
            )
            return {
                "success": False,
                "error": e.message,
                "tool_name": tool_name,
                "latency_ms": elapsed,
            }
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(
                f"[agent_loop] Tool {tool_name} unexpected error ({elapsed:.0f}ms): {e}"
            )
            return {
                "success": False,
                "error": f"Unexpected error: {e}",
                "tool_name": tool_name,
                "latency_ms": elapsed,
            }

    @staticmethod
    def _serialize_tool_result(tool_result: dict[str, Any]) -> str:
        """Serialize a tool result for the API message.

        Args:
            tool_result: Tool execution result dict.

        Returns:
            JSON string of the result.
        """
        if tool_result.get("success"):
            content = tool_result.get("result")
        else:
            content = {"error": tool_result.get("error", "Unknown error")}

        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(content)
