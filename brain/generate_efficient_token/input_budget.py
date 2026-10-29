"""
brain.generate_efficient_token.input_budget — Proportional input-token budgeting.

Instead of subtracting a fixed ``TOKEN_BUFFER`` from the context window and
calling the remainder "available for output", this module calculates how much
of the context window should be *reserved for input* (history + prompts) and
how much should be *reserved for output* — based on task type and model
characteristics.

The key shift vs. the old approach
------------------------------------
Old: ``output_limit = context_window - input_tokens - TOKEN_BUFFER``
     (output gets whatever is left after input fills up)

New: ``input_budget   = f(context_window, task_type, model)``
     ``output_budget  = context_window - input_budget - SAFETY_BUFFER``

This means the system *first* decides how much input it is willing to consume,
then trims history to stay inside that budget.  Output tokens are no longer a
leftover — they get a guaranteed, task-appropriate slice.

Rationale
---------
* Reasoning tasks (REASON, MATH) need long outputs; cap input aggressively.
* Code/school tasks benefit from rich history; allow more input.
* Activity/general tasks: balanced 50 % split.
* Multimodal: input can be large (images); keep output allocation modest.

All ratios are soft: ``calculate()`` always clamps to ``[min_output, model_max_output]``
and never lets input_budget underflow below the minimum needed for system
prompt + current user message.
"""

from __future__ import annotations

from config.constants import TOKEN_BUFFER, TaskType
from utils.logger import get_logger

logger = get_logger(__name__)

# --- Per-task input ratio (fraction of context window reserved for INPUT) ---
# The remaining (1 - ratio - safety) fraction goes to OUTPUT.
#
# Tuning notes (2026-10):
#   ACTIVITY raised 0.50→0.60: chat sessions with long history (300+ turns)
#     were hitting the history_budget ceiling and older context was being
#     pruned. 60% input keeps more history visible without shrinking output
#     below _MIN_OUTPUT (2048).
#   REASON raised 0.30→0.40: the old 30% left only ~30k tokens for input on
#     a 100k-window model, causing aggressive history trimming mid-reasoning.
#   CODE/SCHOOL_TASK unchanged — 55% already balances well.
_INPUT_RATIO: dict[str, float] = {
    TaskType.CODE.value:        0.55,   # rich history helps; output is long
    TaskType.MATH.value:        0.35,   # output is the working-out; keep small input
    TaskType.ACTIVITY.value:    0.60,   # more history → better conversational continuity
    TaskType.SCHOOL_TASK.value: 0.55,   # context matters
    TaskType.GENERATE_IMG.value: 0.25,  # prompt is short; output (url/desc) is short
    TaskType.REASON.value:      0.40,   # raised: long history + chain-of-thought both matter
    TaskType.MULTIMODAL.value:  0.45,   # images eat input tokens
}

# Absolute minimum output tokens we always guarantee, per task type.
_MIN_OUTPUT: dict[str, int] = {
    TaskType.CODE.value:        4096,
    TaskType.MATH.value:        2048,
    TaskType.ACTIVITY.value:    2048,
    TaskType.SCHOOL_TASK.value: 2048,
    TaskType.GENERATE_IMG.value: 512,
    TaskType.REASON.value:      4096,
    TaskType.MULTIMODAL.value:  1024,
}

# Absolute maximum output tokens we ever request, per task type.
# (Even if the model supports more, we don't need it — saves cost.)
_MAX_OUTPUT: dict[str, int] = {
    TaskType.CODE.value:        16384,
    TaskType.MATH.value:        8192,
    TaskType.ACTIVITY.value:    8192,
    TaskType.SCHOOL_TASK.value: 16384,
    TaskType.GENERATE_IMG.value: 2048,
    TaskType.REASON.value:      32768,
    TaskType.MULTIMODAL.value:  8192,
}

# Safety buffer subtracted from context_window before any budget split.
# Slightly larger than the old TOKEN_BUFFER because we're budgeting both sides.
_SAFETY_BUFFER = max(TOKEN_BUFFER, 512)


class InputBudget:
    """Calculate proportional input/output token budgets for a request.

    Usage::

        budget = InputBudget()
        plan = budget.plan(
            context_window=1_000_000,
            task_type=TaskType.REASON,
            model_max_output=128_000,
            base_input_tokens=2_048,   # system prompt + current user message
        )
        # plan.input_budget  → how many tokens history is allowed to use
        # plan.output_budget → what to pass as max_tokens to the API
    """

    def plan(
        self,
        context_window: int,
        task_type: TaskType = TaskType.ACTIVITY,
        model_max_output: int = 8192,
        base_input_tokens: int = 0,
        override_output: int | None = None,
    ) -> "BudgetPlan":
        """Return a :class:`BudgetPlan` for the given context.

        Parameters
        ----------
        context_window:
            Full context window of the model (tokens).
        task_type:
            Task type — drives the input/output ratio split.
        model_max_output:
            Hard maximum output the model supports.
        base_input_tokens:
            Tokens already committed: system prompt + think prompt + current
            user message.  History budget = ``input_budget - base_input_tokens``.
        override_output:
            Caller-requested output limit.  Honoured only when it fits.
        """
        usable = max(1, context_window - _SAFETY_BUFFER)
        ratio = _INPUT_RATIO.get(task_type.value, 0.50)

        raw_input_budget = int(usable * ratio)
        raw_output_budget = usable - raw_input_budget

        task_min = _MIN_OUTPUT.get(task_type.value, 1024)
        task_max = _MAX_OUTPUT.get(task_type.value, 8192)

        # Hard ceiling: the model limit AND the room left after the committed
        # base input. Output must never push the request past the window.
        model_cap = model_max_output if model_max_output > 0 else task_max
        hard_cap = max(1, min(model_cap, usable - base_input_tokens))

        if override_output and override_output > 0:
            # Explicit caller request: honoured up to the hard ceiling
            # (task_max is a cost default, not a limit on explicit overrides).
            output_budget = min(override_output, hard_cap)
        else:
            output_budget = min(raw_output_budget, task_max, hard_cap)

        # Guarantee the task minimum only when it actually fits.
        output_budget = max(output_budget, min(task_min, hard_cap))

        # Recompute input budget from the clamped output
        input_budget = max(base_input_tokens, usable - output_budget)

        # History is what's left in the input budget after base tokens
        history_budget = max(0, input_budget - base_input_tokens)

        plan = BudgetPlan(
            context_window=context_window,
            usable_tokens=usable,
            input_budget=input_budget,
            history_budget=history_budget,
            output_budget=output_budget,
            base_input_tokens=base_input_tokens,
            task_type=task_type,
            ratio_used=ratio,
        )

        logger.debug(
            "[input_budget] %s | ctx=%d usable=%d in=%d hist=%d out=%d ratio=%.0f%%",
            task_type.value,
            context_window,
            usable,
            input_budget,
            history_budget,
            output_budget,
            ratio * 100,
        )
        return plan


class BudgetPlan:
    """Immutable result from :meth:`InputBudget.plan`.

    Attributes
    ----------
    context_window:
        Full context window (tokens).
    usable_tokens:
        Context window minus safety buffer.
    input_budget:
        Maximum tokens the entire input (prompts + history) may consume.
    history_budget:
        Tokens available for conversation history (input_budget - base tokens).
    output_budget:
        Tokens to request for model output (``max_tokens`` parameter).
    base_input_tokens:
        System/think prompt + current user message tokens (committed).
    task_type:
        Task type used for this calculation.
    ratio_used:
        Input fraction that was applied.
    """

    __slots__ = (
        "context_window",
        "usable_tokens",
        "input_budget",
        "history_budget",
        "output_budget",
        "base_input_tokens",
        "task_type",
        "ratio_used",
    )

    def __init__(
        self,
        context_window: int,
        usable_tokens: int,
        input_budget: int,
        history_budget: int,
        output_budget: int,
        base_input_tokens: int,
        task_type: TaskType,
        ratio_used: float,
    ) -> None:
        self.context_window = context_window
        self.usable_tokens = usable_tokens
        self.input_budget = input_budget
        self.history_budget = history_budget
        self.output_budget = output_budget
        self.base_input_tokens = base_input_tokens
        self.task_type = task_type
        self.ratio_used = ratio_used

    def __repr__(self) -> str:
        return (
            f"BudgetPlan("
            f"task={self.task_type.value}, "
            f"ctx={self.context_window}, "
            f"in={self.input_budget}, "
            f"hist={self.history_budget}, "
            f"out={self.output_budget}"
            f")"
        )
