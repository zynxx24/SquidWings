"""
tool.planning.flow.flow_visualizer — Visualize execution flows.

Generates ASCII art and Mermaid diagrams of flow DAGs.
"""

from __future__ import annotations

from tool.planning.flow.flow_builder import ExecutionFlow
from utils.logger import get_logger

logger = get_logger(__name__)


class FlowVisualizer:
    """Visualizes execution flow DAGs."""

    @staticmethod
    def to_mermaid(flow: ExecutionFlow) -> str:
        """Generate a Mermaid flowchart diagram.

        Args:
            flow: The ExecutionFlow to visualize.

        Returns:
            Mermaid diagram string.
        """
        lines = ["flowchart TD"]
        lines.append(f'    START(["▶ {flow.name}"])')

        # Add nodes
        for step in flow.steps:
            label = step.name.replace('"', "'")
            lines.append(f'    {step.task_id}["{label}"]')

        # Add dependency edges
        roots: list[str] = []
        for step in flow.steps:
            if step.depends_on:
                for dep in step.depends_on:
                    lines.append(f"    {dep} --> {step.task_id}")
            else:
                roots.append(step.task_id)

        # Connect START to roots
        for root in roots:
            lines.append(f"    START --> {root}")

        # Connect leaves to END
        has_dependents = set()
        for step in flow.steps:
            for dep in step.depends_on:
                has_dependents.add(dep)

        leaves = [
            s.task_id for s in flow.steps
            if s.task_id not in has_dependents
        ]

        lines.append('    END(["✅ Done"])')
        for leaf in leaves:
            lines.append(f"    {leaf} --> END")

        return "\n".join(lines)

    @staticmethod
    def to_ascii(flow: ExecutionFlow) -> str:
        """Generate an ASCII art representation.

        Args:
            flow: The ExecutionFlow to visualize.

        Returns:
            ASCII art string.
        """
        lines: list[str] = []
        lines.append(f"╔══ Flow: {flow.name} ══╗")
        lines.append(f"║ Steps: {flow.total_steps}")
        lines.append(f"║ Groups: {len(flow.parallel_groups)}")
        lines.append("╠══════════════════════╣")

        for gi, group in enumerate(flow.parallel_groups):
            lines.append(f"║ Group {gi + 1}:")
            for tid in group:
                step = flow.get_step(tid)
                name = step.name if step else tid
                deps = step.depends_on if step else []
                dep_str = f" ← [{', '.join(deps)}]" if deps else ""
                lines.append(f"║   ├─ {name}{dep_str}")

        lines.append("╚══════════════════════╝")
        return "\n".join(lines)

    @staticmethod
    def to_summary(flow: ExecutionFlow) -> str:
        """Generate a short text summary.

        Args:
            flow: The ExecutionFlow to summarize.

        Returns:
            Summary string.
        """
        parts = [f"Flow: {flow.name}"]
        parts.append(f"Total steps: {flow.total_steps}")
        parts.append(f"Parallel groups: {len(flow.parallel_groups)}")

        for gi, group in enumerate(flow.parallel_groups):
            steps_in_group = [
                flow.get_step(tid) for tid in group
            ]
            names = [s.name for s in steps_in_group if s]
            parts.append(f"  Group {gi + 1}: {', '.join(names)}")

        return "\n".join(parts)
