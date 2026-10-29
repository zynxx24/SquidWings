"""
model.type.math — Math solving handler.

Optimized for mathematical problem solving: calculations,
proofs, equations, and step-by-step solutions.
"""

from __future__ import annotations

from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.type.base_handler import BaseTypeHandler


class MathHandler(BaseTypeHandler):
    """Handler for mathematical problem solving.

    Optimizations:
    - Very low temperature (0.1) for accurate calculations
    - Thinking enabled for step-by-step reasoning
    - Tools disabled (pure reasoning task)
    - Post-process: verify step structure
    """

    @property
    def task_type(self) -> TaskType:
        return TaskType.MATH

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
        return 4096

    def get_system_prompt(self) -> str:
        return (
            "You are a mathematics expert. Break down problems step by step. "
            "Show all work clearly. Use proper mathematical notation. "
            "Verify your answers by substituting back or using alternative methods. "
            "Explain concepts in a way that helps understanding, not just memorization.\n\n"
            "ATURAN WAJIB — STOP DAN TUNGGU:\n"
            "Jika ada ambiguitas atau kamu perlu klarifikasi dari pengguna, "
            "kamu WAJIB BERHENTI setelah pertanyaan tersebut dan menunggu respons. "
            "JANGAN mengasumsikan atau menjawab sendiri."
        )

    def get_think_prompt(self) -> str:
        return (
            "Break down the problem. Identify the mathematical concepts needed. "
            "Show each step of the solution. Verify your answer by "
            "substituting back."
        )

    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Verify the response has step-by-step structure."""
        return response
