"""
tool.planning.logic.dependency_resolver — Resolve task dependencies.

Topological sort with cycle detection for task ordering.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ResolvedOrder:
    """Result of dependency resolution.

    Attributes:
        order: Task names/IDs in execution order.
        has_cycles: Whether circular dependencies were found.
        cycle_members: Tasks involved in cycles (if any).
        parallel_groups: Groups of tasks that can run in parallel.
    """
    order: list[str]
    has_cycles: bool = False
    cycle_members: list[str] | None = None
    parallel_groups: list[list[str]] | None = None


class DependencyResolver:
    """Resolves task dependencies into execution order.

    Uses Kahn's algorithm for topological sorting.
    Detects circular dependencies.
    """

    def resolve(
        self,
        tasks: list[dict[str, Any]],
        id_key: str = "name",
        deps_key: str = "depends_on",
    ) -> ResolvedOrder:
        """Resolve task dependencies into execution order.

        Args:
            tasks: List of task dicts, each with an ID and dependency list.
            id_key: Key for the task identifier.
            deps_key: Key for the dependency list.

        Returns:
            ResolvedOrder with execution order and cycle info.
        """
        graph: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = {}
        all_ids: set[str] = set()

        for task in tasks:
            tid = task[id_key]
            all_ids.add(tid)
            deps = task.get(deps_key, []) or []
            in_degree.setdefault(tid, 0)

            for dep in deps:
                graph[dep].append(tid)
                in_degree[tid] = in_degree.get(tid, 0) + 1
                all_ids.add(dep)
                in_degree.setdefault(dep, 0)

        # Kahn's algorithm
        queue = deque(tid for tid in all_ids if in_degree.get(tid, 0) == 0)
        order: list[str] = []
        parallel_groups: list[list[str]] = []

        while queue:
            group = sorted(queue)
            parallel_groups.append(group)
            next_queue: deque[str] = deque()

            for tid in group:
                order.append(tid)
                for neighbor in graph[tid]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_queue.append(neighbor)

            queue = next_queue

        has_cycles = len(order) != len(all_ids)
        cycle_members = None
        if has_cycles:
            cycle_members = [tid for tid in all_ids if tid not in set(order)]
            logger.warning(f"[dep_resolver] Cycles detected: {cycle_members}")

        result = ResolvedOrder(
            order=order,
            has_cycles=has_cycles,
            cycle_members=cycle_members,
            parallel_groups=parallel_groups,
        )

        logger.debug(
            f"[dep_resolver] Resolved: {len(order)} tasks, "
            f"{len(parallel_groups)} groups, cycles={has_cycles}"
        )

        return result
