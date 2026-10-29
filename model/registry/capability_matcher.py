"""
model.registry.capability_matcher — Match task requirements to model capabilities.

Scores models based on how well they match the requirements
of a specific task, enabling smart routing decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.constants import TaskType
from model.registry.model_info import ModelInfo
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TaskRequirements:
    """Requirements for a task that influence model selection.

    Attributes:
        task_type: The type of task.
        needs_vision: Whether the task involves image input.
        needs_audio: Whether the task involves audio input.
        needs_video: Whether the task involves video input.
        needs_search: Whether web search is needed.
        needs_tools: Whether function calling is needed.
        needs_reasoning: Whether deep reasoning is needed.
        prefer_free: Whether to prefer free models.
        prefer_fast: Whether to prefer fast models.
        min_output_tokens: Minimum output tokens required.
        estimated_input_tokens: Estimated input size.
    """

    task_type: TaskType = TaskType.ACTIVITY
    needs_vision: bool = False
    needs_audio: bool = False
    needs_video: bool = False
    needs_search: bool = False
    needs_tools: bool = False
    needs_reasoning: bool = True
    prefer_free: bool = False
    prefer_fast: bool = False
    min_output_tokens: int = 0
    estimated_input_tokens: int = 0

    @classmethod
    def for_task_type(cls, task_type: TaskType) -> TaskRequirements:
        """Create requirements based on task type defaults.

        Args:
            task_type: The task type.

        Returns:
            TaskRequirements with sensible defaults.
        """
        defaults: dict[TaskType, dict[str, Any]] = {
            TaskType.CODE: {
                "needs_tools": True,
                "needs_reasoning": True,
                "min_output_tokens": 100,
            },
            TaskType.MATH: {
                "needs_reasoning": True,
                "min_output_tokens": 50,
            },
            TaskType.ACTIVITY: {
                "needs_reasoning": False,
                "prefer_fast": True,
            },
            TaskType.SCHOOL_TASK: {
                "needs_reasoning": True,
                "min_output_tokens": 100,
            },
            TaskType.GENERATE_IMG: {
                "needs_vision": True,
                "needs_tools": True,
            },
            TaskType.REASON: {
                "needs_reasoning": True,
                "min_output_tokens": 200,
            },
            TaskType.MULTIMODAL: {
                "needs_vision": True,
                "needs_audio": True,
                "needs_video": True,
            },
        }

        kwargs = defaults.get(task_type, {})
        return cls(task_type=task_type, **kwargs)


@dataclass
class MatchResult:
    """Result of matching a model against task requirements.

    Attributes:
        model: The matched model.
        score: Match score (higher = better).
        reasons: List of reasons affecting the score.
    """

    model: ModelInfo
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def add_score(self, points: float, reason: str) -> None:
        """Add points with explanation."""
        self.score += points
        self.reasons.append(f"+{points}: {reason}")

    def subtract_score(self, points: float, reason: str) -> None:
        """Subtract points with explanation."""
        self.score -= points
        self.reasons.append(f"-{points}: {reason}")


class CapabilityMatcher:
    """Matches task requirements against model capabilities.

    Scores each candidate model and returns them sorted by
    how well they match the requirements.
    """

    # Scoring weights
    VISION_MATCH = 15.0
    AUDIO_MATCH = 15.0
    VIDEO_MATCH = 15.0
    SEARCH_MATCH = 10.0
    TOOLS_MATCH = 10.0
    REASONING_MATCH = 10.0
    FREE_BONUS = 5.0
    FAST_TAG_BONUS = 5.0
    OUTPUT_CAPACITY_BONUS = 3.0
    CONTEXT_CAPACITY_BONUS = 2.0

    # Penalties
    MISSING_REQUIRED_PENALTY = -50.0

    def match(
        self,
        requirements: TaskRequirements,
        candidates: list[ModelInfo],
    ) -> list[MatchResult]:
        """Score and rank candidate models against requirements.

        Args:
            requirements: What the task needs.
            candidates: Models to evaluate.

        Returns:
            list: MatchResult objects sorted by score (highest first).
        """
        results: list[MatchResult] = []

        for model in candidates:
            result = self._score_model(model, requirements)
            results.append(result)

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)

        if results:
            logger.debug(
                f"[matcher] Top match: {results[0].model.id} "
                f"(score={results[0].score:.1f})"
            )

        return results

    def _score_model(
        self,
        model: ModelInfo,
        req: TaskRequirements,
    ) -> MatchResult:
        """Score a single model against requirements.

        Args:
            model: Model to score.
            req: Task requirements.

        Returns:
            MatchResult with score and reasons.
        """
        result = MatchResult(model=model)

        # --- Required capabilities (hard requirements) ---

        if req.needs_vision:
            if model.supports_vision:
                result.add_score(self.VISION_MATCH, "vision supported")
            else:
                result.subtract_score(abs(self.MISSING_REQUIRED_PENALTY), "vision REQUIRED but missing")

        if req.needs_audio:
            if model.supports_audio:
                result.add_score(self.AUDIO_MATCH, "audio supported")
            else:
                result.subtract_score(abs(self.MISSING_REQUIRED_PENALTY), "audio REQUIRED but missing")

        if req.needs_video:
            if model.supports_video:
                result.add_score(self.VIDEO_MATCH, "video supported")
            else:
                result.subtract_score(abs(self.MISSING_REQUIRED_PENALTY), "video REQUIRED but missing")

        # --- Soft preferences ---

        if req.needs_search and model.supports_search:
            result.add_score(self.SEARCH_MATCH, "search supported")

        if req.needs_tools and model.supports_tools:
            result.add_score(self.TOOLS_MATCH, "tools supported")

        if req.needs_reasoning and model.supports_reasoning:
            result.add_score(self.REASONING_MATCH, "reasoning supported")

        # Free model preference
        if req.prefer_free and model.is_free:
            result.add_score(self.FREE_BONUS, "free tier preferred")

        # Fast model preference
        if req.prefer_fast and "fast" in model.tags:
            result.add_score(self.FAST_TAG_BONUS, "fast model preferred")

        # Output capacity bonus
        if req.min_output_tokens > 0 and model.max_output >= req.min_output_tokens:
            result.add_score(self.OUTPUT_CAPACITY_BONUS, f"output capacity >= {req.min_output_tokens}")
        elif req.min_output_tokens > 0 and model.max_output < req.min_output_tokens:
            result.subtract_score(abs(self.MISSING_REQUIRED_PENALTY), f"output too low ({model.max_output} < {req.min_output_tokens})")

        # Context window bonus
        if req.estimated_input_tokens > 0 and model.context_window >= req.estimated_input_tokens:
            result.add_score(self.CONTEXT_CAPACITY_BONUS, "context window sufficient")

        return result

    def best_match(
        self,
        requirements: TaskRequirements,
        candidates: list[ModelInfo],
    ) -> ModelInfo | None:
        """Get the single best matching model.

        Args:
            requirements: What the task needs.
            candidates: Models to evaluate.

        Returns:
            Best matching ModelInfo, or None if no candidates.
        """
        results = self.match(requirements, candidates)
        if results and results[0].score > 0:
            return results[0].model
        return results[0].model if results else None
