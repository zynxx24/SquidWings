"""
brain.context.context_ranker — Rank relevance of context pieces.

Prioritizes the most relevant context pieces when the context
window is tight, ensuring the most important information is kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brain.generate_efficient_token.token_counter import TokenCounter
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RankedContext:
    """A context piece with its relevance score.

    Attributes:
        content: The context text.
        source: Where this context came from (e.g., "history", "system").
        score: Relevance score (higher = more relevant).
        token_count: Estimated token count.
    """
    content: str
    source: str
    score: float
    token_count: int = 0
    original_index: int = -1


class ContextRanker:
    """Ranks and selects the most relevant context pieces.

    When the context window is tight, this ranker scores each
    piece of context by its relevance to the current query
    and selects the highest-scoring pieces that fit.

    Scoring factors:
    - Recency: More recent messages score higher
    - Role relevance: System prompts > assistant > user (for history)
    - Keyword overlap: Context containing query terms scores higher
    - Length penalty: Very long pieces are slightly penalized
    """

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        """Initialize the ranker.

        Args:
            token_counter: TokenCounter instance.
        """
        self._counter = token_counter or TokenCounter()

    def rank(
        self,
        context_pieces: list[dict[str, Any]],
        query: str = "",
        model: str = "",
    ) -> list[RankedContext]:
        """Rank context pieces by relevance to the query.

        Args:
            context_pieces: List of message dicts or context dicts.
            query: The current user query for relevance scoring.
            model: Model ID for token counting.

        Returns:
            List of RankedContext sorted by score (highest first).
        """
        ranked: list[RankedContext] = []
        total = len(context_pieces)
        query_terms = set(query.lower().split()) if query else set()

        for i, piece in enumerate(context_pieces):
            content = piece.get("content", "")
            role = piece.get("role", "unknown")
            source = piece.get("source", role)

            if not isinstance(content, str) or not content.strip():
                continue

            score = self._calculate_score(
                content=content,
                role=role,
                position=i,
                total=total,
                query_terms=query_terms,
            )

            token_count = self._counter.count(content, model)

            ranked.append(RankedContext(
                content=content,
                source=source,
                score=score,
                token_count=token_count,
                original_index=i,
            ))

        # Sort by score descending
        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked

    def select_top(
        self,
        context_pieces: list[dict[str, Any]],
        token_budget: int,
        query: str = "",
        model: str = "",
    ) -> list[dict[str, Any]]:
        """Select the most relevant context pieces within a token budget.

        Args:
            context_pieces: List of context message dicts.
            token_budget: Maximum total tokens for selected pieces.
            query: Current user query for relevance scoring.
            model: Model ID.

        Returns:
            Selected context pieces (in original order) that fit the budget.
        """
        if token_budget <= 0 or not context_pieces:
            return []

        ranked = self.rank(context_pieces, query, model)
        selected_indices: set[int] = set()
        used_tokens = 0

        # Track indices explicitly: duplicate content must not select the same
        # original message. Recount the whole candidate list to include message
        # formatting overhead rather than summing content tokens only.
        for candidate in ranked:
            index = candidate.original_index
            if not 0 <= index < len(context_pieces):
                continue
            if index in selected_indices:
                continue

            proposed_indices = sorted([*selected_indices, index])
            proposed_messages = [
                context_pieces[i] for i in proposed_indices
            ]
            proposed_tokens = self._counter.count_messages(
                proposed_messages, model
            )
            if proposed_tokens <= token_budget:
                selected_indices.add(index)
                used_tokens = proposed_tokens

        result = [context_pieces[i] for i in sorted(selected_indices)]
        logger.debug(
            f"[ranker] Selected {len(result)}/{len(context_pieces)} pieces, "
            f"budget used: {used_tokens}/{token_budget}"
        )
        return result

    def _calculate_score(
        self,
        content: str,
        role: str,
        position: int,
        total: int,
        query_terms: set[str],
    ) -> float:
        """Calculate relevance score for a context piece.

        Args:
            content: The text content.
            role: Message role.
            position: Position in the original list (0-indexed).
            total: Total number of pieces.
            query_terms: Set of query terms for keyword matching.

        Returns:
            float: Relevance score (0.0 to 1.0+).
        """
        score = 0.0

        # Recency bonus (more recent = higher score)
        # Position 0 is oldest, total-1 is newest
        if total > 1:
            recency = position / (total - 1)  # 0.0 to 1.0
            score += recency * 0.4  # Max 0.4 from recency

        # Role bonus
        role_scores = {
            "system": 0.3,
            "user": 0.15,
            "assistant": 0.1,
            "tool": 0.05,
        }
        score += role_scores.get(role, 0.05)

        # Keyword overlap bonus
        if query_terms:
            content_terms = set(content.lower().split())
            overlap = len(query_terms & content_terms)
            if overlap > 0:
                overlap_ratio = overlap / len(query_terms)
                score += overlap_ratio * 0.3  # Max 0.3 from keywords

        # Length penalty (very long pieces are slightly penalized)
        content_len = len(content)
        if content_len > 2000:
            penalty = min(0.1, (content_len - 2000) / 20000)
            score -= penalty

        return max(0.0, score)
