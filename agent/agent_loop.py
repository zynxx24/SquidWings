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

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.constants import (
    MAX_THINKING_ONLY_ITERATIONS,
    MAX_TOOL_CALL_ITERATIONS,
)
from model.connection.api_client import APIClient
from model.connection.response_handler import ParsedResponse, ResponseHandler
from model.parser.tool_call_parser import ToolCallParser
from model.stream.stream_handler import StreamAggregator
from tool.registry.tool_registry import READ_ONLY_TOOLS, WORKSPACE_TOOLS, ToolRegistry
from utils.exceptions import ReasoningBudgetError, ToolError
from utils.logger import get_logger

from .harness import Harness
from .harness.advisor import ask_advisor
from .harness.guards import (
    LoopGuard,
    clip_text,
    compact_messages,
    normalize_arguments,
    repair_json_arguments,
    tool_failed,
)
from .harness.verifier import verify_turn

logger = get_logger(__name__)

REASONING_RETRIES = 2
MAX_REASONING_TOKENS = 65_536
MAX_LOCKOUT_WAIT_SECONDS = 30.0


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
        notes: Honest caveats to append to the answer (e.g. unverified work).
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
    notes: str = ""

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
        harness: Harness | None = None,
    ) -> None:
        """Initialize the agent loop.

        Args:
            api_client: API client for model calls.
            tool_registry: Registry of available tools.
            max_iterations: Maximum tool-call iterations (safety guard).
            harness: Supervision for this run. Without one the loop offers no
                tools, which is what truncation continuations want.
        """
        self._api_client = api_client
        self._tool_registry = tool_registry or ToolRegistry()
        self._max_iterations = max_iterations
        self._harness = harness

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

        harness = self._harness
        max_iterations = harness.scaffold.max_iterations if harness else self._max_iterations
        iteration_limit = max_iterations + MAX_THINKING_ONLY_ITERATIONS
        guard = LoopGuard(harness.scaffold.stuck_after) if harness else None
        verify_rounds_left = harness.scaffold.verify_rounds if harness else 0
        advisor_calls_left = harness.scaffold.advisor_calls if harness else 0
        force_final = False

        while iteration < iteration_limit:
            iteration += 1
            result.iterations = iteration

            # Out of budget: without this the run ends on a tool call and the
            # user gets nothing, however much useful work was already done.
            offered_tools = harness.tools if harness and not force_final else None
            if harness and offered_tools and iteration >= iteration_limit:
                offered_tools = None
                current_messages.append({
                    "role": "user",
                    "content": (
                        "Batas langkah tercapai. Jangan panggil tool lagi. Tulis jawaban akhir: "
                        "apa yang sudah selesai, apa yang belum, dan langkah berikutnya."
                    ),
                })
            if harness:
                dropped = compact_messages(current_messages, harness.scaffold.context_chars)
                harness.stats.compactions += dropped

            logger.debug(
                f"[agent_loop] Iteration {iteration}/{iteration_limit}, "
                f"messages={len(current_messages)}"
            )

            # Call the model
            try:
                parsed = await self._call_with_budget(
                    model_id=model_id,
                    messages=current_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=stream,
                    on_content_chunk=on_content_chunk,
                    on_thinking_chunk=on_thinking_chunk,
                    result=result,
                    tools=offered_tools,
                )
            except Exception as exc:
                # Before any tool ran, the fallback chain can simply start over.
                # After that it would repeat writes and commands, so finish here.
                if not harness or not result.tool_calls_history:
                    raise
                target = self._escalate(harness, model_id, result)
                if target != model_id:
                    model_id = target
                    continue
                logger.warning(f"[agent_loop] Model failed after tool work: {exc}")
                problem = f"Model gagal sebelum menulis ringkasan: {str(exc)[:200]}"
                harness.stats.unresolved = [problem]
                result.notes = f"\n\n> **Belum selesai:** {problem}"
                result.finish_reason = "error"
                return result

            # Accumulate token counts
            result.total_prompt_tokens += parsed.prompt_tokens
            result.total_completion_tokens += parsed.completion_tokens
            result.finish_reason = parsed.finish_reason
            result.parsed_response = parsed

            # Many cheap models write tool calls as text instead of using the
            # API field. Treat them as real calls and take the text back.
            if (
                harness
                and (offered_tools or (harness.text_tools and not force_final))
                and not parsed.has_tool_calls
                and parsed.content
            ):
                extracted = ToolCallParser.extract_text_tool_calls(parsed.content)
                if extracted:
                    parsed.tool_calls = [
                        {"id": c.get("id", ""), "name": c.get("name", ""), "arguments": c.get("arguments", {})}
                        for c in extracted
                    ]
                    parsed.content = ToolCallParser.clean_text_tool_calls(parsed.content)
                    if harness.tools:
                        harness.stats.repairs += 1
                    if harness.on_reset:
                        harness.on_reset()

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

                # Execute the tool calls. Lookups in the same turn run together;
                # anything that changes state keeps the order the model chose.
                calls = parsed.tool_calls
                if harness and len(calls) > 1 and all(
                    self._tool_registry.resolve_name(c.get("name", "")) in READ_ONLY_TOOLS for c in calls
                ):
                    outcomes = list(await asyncio.gather(*(self._run_tool(c, harness) for c in calls)))
                else:
                    outcomes = [await self._run_tool(c, harness) for c in calls]

                for tool_call, tool_result in zip(calls, outcomes, strict=True):
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

                if harness and guard:
                    # Separates text written before the tools from what follows.
                    if parsed.content and on_content_chunk:
                        on_content_chunk("\n\n")
                    nudges = [
                        nudge
                        for entry in result.tool_calls_history[-len(parsed.tool_calls):]
                        if (nudge := guard.observe(
                            entry["tool_call"].get("name", ""),
                            entry["tool_call"].get("arguments", {}),
                            tool_failed(entry["result"]),
                        ))
                    ]
                    if nudges:
                        harness.stats.nudges += 1
                        message = nudges[-1]
                        if guard.needs_help and advisor_calls_left > 0:
                            advisor_calls_left -= 1
                            advice = await self._consult(harness, result, message)
                            if advice:
                                message = f"{message}\n\nSaran dari reviewer senior:\n{advice}"
                        elif guard.needs_help:
                            model_id = self._escalate(harness, model_id, result)
                        guard.needs_help = False
                        current_messages.append({"role": "user", "content": message})
                    if guard.give_up:
                        force_final = True
                        current_messages.append({
                            "role": "user",
                            "content": (
                                "Hentikan tool call. Tulis jawaban akhir dari hasil yang sudah ada, "
                                "dan jujur tentang apa yang belum berhasil."
                            ),
                        })

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

            # Gate the claimed result before the user relies on it.
            if harness:
                harness.emit(type="status", text="Verifying result")
                issues, passed = await verify_turn(
                    parsed.content or "",
                    result.tool_calls_history,
                    harness.workspace,
                    bool(harness.tools) or harness.text_tools,
                )
                harness.stats.verified = passed
                harness.stats.unresolved = issues
                if issues and verify_rounds_left > 0 and iteration < iteration_limit:
                    verify_rounds_left -= 1
                    harness.stats.verify_rounds += 1
                    logger.info(f"[agent_loop] Verification failed: {issues}")
                    problem = "\n".join(f"- {issue}" for issue in issues)
                    feedback = (
                        "Pemeriksaan otomatis menemukan masalah pada hasilmu:\n"
                        f"{problem}\n\nPerbaiki, periksa lagi, lalu tulis ulang jawaban akhir."
                    )
                    if verify_rounds_left == 0:
                        if advisor_calls_left > 0:
                            advisor_calls_left -= 1
                            advice = await self._consult(harness, result, problem)
                            if advice:
                                feedback += f"\n\nSaran dari reviewer senior:\n{advice}"
                        else:
                            model_id = self._escalate(harness, model_id, result)
                    current_messages.append({"role": "assistant", "content": parsed.content})
                    current_messages.append({"role": "user", "content": feedback})
                    result.content_parts.clear()
                    if harness.on_reset:
                        harness.on_reset()
                    continue
                if issues:
                    bullets = "\n".join(f"- {issue}" for issue in issues)
                    result.notes = f"\n\n> **Belum terverifikasi:**\n{bullets}"

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
        tools: list[dict[str, Any]] | None = None,
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
            tools: Tool schemas to offer, if any.

        Returns:
            ParsedResponse from the model.
        """
        # Only sent when present: a null or empty list makes some providers
        # disable tool use or reject the request.
        tool_kwargs: dict[str, Any] = {"tools": tools} if tools else {}
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
                **tool_kwargs,
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
                **tool_kwargs,
            )
            return ResponseHandler.handle(raw_response)

    async def _call_with_budget(self, max_tokens: int, **call: Any) -> ParsedResponse:
        """Call the model, waiting out a lockout and raising the reasoning budget."""
        harness = self._harness
        ceiling = (harness.max_output if harness else 0) or MAX_REASONING_TOKENS
        for attempt in range(REASONING_RETRIES + 1):
            try:
                return await self._call_model(max_tokens=max_tokens, **call)
            except ReasoningBudgetError as exc:
                if attempt == REASONING_RETRIES:
                    raise
                wait = min(exc.retry_after or 0.0, MAX_LOCKOUT_WAIT_SECONDS)
                max_tokens = min(max_tokens * 2, ceiling)
                logger.warning(
                    f"[agent_loop] No answer from {call.get('model_id')}; retrying in {wait:.0f}s "
                    f"with max_tokens={max_tokens}"
                )
                if harness:
                    harness.emit(type="status", text=f"Model is busy, retrying in {wait:.0f}s")
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    async def _run_tool(self, tool_call: dict[str, Any], harness: Harness | None) -> dict[str, Any]:
        """Execute one call, reporting progress when supervised."""
        if harness:
            harness.emit(
                type="tool", phase="start",
                name=tool_call.get("name", ""),
                detail=self._call_detail(tool_call),
            )
        tool_result = await self._execute_tool_call(tool_call)
        if harness:
            harness.emit(
                type="tool", phase="end",
                name=tool_call.get("name", ""),
                ok=tool_failed(tool_result) is None,
                ms=round(tool_result.get("latency_ms", 0)),
                detail=self._call_detail(tool_call),
                error=tool_failed(tool_result) or "",
            )
        return tool_result

    async def _consult(self, harness: Harness, result: LoopResult, problem: str) -> str:
        """Ask the advisor model, if there is one, and count the call."""
        if not harness.advisor_model:
            return ""
        harness.emit(type="status", text="Consulting a stronger model")
        harness.stats.advisor_calls += 1
        return await ask_advisor(
            self._api_client,
            harness.advisor_model,
            harness.user_input,
            result.tool_calls_history,
            problem,
        )

    def _escalate(self, harness: Harness, model_id: str, result: LoopResult) -> str:
        """Hand the rest of the run to the stronger model once advice has not helped."""
        target = harness.advisor_model
        if not target or target == model_id:
            return model_id
        logger.info(f"[agent_loop] Escalating {model_id} -> {target}")
        harness.emit(type="status", text=f"Escalating to {target}")
        harness.stats.escalated_to = target
        result.model = target
        return target

    @staticmethod
    def _call_detail(tool_call: dict[str, Any]) -> str:
        """Short, safe description of a call for the progress display."""
        arguments = tool_call.get("arguments")
        if isinstance(arguments, dict):
            for key in ("file_path", "path", "dir_path", "query", "command"):
                value = arguments.get(key)
                if isinstance(value, str) and value:
                    return " ".join(value.split())[:120]
        return ""

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
        harness = self._harness
        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            arguments = repair_json_arguments(arguments_str) if harness else None
            if arguments is None:
                logger.warning(f"[agent_loop] Invalid tool arguments for {tool_name}: {e}")
                return {
                    "success": False,
                    "error": f"Invalid arguments JSON: {e}. Send a single valid JSON object.",
                    "tool_name": tool_name,
                }
            if harness:
                harness.stats.repairs += 1

        if harness:
            resolved_name = self._tool_registry.resolve_name(tool_name)
            if resolved_name in harness.disabled_tools:
                return {
                    "success": False,
                    "error": f"Tool '{tool_name}' is disabled in this environment.",
                    "tool_name": tool_name,
                }
            repaired = repair_json_arguments(arguments)
            if repaired is None:
                return {
                    "success": False,
                    "error": "Arguments must be a single JSON object.",
                    "tool_name": tool_name,
                }
            arguments = repaired
            schema = harness.parameters.get(resolved_name)
            if schema:
                arguments, problem, was_repaired = normalize_arguments(arguments, schema)
                harness.stats.repairs += int(was_repaired)
                if problem:
                    return {"success": False, "error": problem, "tool_name": tool_name}
            # Record what actually ran: the verifier and playbooks read this, and
            # they need the repaired argument names, before paths become absolute.
            tool_call["arguments"] = dict(arguments)
            self._apply_workspace(resolved_name, arguments, harness.workspace)

        # Ensure workspace-aware tools receive an explicit workspace
        if (
            tool_name in WORKSPACE_TOOLS
            and isinstance(arguments, dict)
            and not arguments.get(WORKSPACE_TOOLS[tool_name])
        ):
            import os as _os

            from utils.workspace_context import get_active_workspace as _gaw
            _ws = _gaw() or _os.getenv("SQUIDWINGS_WORKSPACE", "").strip()
            if _ws:
                arguments[WORKSPACE_TOOLS[tool_name]] = _ws

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
    def _apply_workspace(tool_name: str, arguments: dict[str, Any], workspace: str) -> None:
        """Point tools that default to the server's cwd at the session workspace.

        file_write resolves against the workspace but these do not, so without
        this a model cannot read back or run the file it just wrote, and its
        commands run inside the agent's own source tree.
        """
        if not workspace:
            return
        if tool_name == "execute_command":
            if arguments.get("cwd") in (None, "", "."):
                arguments["cwd"] = workspace
            return
        key = {"read_file": "file_path", "list_directory": "dir_path"}.get(tool_name)
        if key is None:
            return
        value = arguments.get(key)
        if not isinstance(value, str) or Path(value).is_absolute():
            return
        candidate = Path(workspace) / value
        # A relative path that only exists outside the workspace is left alone.
        if candidate.exists() or not Path(value).exists():
            arguments[key] = str(candidate)

    def _serialize_tool_result(self, tool_result: dict[str, Any]) -> str:
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
            text = json.dumps(content, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(content)
        if self._harness:
            return clip_text(text, self._harness.scaffold.tool_result_chars)
        return text
