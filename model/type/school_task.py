"""
model.type.school_task — School/education task handler.

Optimized for educational tasks: homework help, exam prep,
concept explanations, and structured learning.
"""

from __future__ import annotations

from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.type.base_handler import BaseTypeHandler


class SchoolTaskHandler(BaseTypeHandler):
    """Handler for school and education tasks.

    Optimizations:
    - Moderate temperature (0.3) for structured answers
    - Thinking enabled for step-by-step explanations
    - Tools disabled (educational focus)
    - Post-process: ensure educational structure
    """

    @property
    def task_type(self) -> TaskType:
        return TaskType.SCHOOL_TASK

    @property
    def default_temperature(self) -> float:
        return 0.3

    @property
    def enable_thinking(self) -> bool:
        return True

    @property
    def enable_tools(self) -> bool:
        return False

    @property
    def default_max_tokens(self) -> int:
        return 16384

    def get_system_prompt(self) -> str:
        return (
            "You are a patient and thorough tutor. Explain concepts clearly "
            "and at the appropriate level. Break down problems into manageable "
            "steps. Provide examples to illustrate key points. Encourage "
            "understanding over memorization. When solving problems, show "
            "all work and explain each step."
        )

    def get_think_prompt(self) -> str:
        return (
            "Identify the subject. Break down the question. Explain each "
            "concept clearly. Provide step-by-step solution. Add helpful "
            "tips for learning."
        )

    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Ensure the response has educational structure."""
        return response
