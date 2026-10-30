"""Token-aware assembly of prompts, relevant history, and current input.

Key change vs. previous version
---------------------------------
The old assembler calculated output capacity as "whatever is left after input
fills the window", leading to unpredictable output budgets.

This version uses **proportional input/output budgeting** via
:class:`~brain.generate_efficient_token.input_budget.InputBudget`:
- Task type determines the input/output split ratio.
- History is trimmed to stay inside the input budget (not the other way round).
- Output budget is guaranteed at a task-appropriate minimum.

Additionally, **prompt caching** is applied via
:mod:`~brain.generate_efficient_token.prompt_cache` so that stable content
(system prompt, old history) is marked for provider-side caching, reducing
effective input-token cost on repeated turns.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from brain.context.context_ranker import ContextRanker
from brain.generate_efficient_token.input_budget import InputBudget
from brain.generate_efficient_token.prompt_cache import apply_cache_control
from brain.generate_efficient_token.token_counter import TokenCounter
from brain.max_token.max_token_calculator import MaxTokenCalculator
from brain.memory.context_window_manager import ContextWindowManager
from brain.memory.conversation_store import ConversationStore
from brain.memory.message_builder import MessageBuilder
from brain.memory.summary_compressor import SummaryCompressor
from brain.prompt_must_think.base_think_prompt import ThinkPromptManager
from brain.system_prompt.base_system_prompt import SystemPromptManager
from config.constants import TOKEN_BUFFER, TaskType
from model.type.base_handler import BaseTypeHandler

_HISTORY_MAX_TOKENS = 24_000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class AssembledContext:
    messages: list[dict[str, Any]] = field(default_factory=list)
    task_type: TaskType = TaskType.ACTIVITY
    system_prompt: str = ""
    think_prompt: str = ""
    estimated_tokens: int = 0
    max_output_tokens: int = 8192
    truncated: bool = False
    # New: expose cache/budget metadata for logging / monitoring
    history_budget: int = 0
    output_budget: int = 0
    cached_tokens_estimate: int = 0


class ContextAssembler:
    """Build context without allowing old history to evict new input.

    Uses proportional input/output budgeting (InputBudget) and
    provider-side prompt caching (apply_cache_control) to maximise
    efficiency across all model context windows.
    """

    def __init__(
        self,
        conversation_store: ConversationStore | None = None,
        think_prompt_manager: ThinkPromptManager | None = None,
        system_prompt_manager: SystemPromptManager | None = None,
        token_counter: TokenCounter | None = None,
        context_ranker: ContextRanker | None = None,
        window_manager: ContextWindowManager | None = None,
        max_token_calculator: MaxTokenCalculator | None = None,
    ) -> None:
        self._store = conversation_store or ConversationStore()
        self._think_mgr = think_prompt_manager or ThinkPromptManager()
        self._system_mgr = system_prompt_manager or SystemPromptManager()
        self._counter = token_counter or TokenCounter()
        self._ranker = context_ranker or ContextRanker(self._counter)
        self._window = window_manager or ContextWindowManager(self._counter)
        self._max_tokens = max_token_calculator or MaxTokenCalculator()
        self._input_budget = InputBudget()
        self._summarizer = SummaryCompressor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble(
        self,
        handler: BaseTypeHandler,
        user_input: str,
        session_id: str = "default",
        history_limit: int = 20,
        model_id: str = "",
        context_window: int = 0,
        max_output_tokens: int | None = None,
        max_tokens: int | None = None,
        model_max_output: int = 0,
    ) -> AssembledContext:
        """Assemble messages, trim history, apply caching, and compute budgets.

        When ``context_window`` is unknown (≤ 0) the assembler falls back to
        the old simple behaviour: include all recent history and use the
        override/default output limit.
        """
        system_prompt = self._system_mgr.get_system_prompt(handler.task_type)
        if not system_prompt:
            system_prompt = handler.get_system_prompt()

        think_prompt = self._think_mgr.get_think_prompt(handler.task_type)
        if not think_prompt:
            think_prompt = handler.get_think_prompt()

        active_think_prompt = think_prompt if handler.enable_thinking else None
        all_history = self._store.get_history_as_api_messages(session_id)
        history = all_history[-history_limit:] if history_limit else all_history
        older = all_history[: len(all_history) - len(history)]
        output_override = max_output_tokens if max_output_tokens is not None else max_tokens

        # ── Simple path: no context_window info ──────────────────────
        if context_window <= 0:
            messages = MessageBuilder.build(
                system_prompt, active_think_prompt, user_input, history,
            )
            cached = apply_cache_control(messages, model_id)
            return AssembledContext(
                messages=cached,
                task_type=handler.task_type,
                system_prompt=system_prompt,
                think_prompt=think_prompt,
                estimated_tokens=self._counter.count_messages(cached, model_id),
                max_output_tokens=output_override or 8192,
            )

        # ── Proportional budget path ──────────────────────────────────
        # Step 1: measure base tokens (system + think + current user only)
        base_messages = MessageBuilder.build(
            system_prompt, active_think_prompt, user_input, [],
        )
        base_tokens = self._counter.count_messages(base_messages, model_id)

        effective_model_max = model_max_output if model_max_output > 0 else 0

        # Step 2: compute the proportional budget plan
        plan = self._input_budget.plan(
            context_window=context_window,
            task_type=handler.task_type,
            model_max_output=effective_model_max or self._max_tokens._config.get_model_max(model_id),
            base_input_tokens=base_tokens,
            override_output=output_override,
        )

        # Step 3: select history that fits inside the history budget
        # The budget scales with the window, so on a 1M model it would allow
        # ~500k tokens of history on every turn. Cap what recency has to offer.
        history_budget = min(plan.history_budget, _env_int("HISTORY_MAX_TOKENS", _HISTORY_MAX_TOKENS))
        selected_history = self._ranker.select_top(
            history,
            history_budget,
            query=user_input,
            model=model_id,
        )

        # Step 3b: keep a short extractive digest of history that did not fit,
        # so older turns are not forgotten silently.
        # This includes everything older than the recent window, which used to
        # disappear without a trace after 20 messages.
        selected_ids = {id(m) for m in selected_history}
        dropped = [*older, *(m for m in history if id(m) not in selected_ids)]
        if dropped:
            max_chars = _env_int("HISTORY_SUMMARY_CHARS", 600)
            share = max(1, _env_int("HISTORY_SUMMARY_SHARE", 4))
            digest = self._summarizer.compress_history(dropped, max_summary_length=max_chars)
            digest_msg = {"role": "user", "content": digest["content"]}
            if max_chars > 0 and self._counter.count_messages([digest_msg], model_id) <= history_budget // share:
                selected_history = [digest_msg, *selected_history]

        # Step 4: build full message list and fit to window (safety trim)
        messages = MessageBuilder.build(
            system_prompt, active_think_prompt, user_input, selected_history,
        )
        fitted = self._window.fit_to_window(
            messages,
            context_window=context_window,
            max_output_tokens=plan.output_budget,
            model=model_id,
            buffer=TOKEN_BUFFER,
        )

        # Step 5: recount after trimming (window may have dropped extra msgs)
        estimated_tokens = self._counter.count_messages(fitted, model_id)

        # Step 6: apply prompt caching on the final message list
        cached_messages = apply_cache_control(fitted, model_id)

        return AssembledContext(
            messages=cached_messages,
            task_type=handler.task_type,
            system_prompt=system_prompt,
            think_prompt=think_prompt,
            estimated_tokens=estimated_tokens,
            max_output_tokens=max(1, plan.output_budget),
            truncated=(
                len(fitted) != len(messages)
                or len(selected_history) != len(history)
            ),
            history_budget=plan.history_budget,
            output_budget=plan.output_budget,
        )
