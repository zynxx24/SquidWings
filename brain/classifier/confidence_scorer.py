"""
brain.classifier.confidence_scorer — Combine signals into classification.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from config.constants import TaskType
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ClassificationResult:
    """Result of intent classification.

    Attributes:
        task_type: The classified task type.
        confidence: Confidence score (0.0 - 1.0).
        all_scores: Scores for all task types.
        matched_rules: Rules that contributed to the classification.
    """
    task_type: TaskType
    confidence: float
    all_scores: dict[str, float]
    matched_rules: list[str]

    @property
    def is_confident(self) -> bool:
        """Check if confidence exceeds threshold."""
        return self.confidence >= 0.6


class ConfidenceScorer:
    """Combines keyword and pattern scores into final classification.

    Aggregates scores from multiple signals, normalizes them,
    and produces a ClassificationResult.
    """

    def __init__(self, default_type: TaskType = TaskType.ACTIVITY) -> None:
        self.default_type = default_type

    def score(
        self,
        keyword_matches: list[tuple[TaskType, float]],
        pattern_matches: list[tuple[TaskType, float]],
    ) -> ClassificationResult:
        """Combine all signals into a final classification.

        Args:
            keyword_matches: (TaskType, weight) from keyword rules.
            pattern_matches: (TaskType, confidence) from pattern matcher.

        Returns:
            ClassificationResult with the best task type and confidence.
        """
        scores: dict[TaskType, float] = defaultdict(float)
        rules: list[str] = []

        # Aggregate keyword scores
        for task_type, weight in keyword_matches:
            scores[task_type] += weight
            rules.append(f"keyword:{task_type.value}={weight:.2f}")

        # Aggregate pattern scores (weighted higher — more specific)
        for task_type, confidence in pattern_matches:
            scores[task_type] += confidence * 1.5  # Patterns are more specific
            rules.append(f"pattern:{task_type.value}={confidence:.2f}")

        if not scores:
            return ClassificationResult(
                task_type=self.default_type,
                confidence=0.3,
                all_scores={self.default_type.value: 0.3},
                matched_rules=["fallback:no_matches"],
            )

        # Absolute signal strength and separation from competing intents.
        # Normalising against the winner would always yield confidence 1.0.
        best_type = max(scores, key=lambda task: scores[task])
        best_score = scores[best_type]
        competing_score = max(
            (score for task, score in scores.items() if task != best_type),
            default=0.0,
        )
        strength = 1.0 - pow(2.0, -best_score)
        if competing_score > 0:
            margin = max(0.0, (best_score - competing_score) / best_score)
            best_confidence = strength * (0.65 + 0.35 * margin)
        else:
            best_confidence = strength

        result = ClassificationResult(
            task_type=best_type,
            confidence=round(max(0.0, min(1.0, best_confidence)), 3),
            all_scores={
                task.value: round(min(1.0, 1.0 - pow(2.0, -score)), 3)
                for task, score in scores.items()
            },
            matched_rules=rules,
        )
        logger.debug(
            f"[scorer] Classified as {result.task_type.value} "
            f"(confidence={result.confidence:.2f})"
        )
        return result
