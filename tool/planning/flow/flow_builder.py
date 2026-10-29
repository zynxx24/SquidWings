"""
tool.planning.flow.flow_builder — Build execution DAGs from tasks.

Constructs a directed acyclic graph (DAG) of tasks with their
dependencies, then validates and topologically sorts them.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FlowStep:
    """A single step in the execution flow.

    Attributes:
        task_id: Unique identifier for this step.
        name: Human-readable name.
        description: What this step does.
        tool_name: Tool to execute (e.g., "execute_command").
        tool_args: Arguments for the tool.
        depends_on: List of task_ids this step depends on.
        priority: Execution priority (higher = sooner).
    """
    task_id: str
    name: str
    description: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    priority: int = 0


@dataclass
class ExecutionFlow:
    """A complete execution flow (DAG).

    Attributes:
        name: Flow name.
        steps: All steps in the flow.
        execution_order: Topologically sorted step IDs.
        parallel_groups: Groups of steps that can run in parallel.
    """
    name: str = ""
    steps: list[FlowStep] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)
    parallel_groups: list[list[str]] = field(default_factory=list)

    def get_step(self, task_id: str) -> FlowStep | None:
        """Get a step by task_id."""
        for s in self.steps:
            if s.task_id == task_id:
                return s
        return None

    @property
    def total_steps(self) -> int:
        return len(self.steps)


class FlowBuilder:
    """Builds an ExecutionFlow (DAG) from a list of tasks.

    Validates that the graph is a DAG (no cycles), then produces
    a topological ordering with parallel execution groups.
    """

    def build(
        self,
        steps: list[FlowStep],
        flow_name: str = "unnamed_flow",
    ) -> ExecutionFlow:
        """Build an execution flow from steps.

        Args:
            steps: List of FlowStep definitions.
            flow_name: Name for the flow.

        Returns:
            ExecutionFlow with topological ordering.

        Raises:
            ValueError: If the graph contains cycles.
        """
        # Build adjacency and in-degree maps
        graph: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = {}
        step_map: dict[str, FlowStep] = {}

        for step in steps:
            step_map[step.task_id] = step
            if step.task_id not in in_degree:
                in_degree[step.task_id] = 0

            for dep in step.depends_on:
                graph[dep].append(step.task_id)
                in_degree[step.task_id] = in_degree.get(step.task_id, 0) + 1
                if dep not in in_degree:
                    in_degree[dep] = 0

        # Topological sort (Kahn's algorithm)
        order: list[str] = []
        parallel_groups: list[list[str]] = []

        queue = deque(
            tid for tid, deg in in_degree.items()
            if deg == 0 and tid in step_map
        )

        while queue:
            # All items currently in the queue can run in parallel
            group = sorted(queue)  # Sort for determinism
            parallel_groups.append(group)
            next_queue: deque[str] = deque()

            for tid in group:
                order.append(tid)
                for neighbor in graph[tid]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0 and neighbor in step_map:
                        next_queue.append(neighbor)

            queue = next_queue

        # Check for cycles
        if len(order) != len(step_map):
            processed = set(order)
            remaining = [tid for tid in step_map if tid not in processed]
            raise ValueError(
                f"Cycle detected in flow '{flow_name}'. "
                f"Unresolvable tasks: {remaining}"
            )

        flow = ExecutionFlow(
            name=flow_name,
            steps=steps,
            execution_order=order,
            parallel_groups=parallel_groups,
        )

        logger.info(
            f"[flow_builder] Built '{flow_name}': "
            f"{len(steps)} steps, {len(parallel_groups)} parallel groups"
        )

        return flow
