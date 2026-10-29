"""
model.type.multimodal — Multimodal input handler.

Handles tasks involving audio, video, and image inputs.
Routes to models with full multimodal support (Gemini).
"""

from __future__ import annotations

from typing import Any

from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.registry.model_info import ModelInfo
from model.type.base_handler import BaseTypeHandler


class MultimodalHandler(BaseTypeHandler):
    """Handler for multimodal tasks (audio, video, image).

    Optimizations:
    - Moderate temperature (0.5)
    - Thinking enabled for analysis
    - Tools enabled for multimodal operations
    - Routes to Gemini (best multimodal support) by default
    """

    @property
    def task_type(self) -> TaskType:
        return TaskType.MULTIMODAL

    @property
    def default_temperature(self) -> float:
        return 0.5

    @property
    def enable_thinking(self) -> bool:
        return True

    @property
    def enable_tools(self) -> bool:
        return True

    @property
    def default_max_tokens(self) -> int:
        return 8192

    def prepare_request_params(self, model: ModelInfo, **overrides: Any) -> dict[str, Any]:
        """Prepare params with multimodal-specific settings."""
        params = super().prepare_request_params(model, **overrides)
        # Could add multimodal-specific config here
        return params

    def get_system_prompt(self) -> str:
        return (
            "You are a multimodal AI assistant capable of understanding "
            "images, audio, and video content. Analyze the provided media "
            "thoroughly and provide detailed, accurate descriptions and "
            "responses based on the content you observe."
        )

    def get_think_prompt(self) -> str:
        return (
            "Carefully analyze all provided media. Describe what you observe "
            "in detail. Consider context and relationships between elements."
        )

    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Process multimodal response."""
        return response
