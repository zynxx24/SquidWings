"""brain.generate_efficient_token — Token optimisation utilities.

Modules
-------
token_counter
    Count tokens for messages using tiktoken or char-ratio approximation.
token_optimizer
    Trim / compress message lists to fit within a token budget.
compression_strategy
    Strategy pattern for different compression approaches.
prompt_cache
    Mark stable content blocks for provider-side prompt caching.
input_budget
    Proportional input/output budget planning per task type.
"""

from brain.generate_efficient_token.input_budget import BudgetPlan, InputBudget
from brain.generate_efficient_token.prompt_cache import (
    apply_cache_control,
    estimate_cached_tokens,
)
from brain.generate_efficient_token.token_counter import TokenCounter
from brain.generate_efficient_token.token_optimizer import TokenOptimizer

__all__ = [
    "TokenCounter",
    "TokenOptimizer",
    "InputBudget",
    "BudgetPlan",
    "apply_cache_control",
    "estimate_cached_tokens",
]
