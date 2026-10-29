"""
model.type.activity — General activity handler.

Handles everyday tasks: conversations, summaries, translations,
brainstorming, and general questions.
"""

from __future__ import annotations

from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.type.base_handler import BaseTypeHandler


class ActivityHandler(BaseTypeHandler):
    """Handler for general-purpose activity tasks.

    Optimizations:
    - Higher temperature (0.7) for creative/varied responses
    - Thinking disabled by default (fast response)
    - Tools disabled (conversational)
    """

    @property
    def task_type(self) -> TaskType:
        return TaskType.ACTIVITY

    @property
    def default_temperature(self) -> float:
        return 0.7

    @property
    def enable_thinking(self) -> bool:
        return False

    @property
    def enable_tools(self) -> bool:
        return False

    @property
    def default_max_tokens(self) -> int:
        return 8192

    def get_system_prompt(self) -> str:
        return (
            "You are a helpful, friendly, and knowledgeable assistant. "
            "Provide clear, concise, and actionable responses. "
            "Adapt your tone to the context of the conversation. "
            "Be practical and direct.\n\n"
            "ATURAN WAJIB — STOP DAN TUNGGU:\n"
            "Jika kamu perlu bertanya kepada pengguna (konfirmasi, klarifikasi, atau pilihan), "
            "kamu WAJIB BERHENTI MENULIS setelah pertanyaan tersebut. "
            "DILARANG KERAS melanjutkan, mengasumsikan jawaban, atau menjawab sendiri pertanyaanmu. "
            "Tunggu pengguna merespons sebelum melanjutkan ke langkah berikutnya. "
            "Hanya boleh satu pertanyaan per giliran jika perlu klarifikasi."
        )

    def get_think_prompt(self) -> str:
        return (
            "Consider the context. Think about what the user really needs. "
            "Provide actionable and practical advice."
        )

    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Format the response for readability."""
        return response
