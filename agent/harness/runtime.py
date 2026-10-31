"""Per-run harness state handed to the agent loop."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .tiers import Scaffold, Tier


@dataclass
class HarnessStats:
    """What the harness did during a run. Sent to the UI and kept in logs."""

    tier: str = ""
    tools_offered: int = 0
    repairs: int = 0
    nudges: int = 0
    compactions: int = 0
    verify_rounds: int = 0
    advisor_calls: int = 0
    escalated_to: str = ""
    playbooks_used: int = 0
    skills_used: list[str] = field(default_factory=list)
    playbook_saved: bool = False
    verified: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    out_of_budget: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunLimits:
    """Caps for a run that shares its time and tokens, such as a team member.

    Each model round resends the whole conversation, so rounds and context size
    multiply. A small model's normal limits allow about 500k tokens for one agent.
    """

    max_rounds: int
    tokens: int
    deadline: float
    context_chars: int = 24_000
    tool_result_chars: int = 6_000
    max_tools: int = 10
    briefing_chars: int = 500
    skill_chars: int = 1_500

    def apply(self, scaffold: Scaffold) -> Scaffold:
        return replace(
            scaffold,
            max_iterations=min(scaffold.max_iterations, self.max_rounds),
            context_chars=min(scaffold.context_chars, self.context_chars),
            tool_result_chars=min(scaffold.tool_result_chars, self.tool_result_chars),
            max_tools=min(self.max_tools if scaffold.max_tools is None else scaffold.max_tools, self.max_tools),
            briefing_chars=min(scaffold.briefing_chars, self.briefing_chars),
            skill_chars=min(scaffold.skill_chars, self.skill_chars),
            verify_rounds=min(scaffold.verify_rounds, 1),
            advisor_calls=0,
        )

    def exceeded(self, tokens_used: int) -> bool:
        return tokens_used >= self.tokens or time.time() >= self.deadline


@dataclass
class Harness:
    """Everything the loop needs to supervise one run.

    Attributes:
        scaffold: Limits for the model's tier.
        tools: Schemas offered to the model on each request.
        parameters: JSON schema of every registered tool, for validation.
        disabled_tools: Tools refused even if the model names them directly.
        user_input: The user's request, for the advisor and playbooks.
        text_tools: Read tool calls from the reply text. For models without native
            tool calling, which get no ``tools`` payload.
        workspace: Directory relative paths and commands resolve against.
        advisor_model: Stronger model to consult when stuck, if one is available.
        on_event: Progress callback for the UI.
        on_reset: Tells the UI to discard text it has already shown.
        limits: Time and token caps, for runs that share a budget.
    """

    scaffold: Scaffold
    tools: list[dict[str, Any]] = field(default_factory=list)
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    disabled_tools: frozenset[str] = frozenset()
    user_input: str = ""
    text_tools: bool = False
    workspace: str = ""
    advisor_model: str | None = None
    max_output: int = 0
    on_event: Callable[[dict[str, Any]], None] | None = None
    on_reset: Callable[[], None] | None = None
    limits: RunLimits | None = None
    stats: HarnessStats = field(default_factory=HarnessStats)

    def __post_init__(self) -> None:
        self.stats.tier = Tier(self.scaffold.tier).name.lower()
        self.stats.tools_offered = len(self.tools)

    def emit(self, **event: Any) -> None:
        if self.on_event:
            self.on_event(event)
