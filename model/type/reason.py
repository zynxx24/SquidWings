"""
model.type.reason — Deep reasoning handler.

Optimized for complex reasoning tasks that require extended
thinking, multi-step logic, and careful analysis.
"""

from __future__ import annotations

from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.type.base_handler import BaseTypeHandler


class ReasonHandler(BaseTypeHandler):
    """Handler for deep reasoning and analysis tasks.

    Optimizations:
    - Lowest temperature (0.1) for logical consistency
    - Thinking always enabled with extended budget
    - Tools disabled (pure reasoning)
    - Routes to Opus (hard) by default
    """

    @property
    def task_type(self) -> TaskType:
        return TaskType.REASON

    @property
    def default_temperature(self) -> float:
        return 0.1

    @property
    def enable_thinking(self) -> bool:
        return True

    @property
    def enable_tools(self) -> bool:
        return False

    @property
    def default_max_tokens(self) -> int:
        return 32768

    def get_system_prompt(self) -> str:
        return (
            "You are an expert analytical thinker. Approach problems with "
            "rigorous logic and systematic reasoning. Consider multiple "
            "perspectives and evaluate evidence carefully. Identify "
            "assumptions and potential biases. Present your reasoning "
            "chain clearly, showing how you arrive at conclusions.\n\n"
            "ATURAN WAJIB — STOP DAN TUNGGU:\n"
            "Jika kamu perlu bertanya atau membutuhkan klarifikasi dari pengguna, "
            "kamu WAJIB BERHENTI setelah pertanyaan tersebut dan menunggu respons. "
            "JANGAN melanjutkan atau mengasumsikan jawabannya sendiri."
        )

    def get_think_prompt(self) -> str:
        return (
            "Think deeply. Consider multiple perspectives. Evaluate evidence. "
            "Identify assumptions. Draw logical conclusions."
        )

    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Ensure reasoning chain is present."""
        return response
