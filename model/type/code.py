"""
model.type.code — Code generation handler.

Optimized for programming tasks: code generation, debugging,
refactoring, and code review. Uses low temperature and enables
tool calling for file operations.
"""

from __future__ import annotations

from config.constants import TaskType
from model.connection.response_handler import ParsedResponse
from model.type.base_handler import BaseTypeHandler
from utils.helpers import extract_code_blocks


class CodeHandler(BaseTypeHandler):
    """Handler for code generation and programming tasks.

    Optimizations:
    - Low temperature (0.2) for deterministic code output
    - Thinking enabled for architecture planning
    - Tools enabled for file operations
    - Post-process: extract and validate code blocks
    """

    @property
    def task_type(self) -> TaskType:
        return TaskType.CODE

    @property
    def default_temperature(self) -> float:
        return 0.2

    @property
    def enable_thinking(self) -> bool:
        return True

    @property
    def enable_tools(self) -> bool:
        return True

    @property
    def default_max_tokens(self) -> int:
        return 16384

    def get_system_prompt(self) -> str:
        return (
            "You are an expert software engineer. Write clean, efficient, "
            "and well-documented code. Follow best practices for the language "
            "being used. Consider edge cases, error handling, and performance. "
            "When modifying existing code, preserve existing comments and structure "
            "unless specifically asked to change them. Always explain your approach "
            "before writing code.\n\n"
            "ATURAN WAJIB — STOP DAN TUNGGU:\n"
            "Jika kamu perlu bertanya kepada pengguna (konfirmasi, klarifikasi, atau pilihan), "
            "kamu WAJIB BERHENTI MENULIS setelah pertanyaan tersebut. "
            "DILARANG KERAS melanjutkan, mengasumsikan jawaban sendiri, atau mengerjakan "
            "sesuatu yang belum dikonfirmasi pengguna. "
            "Tunggu respons pengguna sebelum melanjutkan."
        )

    def get_think_prompt(self) -> str:
        return (
            "Analyze the requirements step by step. Consider edge cases. "
            "Plan the architecture before writing code. Think about error "
            "handling, performance, and maintainability."
        )

    def post_process(self, response: ParsedResponse) -> ParsedResponse:
        """Extract and validate code blocks from the response."""
        if response.has_content:
            code_blocks = extract_code_blocks(response.content)
            if code_blocks:
                response.raw["_extracted_code_blocks"] = code_blocks
        return response
