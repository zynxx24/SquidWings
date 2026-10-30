"""
agent.pipeline_executor — Structured PLAN → SEARCH → COMPILE → EXECUTE pipeline.

Alur kerja agent yang ringkas dan deterministik:

  PLAN    : Agent buat daftar langkah dari goal.
  SEARCH  : Jalankan web_search untuk setiap query yang diidentifikasi.
  COMPILE : Gabungkan plan + web results → satu insight ringkas.
  EXECUTE : Jalankan semua tool call yang diperlukan secara berurutan.

Setiap stage di-checkpoint ke AgentWorkingMemory sehingga:
  - Fallback ke model lain bisa lanjut dari stage yang sama.
  - Prompt caching efektif (blok [WORKING MEMORY] stabil per stage).
  - Agent tidak mengulang web search / tool call yang sudah selesai.

Usage (dari Orchestrator):
    executor = PipelineExecutor(
        api_client=client,
        tool_registry=tool_registry,
        working_memory=agent_working_memory,
        workspace_path=workspace_path,
    )
    result = await executor.run(
        user_input=user_input,
        session_id=session_id,
        turn_id=turn_id,
        model_id=model_id,
        base_messages=assembled_messages,
        max_tokens=max_tokens,
        temperature=temperature,
        on_content_chunk=on_content_chunk,
    )
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from brain.memory.agent_working_memory import (
    AgentStage,
    AgentWorkingMemory,
    WebResult,
    WorkingMemoryState,
)
from config.constants import MAX_TOOL_CALL_ITERATIONS
from model.connection.api_client import APIClient
from model.connection.response_handler import ResponseHandler
from model.stream.stream_handler import StreamAggregator
from tool.registry.tool_registry import ToolRegistry
from utils.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_PLAN_MAX_STEPS      = 8
_SEARCH_MAX_QUERIES  = 4
_TOOL_MAX_CALLS      = MAX_TOOL_CALL_ITERATIONS
_COMPILE_MAX_CHARS   = 3_000

# Single-call token limits per stage (lightweight calls)
_PLAN_MAX_TOKENS     = 1_024
_COMPILE_MAX_TOKENS  = 2_048


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """Output dari satu full pipeline run."""
    content: str = ""
    thinking: str = ""
    stage_reached: str = "idle"
    plan_steps: list[str] = field(default_factory=list)
    web_results: list[dict[str, str]] = field(default_factory=list)
    tools_executed: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_history: list[dict[str, Any]] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    iterations: int = 0
    resumed_from_stage: str = "idle"   # stage sebelumnya jika resumed


# ─────────────────────────────────────────────────────────────────────────────
# Stage prompts (ringkas, cache-friendly)
# ─────────────────────────────────────────────────────────────────────────────

def _plan_prompt(goal: str) -> str:
    return (
        "You are a planning engine. Given the user goal, output a JSON object:\n"
        '{"steps": ["step1", "step2", ...], "search_queries": ["query1", ...]}\n'
        f"- steps: max {_PLAN_MAX_STEPS} concrete action steps (Indonesian or English).\n"
        f"- search_queries: max {_SEARCH_MAX_QUERIES} web search queries if external info needed, else [].\n"
        "Output ONLY the JSON. No prose.\n\n"
        f"Goal: {goal}"
    )


def _compile_prompt(
    goal: str,
    plan_steps: list[str],
    web_results: list[WebResult],
) -> str:
    plan_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan_steps))
    web_text = ""
    for r in web_results[:_SEARCH_MAX_QUERIES]:
        web_text += f"\n[{r.query}] {r.title}\n{r.snippet[:400]}\nsource: {r.url}\n"

    return (
        "You are a synthesis engine. Combine the plan and web research into a "
        "concise execution brief (max 600 words). Focus on actionable facts.\n\n"
        f"Goal: {goal}\n\nPlan:\n{plan_text}\n\nWeb Research:{web_text or ' (none)'}\n\n"
        "Output the compiled brief only."
    )


def _execute_system_inject(state: WorkingMemoryState) -> str:
    """Ringkasan working memory yang disuntikkan ke system prompt saat EXECUTE."""
    return state.to_cache_block()


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Executor
# ─────────────────────────────────────────────────────────────────────────────

class PipelineExecutor:
    """
    Menjalankan pipeline PLAN → SEARCH → COMPILE → EXECUTE.

    - Setiap stage di-checkpoint; fallback model bisa resume.
    - Web search dilakukan via ToolRegistry (tool 'web_search').
    - Caching-friendly: blok WORKING MEMORY stabil per-stage.
    """

    def __init__(
        self,
        api_client: APIClient,
        tool_registry: ToolRegistry,
        working_memory: AgentWorkingMemory,
        workspace_path: str = ".",
        stream_aggregator: StreamAggregator | None = None,
    ) -> None:
        self._api    = api_client
        self._tools  = tool_registry
        self._wm     = working_memory
        self._ws     = workspace_path
        self._stream = stream_aggregator or StreamAggregator()
        self._usage: list[int] = [0, 0]

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    async def run(
        self,
        user_input: str,
        session_id: str,
        turn_id: str,
        model_id: str,
        base_messages: list[dict[str, Any]],
        max_tokens: int = 8_192,
        temperature: float = 0.7,
        on_content_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        stream: bool = False,
        harness: Any = None,
    ) -> PipelineResult:
        """
        Jalankan full pipeline untuk satu turn.
        Jika state sudah ada di working memory (resume), lanjut dari stage terakhir.
        """
        state  = self._wm.get_or_create(session_id, turn_id, goal=user_input)
        result = PipelineResult(resumed_from_stage=state.stage.value)
        self._usage = [0, 0]

        logger.info(
            "[pipeline] Start session=%s turn=%s stage=%s model=%s",
            session_id, turn_id, state.stage.value, model_id,
        )

        try:
            # ── PLAN ──────────────────────────────────────────────────────
            if state.stage in (AgentStage.IDLE, AgentStage.PLAN):
                await self._run_plan(state, model_id)

            result.plan_steps = list(state.plan_steps)

            # ── SEARCH ────────────────────────────────────────────────────
            if state.stage == AgentStage.SEARCH:
                await self._run_search(state)

            result.web_results = [r.to_dict() for r in state.web_results]

            # ── COMPILE ───────────────────────────────────────────────────
            if state.stage == AgentStage.COMPILE:
                await self._run_compile(state, model_id)

            # ── EXECUTE ───────────────────────────────────────────────────
            if state.stage == AgentStage.EXECUTE:
                exec_result = await self._run_execute(
                    state=state,
                    model_id=model_id,
                    base_messages=base_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=stream,
                    on_content_chunk=on_content_chunk,
                    on_thinking_chunk=on_thinking_chunk,
                    harness=harness,
                )
                result.content                  = exec_result.get("content", "")
                result.thinking                 = exec_result.get("thinking", "")
                result.total_prompt_tokens     += exec_result.get("prompt_tokens", 0)
                result.total_completion_tokens += exec_result.get("completion_tokens", 0)
                result.iterations              += exec_result.get("iterations", 0)
                result.tools_executed           = [t.to_dict() for t in state.tools_done]
                result.tool_calls_history       = exec_result.get("tool_calls_history", [])

            # PLAN and COMPILE cost tokens too; leaving them out understated usage.
            result.total_prompt_tokens     += self._usage[0]
            result.total_completion_tokens += self._usage[1]
            result.stage_reached = state.stage.value
            self._wm.finish(state)

        except Exception as exc:
            logger.error("[pipeline] Error at stage=%s: %s", state.stage.value, exc)
            self._wm.save(state)   # checkpoint sebelum raise
            raise

        return result

    # ------------------------------------------------------------------ #
    #  Stage: PLAN                                                         #
    # ------------------------------------------------------------------ #

    async def _run_plan(self, state: WorkingMemoryState, model_id: str) -> None:
        """Panggil model sekali untuk hasilkan rencana + query pencarian."""
        state.stage      = AgentStage.PLAN
        state.model_used = model_id
        self._wm.save(state)

        logger.info("[pipeline] PLAN — goal: %s", state.goal[:80])

        messages = [{"role": "user", "content": _plan_prompt(state.goal)}]
        raw      = await self._lightweight_call(model_id, messages, _PLAN_MAX_TOKENS)

        # Parse JSON; toleran terhadap markdown fences
        parsed  = _extract_json(raw)
        steps   = parsed.get("steps", [])   if parsed else []
        queries = parsed.get("search_queries", []) if parsed else []

        if not steps:
            # Fallback: satu step generik
            steps = [f"Selesaikan: {state.goal}"]

        state.plan_steps     = [str(s)[:200] for s in steps[:_PLAN_MAX_STEPS]]
        state.search_queries = [str(q)[:200] for q in queries[:_SEARCH_MAX_QUERIES]]

        logger.info(
            "[pipeline] PLAN done — %d steps, %d queries",
            len(state.plan_steps), len(state.search_queries),
        )

        # Lanjut ke SEARCH jika ada query, langsung COMPILE jika tidak
        state.stage = AgentStage.SEARCH if state.search_queries else AgentStage.COMPILE
        self._wm.save(state)

    # ------------------------------------------------------------------ #
    #  Stage: SEARCH                                                       #
    # ------------------------------------------------------------------ #

    async def _run_search(self, state: WorkingMemoryState) -> None:
        """Jalankan web_search untuk setiap query yang belum dikerjakan."""
        logger.info("[pipeline] SEARCH — %d queries", len(state.search_queries))

        if not self._tools.has_tool("web_search"):
            logger.warning("[pipeline] web_search tool not registered, skipping SEARCH")
            state.stage = AgentStage.COMPILE
            self._wm.save(state)
            return

        done_queries = {r.query for r in state.web_results}

        for query in state.search_queries:
            if query in done_queries:
                logger.debug("[pipeline] SEARCH skip (cached): %s", query)
                continue

            try:
                # Through the registry, so results get the guard's policy and redaction.
                raw_result = await self._tools.execute("web_search", {"query": query, "max_results": 4})

                if isinstance(raw_result, dict) and raw_result.get("ok") is False:
                    logger.warning(
                        "[pipeline] SEARCH '%s' returned not-ok: %s",
                        query, raw_result.get("error", "unknown error"),
                    )
                    continue

                results = _parse_web_results(query, raw_result)
                state.web_results.extend(results)
                self._wm.save(state)   # checkpoint setelah setiap query
                logger.info("[pipeline] SEARCH '%s' → %d results", query, len(results))
            except Exception as exc:
                logger.warning("[pipeline] SEARCH '%s' failed: %s", query, exc)

        state.stage = AgentStage.COMPILE
        self._wm.save(state)

    # ------------------------------------------------------------------ #
    #  Stage: COMPILE                                                      #
    # ------------------------------------------------------------------ #

    async def _run_compile(self, state: WorkingMemoryState, model_id: str) -> None:
        """Gabungkan plan + web → compiled brief."""
        logger.info("[pipeline] COMPILE")

        if state.web_results or len(state.plan_steps) > 1:
            prompt   = _compile_prompt(state.goal, state.plan_steps, state.web_results)
            messages = [{"role": "user", "content": prompt}]
            brief    = await self._lightweight_call(model_id, messages, _COMPILE_MAX_TOKENS)
            state.compiled = brief[:_COMPILE_MAX_CHARS]
        else:
            # Tidak ada web data + plan trivial → skip compile
            state.compiled = ""

        logger.info("[pipeline] COMPILE done (%d chars)", len(state.compiled))
        state.stage = AgentStage.EXECUTE
        self._wm.save(state)

    # ------------------------------------------------------------------ #
    #  Stage: EXECUTE                                                      #
    # ------------------------------------------------------------------ #

    async def _run_execute(
        self,
        state: WorkingMemoryState,
        model_id: str,
        base_messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        stream: bool,
        on_content_chunk: Callable[[str], None] | None,
        on_thinking_chunk: Callable[[str], None] | None,
        harness: Any = None,
    ) -> dict[str, Any]:
        """
        Jalankan agent loop utama dengan working memory disuntikkan ke messages.
        Tool call yang sudah ada di state.tools_done TIDAK diulang.
        """
        logger.info("[pipeline] EXECUTE — model=%s", model_id)

        # Suntikkan working memory ke akhir system prompt
        wm_block = _execute_system_inject(state)
        messages = _inject_working_memory(base_messages, wm_block)

        # Import di sini untuk hindari circular import
        from agent.agent_loop import AgentLoop

        loop = AgentLoop(
            api_client=self._api,
            tool_registry=self._tools,
            max_iterations=_TOOL_MAX_CALLS,
            harness=harness,
        )

        loop_result = await loop.run(
            model_id=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            on_content_chunk=on_content_chunk,
            on_thinking_chunk=on_thinking_chunk,
        )

        # Checkpoint tool calls ke working memory
        for entry in loop_result.tool_calls_history:
            tc  = entry["tool_call"]
            res = entry["result"]
            state.add_tool(
                name=tc.get("name", ""),
                arguments=tc.get("arguments", {}),
                result=str(res)[:600],
                iteration=entry.get("iteration", 0),
                success=not str(res).startswith("Error"),
            )
        if loop_result.thinking:
            state.thinking = loop_result.thinking[-2_000:]

        self._wm.save(state)

        return {
            "content":           loop_result.content,
            "thinking":          loop_result.thinking,
            "prompt_tokens":     loop_result.total_prompt_tokens,
            "completion_tokens": loop_result.total_completion_tokens,
            "iterations":        loop_result.iterations,
            "tool_calls_history": loop_result.tool_calls_history,
        }

    # ------------------------------------------------------------------ #
    #  Helper: lightweight single call                                     #
    # ------------------------------------------------------------------ #

    async def _lightweight_call(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> str:
        """Single non-streaming call untuk PLAN/COMPILE. Ringan, tanpa tool."""
        resp    = await self._api.chat_completion(
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,   # deterministik untuk planning
            stream=False,
        )
        parsed = ResponseHandler.handle(resp)
        self._usage[0] += parsed.prompt_tokens
        self._usage[1] += parsed.completion_tokens
        return parsed.content or ""


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers (module-level)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict[str, Any] | None:
    """Ekstrak JSON dari teks, toleran terhadap markdown code fence."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    match   = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_web_results(query: str, raw: Any) -> list[WebResult]:
    """
    Konversi output tool web_search ke list[WebResult].
    Format output tool bisa dict atau string — handle keduanya.
    """
    results: list[WebResult] = []

    if isinstance(raw, str):
        results.append(WebResult(
            query=query, title="(web result)", url="", snippet=raw[:400],
        ))
        return results

    if isinstance(raw, dict):
        items = raw.get("results", raw.get("data", []))
        if isinstance(items, list):
            for item in items[:4]:
                if isinstance(item, dict):
                    results.append(WebResult(
                        query=query,
                        title=item.get("title", ""),
                        url=str(item.get("url") or item.get("link") or ""),
                        snippet=str(item.get("snippet") or item.get("body") or "")[:400],
                    ))
        elif raw.get("snippet"):
            results.append(WebResult(
                query=query,
                title=raw.get("title", ""),
                url=raw.get("url", ""),
                snippet=raw.get("snippet", "")[:400],
            ))

    return results


def _inject_working_memory(
    messages: list[dict[str, Any]],
    wm_block: str,
) -> list[dict[str, Any]]:
    """
    Suntikkan blok working memory ke system prompt (index 0).
    Jika tidak ada system prompt, tambahkan di awal.
    """
    if not wm_block or not wm_block.strip():
        return messages

    msgs = [dict(m) for m in messages]

    if msgs and msgs[0].get("role") == "system":
        existing = msgs[0].get("content", "")
        if isinstance(existing, str):
            msgs[0]["content"] = existing + "\n\n" + wm_block
        elif isinstance(existing, list):
            msgs[0]["content"] = existing + [{"type": "text", "text": "\n\n" + wm_block}]
    else:
        msgs.insert(0, {"role": "system", "content": wm_block})

    return msgs
