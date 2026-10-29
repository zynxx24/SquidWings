"""
model.type.generate_img — Image generation handler.

Handles image generation and multimodal image tasks.
Routes primarily to models with vision/image capabilities.
"""

from __future__ import annotations

from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.type.base_handler import BaseTypeHandler


class GenerateImgHandler(BaseTypeHandler):
    """Handler for image generation tasks.

    Optimizations:
    - Higher temperature (0.8) for creative output
    - Thinking disabled (creative task)
    - Tools enabled for potential image operations
    - Routes to multimodal models (Gemini) by default
    """

    @property
    def task_type(self) -> TaskType:
        return TaskType.GENERATE_IMG

    @property
    def default_temperature(self) -> float:
        return 0.8

    @property
    def enable_thinking(self) -> bool:
        return False

    @property
    def enable_tools(self) -> bool:
        return True

    @property
    def default_max_tokens(self) -> int:
        return 4096

    def get_system_prompt(self) -> str:
        return (
            "You are a creative visual assistant. Help users with image "
            "generation, description, and visual content creation. "
            "Provide detailed, vivid descriptions when generating images. "
            "Be creative and imaginative while following user preferences."
        )

    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Process image generation response."""
        return response
