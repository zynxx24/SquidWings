"""
brain.generate_efficient_token.prompt_cache — System-prompt caching layer.

Marks stable, repetitive content blocks with ``cache_control`` so
providers that support prompt caching (Anthropic, some OpenAI-compatible
routes) can skip re-encoding them on every turn.

Strategy
--------
* System prompt and think prompt are the heaviest static blobs — mark them
  as ``ephemeral`` cache breakpoints.
* History messages older than ``min_age_turns`` are also eligible for
  caching because they never change after being written.
* The last two user/assistant turns are **not** cached — they change every
  call and caching them would waste a breakpoint slot.

Why this matters
----------------
Without caching, the entire input is re-tokenised and re-processed by the
model on every turn.  With provider-side caching enabled, only the new
tokens (user input + last turn) are freshly processed.  For long system
prompts this cuts effective input-token cost by 60-90 %.

Provider support
----------------
* Anthropic (Claude): native ``cache_control`` field on content blocks.
* OpenAI-compatible: some routes honour ``cache_control``; others silently
  ignore it — either way, adding it is safe.
* Gemini / DeepSeek: field is ignored; no negative side-effect.
"""

from __future__ import annotations

import copy
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# Providers / model-id prefixes that natively support prompt caching.
_CACHE_SUPPORTED_PREFIXES: tuple[str, ...] = (
    "ag/claude",
    "kg/claude",
    "ag/gemini",   # Gemini silently ignores — safe to include
    "kg/gemini",
    "kg/gpt",
    "ag/gpt",
)

# Minimum system-prompt token count to bother caching.
_MIN_TOKENS_TO_CACHE = 100

# Max number of cache breakpoints per request (Anthropic limit is 4).
_MAX_BREAKPOINTS = 4


def _supports_cache(model_id: str) -> bool:
    """Return True if the model may benefit from prompt caching."""
    ml = (model_id or "").lower()
    # Providers differ in prefix, but a Claude model behind any of them caches.
    return "claude" in ml or any(ml.startswith(p) for p in _CACHE_SUPPORTED_PREFIXES)


def _inject_cache_control(block: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copy of a content block with cache_control added."""
    b = dict(block)
    b["cache_control"] = {"type": "ephemeral"}
    return b


def _content_to_blocks(content: str | list[Any]) -> list[dict[str, Any]]:
    """Normalise str/list content into a list of content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    # Already a list — return a shallow copy of each block
    return [dict(b) if isinstance(b, dict) else b for b in content]


def apply_cache_control(
    messages: list[dict[str, Any]],
    model_id: str = "",
    min_tokens_to_cache: int = _MIN_TOKENS_TO_CACHE,
    max_breakpoints: int = _MAX_BREAKPOINTS,
) -> list[dict[str, Any]]:
    """Return a new messages list with ``cache_control`` injected where useful.

    Parameters
    ----------
    messages:
        The assembled message list (system + history + user input).
    model_id:
        Used to decide whether caching is worth attempting.
    min_tokens_to_cache:
        Skip caching content blocks shorter than this (not worth a breakpoint).
    max_breakpoints:
        Hard cap on cache breakpoints to stay within provider limits.

    Returns
    -------
    list[dict[str, Any]]
        Deep-copied message list; original is never mutated.
    """
    if not messages:
        return messages

    # Always return a copy so callers can mutate safely.
    result: list[dict[str, Any]] = copy.deepcopy(messages)

    if not _supports_cache(model_id):
        logger.debug(
            "[prompt_cache] Caching skipped for model %s (not in support list)",
            model_id,
        )
        return result

    breakpoints_used = 0

    # --- Pass 1: cache system messages (largest static payload) ---
    for msg in result:
        if breakpoints_used >= max_breakpoints:
            break
        if msg.get("role") != "system":
            continue

        content = msg.get("content", "")
        # Rough char→token estimate (4 chars ≈ 1 token)
        approx_tokens = len(str(content)) // 4
        if approx_tokens < min_tokens_to_cache:
            continue

        blocks = _content_to_blocks(content)
        if blocks:
            blocks[-1] = _inject_cache_control(blocks[-1])
            msg["content"] = blocks
            breakpoints_used += 1
            logger.debug(
                "[prompt_cache] Cached system message (%d approx tokens)",
                approx_tokens,
            )

    # --- Pass 2: cache old history (skip last 2 non-system messages) ---
    # Collect indices of non-system messages, newest last.
    non_system_indices = [
        i for i, m in enumerate(result) if m.get("role") != "system"
    ]

    # The last 2 (user turn + assistant turn) are excluded from caching.
    cacheable_history_indices = non_system_indices[:-2] if len(non_system_indices) > 2 else []

    for idx in reversed(cacheable_history_indices):
        if breakpoints_used >= max_breakpoints:
            break

        msg = result[idx]
        content = msg.get("content", "")
        if not content:
            continue

        approx_tokens = len(str(content)) // 4
        if approx_tokens < min_tokens_to_cache:
            continue

        blocks = _content_to_blocks(content)
        if blocks:
            blocks[-1] = _inject_cache_control(blocks[-1])
            msg["content"] = blocks
            breakpoints_used += 1
            logger.debug(
                "[prompt_cache] Cached history[%d] (%d approx tokens, role=%s)",
                idx,
                approx_tokens,
                msg.get("role"),
            )

    if breakpoints_used:
        logger.info(
            "[prompt_cache] Applied %d/%d cache breakpoints for %s",
            breakpoints_used,
            max_breakpoints,
            model_id,
        )

    return result


def estimate_cached_tokens(
    messages: list[dict[str, Any]],
) -> int:
    """Estimate how many tokens are in cache-marked blocks.

    Useful for logging / metrics.  Uses the same 4-chars-per-token
    approximation as the rest of the efficient-token layer.
    """
    cached = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control"):
                    text = block.get("text", "")
                    cached += max(1, len(text) // 4)
    return cached
