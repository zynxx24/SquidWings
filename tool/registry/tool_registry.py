"""
tool.registry.tool_registry — Central tool registration and execution.

Singleton registry that maps tool names to their schemas and
executor functions. Supports both decorator-based and manual
registration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from security.guard import GuardError
from tool.registry.tool_schema import ToolSchema
from utils.exceptions import ToolError
from utils.logger import get_logger

logger = get_logger(__name__)

# Type for tool executor functions
ToolExecutor = Callable[..., Any] | Callable[..., Awaitable[Any]]

# Models frequently invent near-miss tool names ("shell", "bash", "read").
# Resolving them here keeps one wasted iteration from killing a whole turn.
_TOOL_ALIASES: dict[str, str] = {
    "shell": "execute_command",
    "bash": "execute_command",
    "sh": "execute_command",
    "terminal": "execute_command",
    "run_command": "execute_command",
    "run_shell": "execute_command",
    "cmd": "execute_command",
    "read": "read_file",
    "cat": "read_file",
    "open_file": "read_file",
    "view_file": "read_file",
    "list_dir": "list_directory",
    "ls": "list_directory",
    "dir_list": "list_directory",
    "grep": "file_search",
    "search_file": "file_search",
    "search_files": "file_search",
    "find": "file_search",
    "write": "file_write",
    "write_file": "file_write",
    "create_file": "file_write",
    "edit": "file_patch",
    "patch": "file_patch",
    "apply_patch": "file_patch",
    "rename": "file_move",
    "mv": "file_move",
    "rm": "file_delete",
    "delete_file": "file_delete",
    "stat": "file_stat",
    "mkdir": "dir_create",
    "websearch": "web_search",
}


class _ToolEntry:
    """Internal entry for a registered tool."""
    __slots__ = ("schema", "executor", "is_async")

    def __init__(self, schema: ToolSchema, executor: ToolExecutor) -> None:
        self.schema = schema
        self.executor = executor
        self.is_async = asyncio.iscoroutinefunction(executor)


class ToolRegistry:
    """Central registry for all agent tools.

    Singleton pattern — all parts of the system share the same
    registry instance.

    Usage (decorator):
        registry = ToolRegistry()

        @registry.tool(name="execute_command", description="Run a shell command")
        async def execute_command(command: str, cwd: str = ".") -> dict:
            ...

    Usage (manual):
        schema = ToolSchema(name="edit_file", description="Edit a file", ...)
        registry.register(schema, my_executor_fn)
    """

    _instance: ToolRegistry | None = None
    _tools: dict[str, Any]
    _initialized: bool

    def __new__(cls) -> ToolRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools = {}
            cls._instance._initialized = True
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_tools"):
            self._tools: dict[str, _ToolEntry] = {}

    def register(self, schema: ToolSchema, executor: ToolExecutor) -> None:
        """Register a tool with its schema and executor.

        Args:
            schema: The tool's schema definition.
            executor: The callable that executes the tool.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if schema.name in self._tools:
            logger.warning(
                f"[tool_registry] Overwriting existing tool: {schema.name}"
            )

        self._tools[schema.name] = _ToolEntry(schema, executor)
        logger.info(f"[tool_registry] Registered tool: {schema.name}")

    def tool(
        self,
        name: str,
        description: str,
        parameters: list[Any] | None = None,
    ) -> Callable:
        """Decorator for registering a tool.

        Args:
            name: Tool name.
            description: Tool description.
            parameters: Optional list of ToolParameter.

        Returns:
            Decorator that registers the function.
        """
        def decorator(func: ToolExecutor) -> ToolExecutor:
            schema = ToolSchema(
                name=name,
                description=description,
                parameters=parameters or [],
            )
            self.register(schema, func)
            return func
        return decorator

    def get(self, tool_name: str) -> _ToolEntry | None:
        """Get a registered tool entry by name.

        Args:
            tool_name: Name of the tool.

        Returns:
            _ToolEntry or None if not found.
        """
        return self._tools.get(tool_name)

    def get_schema(self, tool_name: str) -> ToolSchema | None:
        """Get the schema for a tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            ToolSchema or None.
        """
        entry = self._tools.get(tool_name)
        return entry.schema if entry else None

    def get_all_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas in OpenAI function-calling format, deterministically sorted.

        Returns:
            list: OpenAI-compatible tool definitions sorted alphabetically by name.
        """
        schemas = [
            entry.schema.to_openai_schema()
            for entry in self._tools.values()
        ]
        return sorted(schemas, key=lambda s: s.get("function", {}).get("name", ""))

    def get_all_names(self) -> list[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())

    def resolve_name(self, tool_name: str) -> str:
        """Map a model-proposed tool name onto a registered tool.

        Handles exact matches, provider prefixes (``functions.read_file``,
        ``tool:read_file``), case differences, and a small alias table for
        common inventions such as ``shell`` or ``bash``.
        """
        name = (tool_name or "").strip()
        if name in self._tools:
            return name

        bare = name.lower()
        for sep in ("::", "."):
            if sep in bare:
                bare = bare.rsplit(sep, 1)[-1]
        bare = bare.strip("/: ").replace("-", "_")

        for candidate in (bare, _TOOL_ALIASES.get(bare, "")):
            if candidate and candidate in self._tools:
                return candidate
        return name

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a registered tool.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool call arguments.

        Returns:
            Tool execution result.

        Raises:
            ToolError: If tool is not found or execution fails.
        """
        resolved = self.resolve_name(tool_name)
        if resolved != tool_name:
            logger.info(f"[tool_registry] Resolved tool name: {tool_name} -> {resolved}")
            tool_name = resolved

        entry = self._tools.get(tool_name)
        if not entry:
            import difflib

            close = difflib.get_close_matches(tool_name, list(self._tools), n=3, cutoff=0.5)
            hint = f" Available tools: {', '.join(close)}" if close else ""
            raise ToolError(
                message=f"Unknown tool: {tool_name}.{hint}",
                tool_name=tool_name,
            )

        logger.info(
            f"[tool_registry] Executing: {tool_name} "
            f"(args={list(arguments.keys())})"
        )

        guard = self._get_guard()
        try:
            if guard is None:
                # Security layer unavailable -> fail open, but say so loudly.
                logger.warning(
                    "[tool_registry] security guard unavailable; "
                    f"executing {tool_name} unguarded"
                )
                if entry.is_async:
                    return await entry.executor(**arguments)
                return entry.executor(**arguments)

            # Evaluate the call without running it: the registry owns
            # execution (and may need to await an async executor), so
            # Guard.execute() -- which calls func() synchronously -- is
            # not usable here.
            verdict = guard.check(tool_name, arguments)
            verdict.raise_if_blocked(tool_name)

            if entry.is_async:
                result = await entry.executor(**arguments)
            else:
                result = entry.executor(**arguments)
            return guard.scrub(result)

        except ToolError:
            raise
        except GuardError as e:
            # Policy/jail/shield denial -> surface as a denied ToolError.
            raise ToolError(
                message=f"Tool '{tool_name}' denied: {e.reason}",
                tool_name=tool_name,
                original_error=e,
            ) from e
        except Exception as e:
            raise ToolError(
                message=f"Tool '{tool_name}' failed: {e}",
                tool_name=tool_name,
                original_error=e,
            ) from e

    @staticmethod
    def _get_guard() -> Any | None:
        """Return the process-wide Guard facade, or None if disabled/broken.

        Imported lazily so the registry stays usable in environments where
        the security package is not installed (e.g. minimal test harnesses).
        """
        try:
            from security.guard import get_guard_facade

            return get_guard_facade()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"[tool_registry] guard init failed: {exc}")
            return None

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is registered (aliases and prefixes included)."""
        return self.resolve_name(tool_name) in self._tools

    @property
    def tool_count(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    def reset(self) -> None:
        """Clear all registered tools (mainly for testing)."""
        self._tools.clear()
        logger.debug("[tool_registry] All tools cleared")

    @classmethod
    def reset_singleton(cls) -> None:
        """Reset the singleton instance (for testing)."""
        cls._instance = None
