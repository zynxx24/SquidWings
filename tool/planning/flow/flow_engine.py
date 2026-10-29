"""
tool.planning.flow.flow_engine — Execute a flow DAG.

Runs steps in topological order, with parallel execution
for independent steps within the same group.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from tool.planning.flow.step_executor import StepExecutor

from tool.planning.flow.flow_builder import ExecutionFlow, FlowStep
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class StepResult:
    """Result from executing a single step."""
    task_id: str
    success: bool = True
    output: Any = None
    error: str = ""
    duration: float = 0.0


@dataclass
class FlowResult:
    """Result from executing an entire flow."""
    flow_name: str
    success: bool = True
    step_results: list[StepResult] = field(default_factory=list)
    total_duration: float = 0.0
    failed_steps: list[str] = field(default_factory=list)

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.step_results if r.success)

    @property
    def summary(self) -> str:
        return (
            f"Flow '{self.flow_name}': "
            f"{self.completed_count}/{len(self.step_results)} steps completed "
            f"in {self.total_duration:.2f}s"
        )


class FlowEngine:
    """Executes a flow DAG.

    Runs steps in topological order. Steps within the same
    parallel group are executed concurrently using asyncio.gather.
    """

    def __init__(
        self,
        step_executor: StepExecutor | Any = None,
        max_parallel: int = 5,
        stop_on_failure: bool = True,
    ) -> None:
        """Initialize the engine.

        Args:
            step_executor: StepExecutor instance for running individual steps.
            max_parallel: Maximum concurrent steps.
            stop_on_failure: Whether to stop the flow on first failure.
        """
        self._executor = step_executor
        self._max_parallel = max_parallel
        self._stop_on_failure = stop_on_failure

    async def execute(self, flow: ExecutionFlow) -> FlowResult:
        """Execute a flow DAG.

        Args:
            flow: The ExecutionFlow to execute.

        Returns:
            FlowResult with all step outcomes.
        """
        start = time.monotonic()
        result = FlowResult(flow_name=flow.name)

        logger.info(
            f"[flow_engine] Starting '{flow.name}': "
            f"{flow.total_steps} steps, "
            f"{len(flow.parallel_groups)} groups"
        )

        for group_idx, group in enumerate(flow.parallel_groups):
            logger.debug(
                f"[flow_engine] Group {group_idx + 1}: {group}"
            )

            # Execute group (parallel or sequential)
            if len(group) == 1:
                step = flow.get_step(group[0])
                if step:
                    sr = await self._execute_step(step)
                    result.step_results.append(sr)
                    if not sr.success:
                        result.failed_steps.append(sr.task_id)
            else:
                # Parallel execution (limited concurrency)
                semaphore = asyncio.Semaphore(self._max_parallel)

                async def run_with_sem(
                    s: FlowStep,
                    sem: asyncio.Semaphore = semaphore,
                ) -> StepResult:
                    async with sem:
                        return await self._execute_step(s)

                steps_in_group = [
                    flow.get_step(tid) for tid in group
                ]
                tasks = [
                    run_with_sem(s) for s in steps_in_group if s
                ]
                step_results = await asyncio.gather(*tasks)
                for sr in step_results:
                    result.step_results.append(sr)
                    if not sr.success:
                        result.failed_steps.append(sr.task_id)

            # Check stop condition
            if self._stop_on_failure and result.failed_steps:
                logger.warning(
                    f"[flow_engine] Stopping: step(s) failed: "
                    f"{result.failed_steps}"
                )
                result.success = False
                break

        result.total_duration = time.monotonic() - start
        result.success = len(result.failed_steps) == 0

        logger.info(f"[flow_engine] {result.summary}")
        return result

    async def _execute_step(self, step: FlowStep) -> StepResult:
        """Execute a single step.

        Args:
            step: The FlowStep to execute.

        Returns:
            StepResult with outcome.
        """
        start = time.monotonic()

        if self._executor:
            try:
                sr = await self._executor.execute_step(step)
                if isinstance(sr, StepResult):
                    sr.duration = time.monotonic() - start
                    return sr
                result_obj = cast(StepResult, sr)
                result_obj.duration = time.monotonic() - start
                return result_obj
            except Exception as e:
                return StepResult(
                    task_id=step.task_id,
                    success=False,
                    error=str(e),
                    duration=time.monotonic() - start,
                )
        else:
            # No executor — dry run mode
            logger.debug(f"[flow_engine] Dry run: {step.task_id} ({step.name})")
            return StepResult(
                task_id=step.task_id,
                success=True,
                output=f"Dry run: {step.name}",
                duration=time.monotonic() - start,
            )
