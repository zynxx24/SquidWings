"""
tool.planning.logic.decomposer — Decompose complex tasks into sub-tasks.

Breaks down a complex task description into smaller, actionable
sub-tasks with estimated effort and dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SubTask:
    """A decomposed sub-task.

    Attributes:
        name: Short task name.
        description: What this sub-task does.
        task_type: Type of task (code, command, edit, etc.).
        estimated_tokens: Estimated token cost.
        depends_on: Names of tasks this depends on.
        priority: Priority level (higher = more important).
        tool_name: Suggested tool to use.
        tool_args: Suggested tool arguments.
    """
    name: str
    description: str = ""
    task_type: str = "general"
    estimated_tokens: int = 500
    depends_on: list[str] = field(default_factory=list)
    priority: int = 0
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)


class TaskDecomposer:
    """Decomposes complex tasks into sub-tasks.

    Uses rule-based heuristics for decomposition. For LLM-based
    decomposition, an API client can be injected.
    """

    def __init__(self, api_client: Any = None) -> None:
        """Initialize the decomposer.

        Args:
            api_client: Optional API client for LLM-based decomposition.
        """
        self._client = api_client

    def decompose(
        self,
        task_description: str,
        context: dict[str, Any] | None = None,
    ) -> list[SubTask]:
        """Decompose a complex task into sub-tasks.

        Uses heuristic rules to identify actionable steps.

        Args:
            task_description: The complex task to break down.
            context: Optional context (file paths, codebase info).

        Returns:
            List of SubTask objects.
        """
        sub_tasks: list[SubTask] = []
        desc_lower = task_description.lower()

        # Heuristic: detect common multi-step patterns
        if "refactor" in desc_lower:
            sub_tasks = self._decompose_refactor(task_description, context)
        elif "test" in desc_lower and "write" in desc_lower:
            sub_tasks = self._decompose_write_tests(task_description, context)
        elif "fix" in desc_lower or "bug" in desc_lower:
            sub_tasks = self._decompose_bugfix(task_description, context)
        elif "create" in desc_lower or "implement" in desc_lower:
            sub_tasks = self._decompose_implement(task_description, context)
        else:
            # Generic: single-step task
            sub_tasks = [
                SubTask(
                    name="Execute task",
                    description=task_description,
                    priority=5,
                )
            ]

        logger.info(
            f"[decomposer] Decomposed into {len(sub_tasks)} sub-tasks"
        )
        return sub_tasks

    def _decompose_refactor(self, desc: str, ctx: dict | None) -> list[SubTask]:
        return [
            SubTask(name="Analyze code", description="Understand current structure", task_type="code", priority=4),
            SubTask(name="Plan refactoring", description="Design new structure", task_type="code", priority=3, depends_on=["Analyze code"]),
            SubTask(name="Apply changes", description="Implement the refactoring", task_type="code", priority=2, depends_on=["Plan refactoring"], tool_name="edit_file"),
            SubTask(name="Run tests", description="Verify nothing broke", task_type="code", priority=1, depends_on=["Apply changes"], tool_name="execute_command", tool_args={"command": "pytest -x"}),
        ]

    def _decompose_write_tests(self, desc: str, ctx: dict | None) -> list[SubTask]:
        return [
            SubTask(name="Analyze source code", description="Identify functions to test", task_type="code", priority=4),
            SubTask(name="Design test cases", description="Plan test scenarios", task_type="code", priority=3, depends_on=["Analyze source code"]),
            SubTask(name="Write tests", description="Implement test code", task_type="code", priority=2, depends_on=["Design test cases"], tool_name="edit_file"),
            SubTask(name="Run tests", description="Execute and verify", task_type="code", priority=1, depends_on=["Write tests"], tool_name="execute_command", tool_args={"command": "pytest -v"}),
        ]

    def _decompose_bugfix(self, desc: str, ctx: dict | None) -> list[SubTask]:
        return [
            SubTask(name="Reproduce bug", description="Understand and reproduce the issue", task_type="code", priority=5),
            SubTask(name="Identify root cause", description="Trace the source of the bug", task_type="code", priority=4, depends_on=["Reproduce bug"]),
            SubTask(name="Apply fix", description="Implement the fix", task_type="code", priority=3, depends_on=["Identify root cause"], tool_name="edit_file"),
            SubTask(name="Verify fix", description="Run tests to confirm", task_type="code", priority=2, depends_on=["Apply fix"], tool_name="execute_command", tool_args={"command": "pytest -x"}),
        ]

    def _decompose_implement(self, desc: str, ctx: dict | None) -> list[SubTask]:
        return [
            SubTask(name="Design", description="Plan architecture and interfaces", task_type="code", priority=4),
            SubTask(name="Implement", description="Write the code", task_type="code", priority=3, depends_on=["Design"], tool_name="edit_file"),
            SubTask(name="Test", description="Write and run tests", task_type="code", priority=2, depends_on=["Implement"], tool_name="execute_command", tool_args={"command": "pytest -x"}),
        ]
