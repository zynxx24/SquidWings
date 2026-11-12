"""The toolbox: 100+ exact, deferred tools, plus ``find_tools`` to discover and unlock them.

See ``catalog`` for how a deferred tool reaches the model. Importing this package loads
every tool module, so the catalog is complete before anything asks it.
"""

from __future__ import annotations

from typing import Any

from utils import session_settings
from utils.workspace_context import get_active_workspace

from . import (  # noqa: F401 - importing registers the tools in the catalog
    data_tools,
    datastore_tools,
    dev_tools,
    finance_id_tools,
    geo_maps_tools,
    math_tools,
    media_tools,
    office_tools,
    style_tools,
    text_encode_tools,
    trading_live_tools,
    units_time_tools,
    voice_tools,
)
from .catalog import CATALOG, GROUPS, groups_line, match, search

FIND_TOOLS = "find_tools"
# Per call, so one vague query cannot flood a small model's tool list.
_UNLOCK_PER_CALL = 5


def deferred_names() -> frozenset[str]:
    return frozenset(CATALOG)


def workspace_tools() -> dict[str, str]:
    return {name: "workspace_path" for name, entry in CATALOG.items() if entry.workspace}


def read_only_tools() -> frozenset[str]:
    return frozenset(name for name, entry in CATALOG.items() if entry.read_only and not entry.network)


async def find_tools(query: str = "", group: str = "", limit: int = 6, unlock: bool = True) -> dict[str, Any]:
    """Search the hidden toolbox and unlock the best matches for this session."""
    entries = search(query, limit=max(1, min(int(limit), 15)), group_name=group)
    if not entries and not group:
        return {"ok": True, "tools": [], "groups": {g.name: g.label for g in GROUPS.values()},
                "hint": "nothing matched; try other words (Indonesian or English) or pick a group"}
    unlocked: list[str] = []
    if unlock:
        unlocked = [e.name for e in entries[:_UNLOCK_PER_CALL]]
        workspace = get_active_workspace()
        if workspace:
            session_settings.unlock_tools(workspace, unlocked)
    return {
        "ok": True,
        "tools": [{"name": e.name, "description": e.description, "call": e.usage(),
                   "needs_internet": e.network} for e in entries],
        "unlocked": unlocked,
        "next": "call one of these tools directly with the arguments shown in 'call'",
    }


def register(registry: Any) -> int:
    """Register every toolbox tool and find_tools. Returns how many were added."""
    from tool.registry.tool_registry import WORKSPACE_TOOLS, mark_read_only
    from tool.registry.tool_schema import ParamType, ToolParameter, ToolSchema

    for entry in CATALOG.values():
        registry.register(entry.schema(), entry.func)
    WORKSPACE_TOOLS.update(workspace_tools())
    mark_read_only(read_only_tools() | {FIND_TOOLS})
    registry.register(
        ToolSchema(
            name=FIND_TOOLS,
            description=(
                f"Search {len(CATALOG)} hidden exact tools and unlock the best ones for this chat: maths, statistics, "
                "units, dates and time zones, text, encodings, tables (CSV/Excel/JSON/SQLite), developer helpers, money "
                "and loans, Indonesian formats (terbilang, Rupiah, phone numbers), health numbers, Google Maps places and "
                "distances, trading risk, and live weather, exchange rates and holidays. Call it whenever a precise "
                "answer is needed and no offered tool fits."
            ),
            parameters=[
                ToolParameter(name="query", param_type=ParamType.STRING,
                              description="What you need, in any language, e.g. 'loan installment' or 'jarak dua kota'.",
                              required=False, default=""),
                ToolParameter(name="group", param_type=ParamType.STRING, description="Optional group to list.",
                              required=False, enum=sorted(GROUPS)),
                ToolParameter(name="limit", param_type=ParamType.INTEGER, description="Max tools (default 6).",
                              required=False, default=6),
            ],
        ),
        find_tools,
    )
    return len(CATALOG) + 1


__all__ = ["CATALOG", "FIND_TOOLS", "GROUPS", "deferred_names", "find_tools", "groups_line", "match", "register", "search"]
