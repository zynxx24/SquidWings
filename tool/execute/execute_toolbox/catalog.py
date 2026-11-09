"""The toolbox catalog: many small, exact tools that stay hidden until a request needs them.

A cheap model does its best work when it calls a tool for anything exact (sums, dates,
units, parsing, encodings, distances) instead of guessing. Offering a hundred tool schemas
on every request would cost thousands of prompt tokens and confuse it, so these tools are
*deferred*: none is offered by default. One gets offered when

* the request's words match the tool's or its group's keywords (see ``match``),
* an awake skill names it in its ``tools:`` header,
* the model asks ``find_tools`` and the tool is unlocked for this session, or
* the model calls it by name anyway, which always works.

Every tool here is a plain function taking keyword arguments and returning a dict with
``ok``. ``spec`` wraps it so a bad argument comes back as a readable error with the
expected call, instead of a traceback that a small model cannot act on.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from tool.registry.tool_schema import ParamType, ToolParameter, ToolSchema
from utils.logger import get_logger

logger = get_logger(__name__)

_TYPES = {
    "str": ParamType.STRING,
    "int": ParamType.INTEGER,
    "num": ParamType.NUMBER,
    "bool": ParamType.BOOLEAN,
    "list": ParamType.ARRAY,
    "obj": ParamType.OBJECT,
}


@dataclass(frozen=True)
class Group:
    """A family of tools, woken together by its keywords."""

    name: str
    label: str
    keywords: tuple[str, ...]


@dataclass
class Spec:
    name: str
    description: str
    group: str
    func: Callable[..., Any]
    params: list[ToolParameter]
    keywords: tuple[str, ...] = ()
    lead: bool = False
    workspace: bool = False
    network: bool = False
    read_only: bool = True
    examples: list[str] = field(default_factory=list)

    def schema(self) -> ToolSchema:
        params = list(self.params)
        if self.workspace:
            params.append(ToolParameter(name="workspace_path", param_type=ParamType.STRING,
                                        description="Workspace root.", required=False))
        return ToolSchema(name=self.name, description=self.description, parameters=params)

    def usage(self) -> str:
        parts = []
        for p in self.params:
            parts.append(f"{p.name}: {p.param_type.value}{'' if p.required else '?'}")
        return f"{self.name}({', '.join(parts)})"


GROUPS: dict[str, Group] = {}
CATALOG: dict[str, Spec] = {}


def group(name: str, label: str, keywords: str) -> None:
    GROUPS[name] = Group(name, label, tuple(k.strip().lower() for k in keywords.split(",") if k.strip()))


def param(name: str, kind: str = "str", description: str = "", required: bool = True,
      default: Any = None, enum: list[str] | None = None, items: str | None = None) -> ToolParameter:
    """A parameter in one line. ``kind`` is str, int, num, bool, list, or obj."""
    item_schema = {"type": _TYPES[items].value} if items else ({"type": "string"} if kind == "list" else None)
    return ToolParameter(name=name, param_type=_TYPES[kind], description=description,
                         required=required, default=default, enum=enum, items=item_schema)


def spec(name: str, description: str, group_name: str, params: list[ToolParameter] | None = None, *,
         keywords: str = "", lead: bool = False, workspace: bool = False, network: bool = False,
         read_only: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a toolbox function in the catalog."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in CATALOG:
            raise ValueError(f"toolbox tool {name} defined twice")
        if group_name not in GROUPS:
            raise ValueError(f"toolbox tool {name} names unknown group {group_name}")
        entry = Spec(
            name=name, description=description, group=group_name, func=func, params=params or [],
            keywords=tuple(k.strip().lower() for k in keywords.split(",") if k.strip()),
            lead=lead, workspace=workspace, network=network, read_only=read_only,
        )
        CATALOG[name] = entry
        entry.func = _safe(entry)
        return func

    return decorator


def _safe(entry: Spec) -> Callable[..., Any]:
    """Turn every failure into ``{"ok": False, "error", "usage"}`` the model can fix."""
    func = entry.func
    accepted = set(inspect.signature(func).parameters)

    def failure(exc: Exception) -> dict[str, Any]:
        message = str(exc) or type(exc).__name__
        return {"ok": False, "error": f"{entry.name}: {message}", "usage": entry.usage()}

    def clean(kwargs: dict[str, Any]) -> dict[str, Any]:
        # Extra arguments (workspace_path on a tool that ignores it) must not raise.
        return {k: v for k, v in kwargs.items() if k in accepted}

    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def run_async(**kwargs: Any) -> dict[str, Any]:
            try:
                return _finish(await func(**clean(kwargs)))
            except Exception as exc:  # noqa: BLE001 - reported to the model, never swallowed
                logger.info(f"[toolbox] {entry.name} failed: {exc}")
                return failure(exc)
        return run_async

    @wraps(func)
    def run(**kwargs: Any) -> dict[str, Any]:
        try:
            return _finish(func(**clean(kwargs)))
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[toolbox] {entry.name} failed: {exc}")
            return failure(exc)
    return run


def _finish(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result if "ok" in result else {"ok": True, **result}
    return {"ok": True, "result": result}


# ── Matching ──────────────────────────────────────────────────────────────────
def _hits(words: tuple[str, ...], text: str) -> int:
    return sum(1 for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text))


def match(user_input: str, limit: int = 6) -> list[str]:
    """Catalog tools the request's own words call for, best first.

    A tool's own keyword decides. A group's keywords only break ties, or, when at least
    two of them appear and no tool of that group was named, wake up to two of its lead
    tools. So "hitung" does not pull in every math tool, and "bug di api" pulls in none.
    """
    text = (user_input or "").lower()
    if not text.strip():
        return []
    group_hits = {name: _hits(g.keywords, text) for name, g in GROUPS.items()}
    own = {name: _hits(entry.keywords, text) for name, entry in CATALOG.items()}
    named_groups = {CATALOG[name].group for name, hits in own.items() if hits}
    scored: list[tuple[int, str]] = []
    leads: dict[str, int] = {}
    for entry in CATALOG.values():
        hits, from_group = own[entry.name], group_hits.get(entry.group, 0)
        if hits:
            scored.append((hits * 2 + from_group, entry.name))
        elif entry.lead and from_group >= 2 and entry.group not in named_groups and leads.get(entry.group, 0) < 2:
            leads[entry.group] = leads.get(entry.group, 0) + 1
            scored.append((1, entry.name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:limit]]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1]


def search(query: str, limit: int = 8, group_name: str = "") -> list[Spec]:
    """Rank catalog tools for a free-text need. Used by find_tools.

    A word in the tool's name counts most, then its own keywords and description, and the
    group's keywords least: "cicilan pinjaman" must find loan_payment, not every money tool.
    """
    words = _words(query)
    wanted_group = group_name.strip().lower()
    scored: list[tuple[float, str]] = []
    for entry in CATALOG.values():
        if wanted_group and entry.group != wanted_group:
            continue
        name = entry.name.lower().replace("_", " ")
        own = " ".join([entry.description.lower(), " ".join(entry.keywords)])
        shared = " ".join([GROUPS[entry.group].label.lower(), " ".join(GROUPS[entry.group].keywords)])
        score = 0.0
        for w in words:
            pattern = rf"(?<![a-z0-9]){re.escape(w)}"
            if re.search(pattern, name):
                score += 3
            if re.search(pattern, own):
                score += 2
            elif re.search(pattern, shared):
                score += 0.5
        score += 2 * _hits(entry.keywords, " ".join(words))
        if not words and wanted_group:
            score = 1
        if score:
            scored.append((score, entry.name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [CATALOG[name] for _, name in scored[:limit]]


def groups_line() -> str:
    """One prompt line naming the hidden groups, so the model knows find_tools is worth calling."""
    counts: dict[str, int] = {}
    for entry in CATALOG.values():
        counts[entry.group] = counts.get(entry.group, 0) + 1
    names = ", ".join(f"{g} ({counts[g]})" for g in sorted(counts))
    return (
        f"[MORE TOOLS: {len(CATALOG)} exact tools are hidden to save tokens: {names}. "
        "For any sum, date, unit, conversion, parsing, encoding, map or place question, call "
        "find_tools(\"what you need\") and then the tool, instead of working it out by hand.]"
    )


__all__ = ["CATALOG", "GROUPS", "Spec", "group", "groups_line", "match", "param", "search", "spec"]
