"""Model tiers: how much scaffolding a model gets from the harness."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import yaml

from utils.logger import get_logger

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "brain" / "system_prompt"
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models_config.yaml"

# Used when models_config.yaml has no ``harness.tiers`` section.
_DEFAULT_PATTERNS: dict[str, list[str]] = {
    "frontier": [r"opus", r"gpt-6-sol", r"fable"],
    "lite": [r"flash", r"haiku", r"\bmini\b", r"-mini", r"lite", r"nano", r"free", r"gemma", r"^bansos/"],
}


class Tier(IntEnum):
    """Ordered, so ``advisor tier > worker tier`` reads naturally."""

    LITE = 0
    STANDARD = 1
    FRONTIER = 2


@dataclass(frozen=True)
class Scaffold:
    """Per-tier limits.

    Attributes:
        tier: The tier these limits belong to.
        prompt_file: Operating protocol appended to the system prompt.
        max_tools: Tool schemas offered per request (None = all). Tool schemas
            cost ~5k tokens in total and too many choices confuse small models.
        max_iterations: Tool rounds before the model must write its answer.
        tool_result_chars: Longest tool result kept in the conversation.
        context_chars: Conversation size at which old tool results are dropped.
        stuck_after: Repeats or failures in a row before the loop intervenes.
        verify_rounds: Times a failed verification is sent back to the model.
        advisor_calls: Consultations with a stronger model per run.
        briefing_chars: Budget for auto-injected playbooks and vault notes.
        skill_chars: Budget for skill guides placed in the prompt. Frontier models
            get only the index and can load a guide if they want one.
    """

    tier: Tier
    prompt_file: str
    max_tools: int | None
    max_iterations: int
    tool_result_chars: int
    context_chars: int
    stuck_after: int
    verify_rounds: int
    advisor_calls: int
    briefing_chars: int
    skill_chars: int


_SCAFFOLDS: dict[Tier, Scaffold] = {
    Tier.LITE: Scaffold(Tier.LITE, "harness_lite.txt", 16, 20, 6_000, 60_000, 2, 2, 2, 1_800, 6_000),
    Tier.STANDARD: Scaffold(Tier.STANDARD, "harness_standard.txt", 18, 30, 12_000, 120_000, 3, 2, 1, 1_500, 3_500),
    Tier.FRONTIER: Scaffold(Tier.FRONTIER, "", None, 40, 30_000, 400_000, 4, 1, 0, 800, 0),
}

_patterns_cache: dict[str, list[re.Pattern[str]]] | None = None


def _patterns() -> dict[str, list[re.Pattern[str]]]:
    global _patterns_cache
    if _patterns_cache is None:
        raw = dict(_DEFAULT_PATTERNS)
        try:
            config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            raw.update((config.get("harness") or {}).get("tiers") or {})
        except (OSError, yaml.YAMLError) as exc:
            logger.warning(f"[harness] Could not read tier config: {exc}")
        _patterns_cache = {
            name: [re.compile(p, re.IGNORECASE) for p in items] for name, items in raw.items()
        }
    return _patterns_cache


def tier_for(model_id: str) -> Tier:
    """Classify a model by its id. Unknown models get the middle tier."""
    patterns = _patterns()
    if any(p.search(model_id) for p in patterns.get("frontier", [])):
        return Tier.FRONTIER
    if any(p.search(model_id) for p in patterns.get("lite", [])):
        return Tier.LITE
    return Tier.STANDARD


def scaffold_for(model_id: str) -> Scaffold:
    return _SCAFFOLDS[tier_for(model_id)]


def protocol_text(scaffold: Scaffold) -> str:
    """The operating protocol for a tier, or "" when it needs none."""
    if not scaffold.prompt_file:
        return ""
    try:
        return (_PROMPTS_DIR / scaffold.prompt_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning(f"[harness] Missing protocol file: {exc}")
        return ""
