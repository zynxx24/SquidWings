"""
tool.planning.flow.step_executor — Execute individual flow steps.

Dispatches each step to the appropriate tool executor
and captures the result.
"""

from __future__ import annotations

import time

from tool.planning.flow.flow_builder import FlowStep
from tool.planning.flow.flow_engine import StepResult
from tool.registry.tool_registry import ToolRegistry
from utils.exceptions import ToolError
from utils.logger import get_logger

logger = get_logger(__name__)


class StepExecutor:
    """Executes individual steps by dispatching to tool executors.

    Each FlowStep specifies a tool_name and tool_args. This executor
    looks up the tool in the ToolRegistry and calls it.
    """

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        """Initialize with a tool registry.

        Args:
            tool_registry: ToolRegistry instance. Uses singleton if None.
        """
        self._registry = tool_registry or ToolRegistry()

    async def execute_step(self, step: FlowStep) -> StepResult:
        """Execute a single flow step.

        Args:
            step: The FlowStep to execute.

        Returns:
            StepResult with the outcome.
        """
        start = time.monotonic()

        logger.info(
            f"[step_exec] Executing: {step.task_id} ({step.name}) "
            f"→ tool={step.tool_name}"
        )

        if not step.tool_name:
            return StepResult(
                task_id=step.task_id,
                success=True,
                output="No tool specified — skipped",
                duration=time.monotonic() - start,
            )

        if not self._registry.has_tool(step.tool_name):
            return StepResult(
                task_id=step.task_id,
                success=False,
                error=f"Tool not found: {step.tool_name}",
                duration=time.monotonic() - start,
            )

        try:
            output = await self._registry.execute(
                step.tool_name,
                step.tool_args,
            )

            duration = time.monotonic() - start
            logger.info(
                f"[step_exec] Completed: {step.task_id} "
                f"({duration:.2f}s)"
            )

            return StepResult(
                task_id=step.task_id,
                success=True,
                output=output,
                duration=duration,
            )

        except ToolError as e:
            duration = time.monotonic() - start
            logger.error(
                f"[step_exec] Failed: {step.task_id} — {e.message}"
            )
            return StepResult(
                task_id=step.task_id,
                success=False,
                error=e.message,
                duration=duration,
            )

        except Exception as e:
            duration = time.monotonic() - start
            logger.error(
                f"[step_exec] Unexpected error: {step.task_id} — {e}"
            )
            return StepResult(
                task_id=step.task_id,
                success=False,
                error=str(e),
                duration=duration,
            )
