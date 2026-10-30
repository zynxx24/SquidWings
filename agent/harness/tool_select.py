"""Choose which tool schemas to offer for a request."""

from __future__ import annotations

from typing import Any

from config.constants import TaskType
from tool.registry.tool_registry import WORKSPACE_TOOLS

from .tiers import Scaffold

# Most important first: the list is cut to the tier's limit.
_BY_TASK: dict[TaskType, list[str]] = {
    TaskType.CODE: [
        "read_file", "file_patch", "file_write", "file_search", "execute_command", "run_checks",
        "find_symbol", "code_outline", "project_overview", "code_graph", "aislop_scan",
        "list_directory", "web_research", "web_read", "load_skill", "git_diff", "vault_recall",
    ],
    TaskType.ACTIVITY: [
        "web_research", "web_read", "read_file", "file_write", "file_patch", "file_search",
        "execute_command", "list_directory", "time_now", "vault_recall", "vault_write", "load_skill",
    ],
    TaskType.SCHOOL_TASK: ["web_research", "web_read", "read_file", "file_write", "vault_recall", "time_now"],
    # Small models make arithmetic slips; running the sum in Python avoids them.
    TaskType.MATH: ["execute_command"],
    TaskType.REASON: ["web_research", "web_read", "read_file", "vault_recall", "load_skill"],
    TaskType.MULTIMODAL: ["read_file", "web_read"],
    TaskType.GENERATE_IMG: [],
}

# How many of the task's tools always make the cut. A mention of "memory" must
# add memory tools without pushing out the ability to run anything.
_CORE_TOOLS = 8

# Extra tools pulled in only when the request talks about them.
_ON_DEMAND: dict[tuple[str, ...], list[str]] = {
    ("vault", "obsidian", "catatan", "note", "ingat", "remember", "memory", "memori", "lupa", "forget"): [
        "memory_remember", "memory_forget", "memory_recall", "vault_search", "vault_read", "vault_write", "vault_recall",
    ],
    ("graph", "graphify", "relasi", "hubungan"): [
        "vault_graph_build", "vault_neighbours", "vault_explain", "graphify_query", "graphify_ingest",
    ],
    ("git", "commit", "diff", "branch"): ["git_status", "git_diff"],
    ("hapus", "delete", "pindah", "rename", "move", "folder", "direktori"): [
        "file_move", "file_delete", "dir_create",
    ],
    ("json",): ["json_query"],
}


def _for_model(schema: dict[str, Any]) -> dict[str, Any]:
    """Hide workspace_path. The loop fills it in, and a model that could set it
    could also point a tool outside the session workspace."""
    function = schema.get("function", {})
    parameters = function.get("parameters") or {}
    properties = parameters.get("properties") or {}
    hidden = WORKSPACE_TOOLS.get(function.get("name", ""), "workspace_path")
    if hidden not in properties:
        return schema
    trimmed = {k: v for k, v in properties.items() if k != hidden}
    return {**schema, "function": {**function, "parameters": {**parameters, "properties": trimmed}}}


def select_tools(
    schemas: list[dict[str, Any]],
    task_type: TaskType,
    user_input: str,
    scaffold: Scaffold,
    disabled: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Return the schemas to offer, most relevant first, capped for the tier."""
    by_name = {s.get("function", {}).get("name", ""): s for s in schemas}
    by_name = {name: s for name, s in by_name.items() if name and name not in disabled}

    if task_type == TaskType.GENERATE_IMG:
        return []
    if scaffold.max_tools is None:
        return [_for_model(s) for s in by_name.values()]

    lowered = user_input.lower()
    base = _BY_TASK.get(task_type, _BY_TASK[TaskType.ACTIVITY])
    # A tool the user names is always offered, then the task's core tools, then
    # whatever the request's topic pulls in, then the task's remaining tools.
    wanted = [name for name in by_name if name in lowered]
    wanted.extend(base[:_CORE_TOOLS])
    for keywords, names in _ON_DEMAND.items():
        if any(k in lowered for k in keywords):
            wanted.extend(names)
    wanted.extend(base[_CORE_TOOLS:])

    # dict.fromkeys de-duplicates while keeping the priority order above.
    names = [n for n in dict.fromkeys(wanted) if n in by_name][: scaffold.max_tools]
    # Tools sit ahead of the prompt in provider caches, so a stable order keeps
    # the same set from invalidating the cached prefix.
    return [_for_model(by_name[n]) for n in sorted(names)]


def text_tool_protocol(schemas: list[dict[str, Any]]) -> str:
    """How to call tools for a model without native tool calling.

    Many cheap models have no tool support in their API. The loop reads calls from
    their text instead, so they can still do real work.
    """
    if not schemas:
        return ""
    lines = [
        "\n\n[TOOLS] To use a tool, write a block like this and stop. The result comes back in the next message.",
        '```tool:read_file\n{"file_path": "app.py"}\n```',
    ]
    for schema in schemas:
        function = schema.get("function", {})
        parameters = ", ".join((function.get("parameters") or {}).get("properties", {}))
        summary = str(function.get("description", "")).split(". ")[0][:100]
        lines.append(f"- {function.get('name')}({parameters}): {summary}")
    return "\n".join(lines)
