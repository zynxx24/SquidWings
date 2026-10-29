"""
tool.planning.logic.constraint_checker — Check task constraints.

Verifies that required resources, models, and permissions are
available before executing a task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CheckResult:
    """Result of constraint checking.

    Attributes:
        can_execute: Whether all constraints are met.
        violations: List of constraint violations.
        warnings: Non-blocking warnings.
    """
    can_execute: bool = True
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ConstraintChecker:
    """Checks whether a task can be executed given constraints.

    Checks:
    - Resource availability (disk space, memory)
    - Model availability (is the needed model online?)
    - Rate limits (is the model rate-limited?)
    - Permissions (is the action allowed?)
    """

    def __init__(
        self,
        rate_limiter: Any = None,
        tool_registry: Any = None,
    ) -> None:
        """Initialize with optional dependencies.

        Args:
            rate_limiter: RateLimiter instance for checking rate limits.
            tool_registry: ToolRegistry for checking tool availability.
        """
        self._rate_limiter = rate_limiter
        self._tool_registry = tool_registry

    def check(
        self,
        task: dict[str, Any],
        constraints: dict[str, Any] | None = None,
    ) -> CheckResult:
        """Check all constraints for a task.

        Args:
            task: Task dict with tool_name, model requirements, etc.
            constraints: Additional constraints to check.

        Returns:
            CheckResult with pass/fail and violations.
        """
        result = CheckResult()

        # Check tool availability
        tool_name = task.get("tool_name", "")
        if (
            tool_name
            and self._tool_registry
            and not self._tool_registry.has_tool(tool_name)
        ):
            result.can_execute = False
            result.violations.append(
                f"Tool not registered: {tool_name}"
            )

        # Check rate limits
        model = task.get("model", "")
        if model and self._rate_limiter and not self._rate_limiter.is_available(model):
            result.can_execute = False
            result.violations.append(
                f"Rate limited: {model}"
            )

        # Check custom constraints
        if constraints:
            # Max tokens constraint
            max_tokens = constraints.get("max_tokens")
            estimated = task.get("estimated_tokens", 0)
            if max_tokens and estimated > max_tokens:
                result.warnings.append(
                    f"Estimated tokens ({estimated}) exceeds budget ({max_tokens})"
                )

            # Timeout constraint
            max_timeout = constraints.get("max_timeout")
            if max_timeout:
                result.warnings.append(
                    f"Task will be limited to {max_timeout}s timeout"
                )

            # Allowed directories
            allowed_dirs = constraints.get("allowed_directories", [])
            file_path = task.get("file_path", "")
            if (
                allowed_dirs
                and file_path
                and not any(file_path.startswith(d) for d in allowed_dirs)
            ):
                result.can_execute = False
                result.violations.append(
                    f"File outside allowed directories: {file_path}"
                )

        if result.violations:
            logger.warning(
                f"[constraint] Violations: {result.violations}"
            )
        elif result.warnings:
            logger.debug(f"[constraint] Warnings: {result.warnings}")

        return result
