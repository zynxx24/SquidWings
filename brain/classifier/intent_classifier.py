"""
brain.classifier.intent_classifier — Main intent classification entry point.

Orchestrates keyword rules, pattern matching, and confidence scoring
to classify user input into a TaskType.
"""

from __future__ import annotations

from brain.classifier.confidence_scorer import ClassificationResult, ConfidenceScorer
from brain.classifier.keyword_rules import get_keyword_matches
from brain.classifier.llm_classifier import LLMIntentClassifier
from brain.classifier.pattern_matcher import get_pattern_matches
from config.constants import CLASSIFICATION_CONFIDENCE_THRESHOLD, TaskType
from utils.logger import get_logger

logger = get_logger(__name__)


class IntentClassifier:
    """Classifies user input into a TaskType.

    Pipeline:
    1. Run keyword matching (fast, broad)
    2. Run pattern matching (regex, specific)
    3. Combine scores via ConfidenceScorer
    4. If confidence < threshold → fallback to ACTIVITY

    Usage:
        classifier = IntentClassifier()
        result = classifier.classify("buatkan kode python sorting")
        print(result.task_type)  # TaskType.CODE
        print(result.confidence)  # 0.95
    """

    def __init__(
        self,
        confidence_threshold: float = CLASSIFICATION_CONFIDENCE_THRESHOLD,
        default_type: TaskType = TaskType.ACTIVITY,
        llm_classifier: LLMIntentClassifier | None = None,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.scorer = ConfidenceScorer(default_type=default_type)
        self._llm_classifier = llm_classifier

    def classify(self, user_input: str) -> ClassificationResult:
        """Classify user input into a TaskType.

        Args:
            user_input: The user's text input.

        Returns:
            ClassificationResult with task_type and confidence.
        """
        if not user_input.strip():
            return ClassificationResult(
                task_type=TaskType.ACTIVITY,
                confidence=0.0,
                all_scores={},
                matched_rules=["empty_input"],
            )

        # Step 1: Keyword matching
        keyword_matches = get_keyword_matches(user_input)

        # Step 2: Pattern matching
        pattern_matches = get_pattern_matches(user_input)

        # Step 3: Score and combine
        result = self.scorer.score(keyword_matches, pattern_matches)

        # Step 4: Log and return
        logger.info(
            f"[classifier] '{user_input[:50]}...' → "
            f"{result.task_type.value} (conf={result.confidence:.2f})"
        )

        return result

    async def classify_async(self, user_input: str) -> ClassificationResult:
        """Use the LLM only when inexpensive heuristics are uncertain."""
        result = self.classify(user_input)
        if (
            self._llm_classifier is None
            or not user_input.strip()
            or result.confidence >= self.confidence_threshold
        ):
            return result
        try:
            refined = await self._llm_classifier.classify(user_input)
        except Exception as exc:
            # Auxiliary classification failures must not fail the request.
            logger.warning(f"[classifier] LLM fallback failed: {exc}")
            result.matched_rules = [
                *result.matched_rules,
                "llm_fallback:error",
            ]
            return result

        if refined is not None and refined.confidence >= result.confidence:
            refined.matched_rules = [
                *result.matched_rules,
                *refined.matched_rules,
            ]
            logger.info(
                f"[classifier] LLM fallback: {result.task_type.value} -> "
                f"{refined.task_type.value} (conf={refined.confidence:.2f})"
            )
            return refined

        reason = "unavailable" if refined is None else "lower_confidence"
        result.matched_rules = [
            *result.matched_rules,
            f"llm_fallback:{reason}",
        ]
        return result

    def classify_with_difficulty(
        self, user_input: str
    ) -> tuple[ClassificationResult, str]:
        """Classify input and estimate difficulty.

        Uses simple heuristics to guess difficulty:
        - Short/simple input → easy
        - Medium complexity → medium
        - Long/complex/multiple requirements → hard

        Args:
            user_input: The user's text input.

        Returns:
            Tuple of (ClassificationResult, difficulty).
        """
        result = self.classify(user_input)
        return result, self.estimate_difficulty(user_input)

    async def classify_async_with_difficulty(
        self, user_input: str
    ) -> tuple[ClassificationResult, str]:
        result = await self.classify_async(user_input)
        return result, self.estimate_difficulty(user_input)

    @staticmethod
    def estimate_difficulty(user_input: str) -> str:
        """Estimate routing difficulty independently of intent detection."""
        word_count = len(user_input.split())

        # Complexity indicators
        has_multiple_requirements = any(
            marker in user_input.lower()
            for marker in ["dan juga", "serta", "kemudian", "lalu", "also", "then", "and also"]
        )
        has_code_block = "```" in user_input
        has_complex_keywords = any(
            kw in user_input.lower()
            for kw in [
                "complex", "kompleks", "advanced", "lanjutan",
                "optimize", "optimasi", "architecture", "arsitektur",
                "deep", "mendalam",
            ]
        )

        if has_complex_keywords or (word_count > 100) or has_multiple_requirements:
            difficulty = "hard"
        elif word_count > 30 or has_code_block:
            difficulty = "medium"
        else:
            difficulty = "easy"

        logger.debug(
            f"[classifier] Difficulty: {difficulty} "
            f"(words={word_count}, complex={has_complex_keywords})"
        )

        return difficulty
