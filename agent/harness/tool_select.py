"""Choose which tool schemas to offer for a request."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from brain import skills
from config.constants import TaskType
from tool.execute import execute_toolbox
from tool.registry.tool_registry import WORKSPACE_TOOLS
from utils import session_settings
from utils.workspace_context import get_active_workspace

from .tiers import Scaffold

if TYPE_CHECKING:
    from .runtime import RunLimits

# Most important first: the list is cut to the tier's limit.
_BY_TASK: dict[TaskType, list[str]] = {
    TaskType.CODE: [
        "read_file", "file_patch", "file_write", "file_search", "execute_command", "run_checks",
        "find_symbol", "code_outline", "project_overview", "code_graph", "aislop_scan",
        "list_directory", "web_research", "web_read", "load_skill", "git_diff", "vault_recall",
    ],
    TaskType.ACTIVITY: [
        "web_research", "web_read", "read_file", "file_write", "file_patch", "file_search",
        "execute_command", "list_directory", "time_now", "vault_recall", "vault_write", "load_skill", "extract_files",
    ],
    TaskType.SCHOOL_TASK: ["web_research", "web_read", "read_file", "file_write", "vault_recall", "time_now", "extract_files", "calc"],
    # Small models make arithmetic slips; an exact tool avoids them.
    TaskType.MATH: ["calc", "execute_command", "solve_equation", "stats_describe", "unit_convert", "fraction"],
    TaskType.REASON: ["web_research", "web_read", "read_file", "vault_recall", "load_skill"],
    TaskType.MULTIMODAL: ["read_file", "web_read"],
    # No image model here: a picture is drawn in code (SVG, HTML or canvas), rendered, and shared.
    TaskType.GENERATE_IMG: [
        "style_pick", "file_write", "execute_command", "share_file", "read_file", "load_skill", "file_patch",
        "media_convert", "web_research", "list_directory",
    ],
}

# How many of the task's tools always make the cut. A mention of "memory" must
# add memory tools without pushing out the ability to run anything.
_CORE_TOOLS = 8
# Hidden toolbox tools a request's words may pull in, at most.
_MATCHED_TOOLS = 5

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
        "file_move", "file_delete", "dir_create", "file_copy",
    ],
    ("cari file", "find file", "temukan file", "file mana", "which file", "where is", "di mana file", "glob",
     "locate", "semua file", "all files", "file terbaru", "newest", "latest file", "terakhir dibuat"): ["file_find"],
    ("baca semua", "read all", "baca file", "read files", "read the files", "beberapa file", "multiple files",
     "several files", "files"): ["file_read_many"],
    ("copy", "salin", "duplikat", "duplicate", "cadangkan", "backup"): ["file_copy"],
    ("ganti semua", "replace all", "replace", "cari dan ganti", "find and replace", "search and replace",
     "ganti nama", "rename all", "refactor", "di semua file", "across files"): ["file_replace_all"],
    ("zip", "unzip", "arsip", "archive", "kompres", "compress", "ekstrak", "extract"): ["archive"],
    ("ffmpeg", "konversi", "convert", "gif", "webp", "webm", "mp4", "video", "poster", "thumbnail",
     "animasi", "animation"): ["media_convert"],
    ("trading", "trade", "trader", "mt4", "mt5", "metatrader", "forex", "xauusd", "eurusd", "gbpusd", "usdjpy",
     "stop loss", "take profit", "sinyal trading", "trading signal", "scalp", "scalping", "backtest",
     "akun trading", "ekuitas", "open posisi", "posisi trading", "floating"): ["trading_snapshot"],
    ("json",): ["json_query"],
    ("pdf", "dokumen", "ekstrak", "extract", "docx", "word", "excel", "pptx", "gambar", "file", "folder"): ["extract_files"],
    ("kirim file", "kirimkan file", "kirim ke chat", "kirimkan", "lampirkan", "attach", "send file", "bagikan", "share",
     "download", "unduh", "mp4", "render", "excel", "csv", "pdf", "gambar", "foto", "laporan"): ["share_file"],
}
# A tool that makes a file is only half the job if the file cannot reach the chat.
_MAKES_FILES = frozenset({"file_write", "execute_command", "media_convert", "archive", "file_patch"})
_SHARE = "share_file"


def _keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern[str]:
    """A keyword must start a word: "file" is in "filenya" but not in "profile", "git" not in "digital"."""
    alternatives = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9_])(?:{alternatives})")


# Compiled once: one regex per group instead of a substring scan per keyword per request.
_ON_DEMAND_RE: tuple[tuple[re.Pattern[str], list[str]], ...] = tuple(
    (_keyword_pattern(keywords), names) for keywords, names in _ON_DEMAND.items()
)


@lru_cache(maxsize=512)
def _whole_word(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])")


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


def _session() -> tuple[list[str], list[str]]:
    """(awake skills, unlocked toolbox tools) of the active session."""
    workspace = get_active_workspace()
    if not workspace:
        return [], []
    settings = session_settings.load(workspace)
    return list(settings.skills), list(settings.tools)


def _skill_tools(user_input: str, active: list[str], task: str) -> list[str]:
    """Tools an awake contextual skill needs, in this session or woken by this request, and the tools
    of the built-in companions (aislop_scan for a web page or a video made in code)."""
    awake = [*active, *skills.woken(user_input, task)]
    picked = [s.name for s in skills.select(user_input, active=awake, task=task)]
    return list(dict.fromkeys(skills.tools_for([*awake, *picked])))


def _named(by_name: dict[str, Any], lowered: str, deferred: frozenset[str]) -> list[str]:
    """Tools the user names. Core names may appear inside words; a hidden tool needs its whole name."""
    named = []
    for name in by_name:
        if name in deferred:
            if _whole_word(name).search(lowered):
                named.append(name)
        elif name in lowered:
            named.append(name)
    return named


def select_tools(
    schemas: list[dict[str, Any]],
    task_type: TaskType,
    user_input: str,
    scaffold: Scaffold,
    disabled: frozenset[str] = frozenset(),
    limits: RunLimits | None = None,
) -> list[dict[str, Any]]:
    """Return the schemas to offer, most relevant first, capped for the tier.

    The hidden toolbox never rides along by default, not even for a frontier model:
    only the tools this request, an awake skill, or an earlier find_tools call asks for.
    """
    by_name = {s.get("function", {}).get("name", ""): s for s in schemas}
    by_name = {name: s for name, s in by_name.items() if name and name not in disabled}
    if limits is not None and limits.only_tools is not None:
        # A run held to a short list gets exactly that list: no skills, no toolbox search, nothing it asks for.
        return [_for_model(by_name[n]) for n in sorted(limits.only_tools) if n in by_name]
    extra = list(limits.extra_tools) if limits is not None else []

    lowered = user_input.lower()
    deferred = execute_toolbox.deferred_names()
    active, unlocked = _session()
    named = _named(by_name, lowered, deferred)
    for_skills = _skill_tools(user_input, active, task_type.value)
    matched = execute_toolbox.match(user_input, limit=_MATCHED_TOOLS)

    if scaffold.max_tools is None:
        asked = {*named, *extra, *for_skills, *unlocked, *matched}
        names = [n for n in by_name if n not in deferred or n in asked]
        return [_for_model(by_name[n]) for n in sorted(names)]

    base = _BY_TASK.get(task_type, _BY_TASK[TaskType.ACTIVITY])
    # A tool the user names is always offered, then what an awake skill needs, then
    # the task's core tools and find_tools, then what the request's words pull in,
    # then what this chat unlocked earlier, then the task's remaining tools.
    wanted = [*named, *extra, *for_skills, *base[:_CORE_TOOLS], execute_toolbox.FIND_TOOLS, *matched]
    for pattern, names in _ON_DEMAND_RE:
        if pattern.search(lowered):
            wanted.extend(names)
    wanted.extend(reversed(unlocked))
    wanted.extend(base[_CORE_TOOLS:])

    # dict.fromkeys de-duplicates while keeping the priority order above.
    names = [n for n in dict.fromkeys(wanted) if n in by_name][: scaffold.max_tools]
    if _SHARE in by_name and _SHARE not in names and _MAKES_FILES & set(names):
        # The last name is the least wanted one, so it makes way when the tier is full.
        names = [*names[: scaffold.max_tools - 1], _SHARE] if len(names) >= scaffold.max_tools else [*names, _SHARE]
    # Tools sit ahead of the prompt in provider caches, so a stable order keeps
    # the same set from invalidating the cached prefix.
    return [_for_model(by_name[n]) for n in sorted(names)]


def toolbox_hint(tools: list[dict[str, Any]]) -> str:
    """One prompt line about the hidden tools, when find_tools is on offer."""
    if not any(t.get("function", {}).get("name") == execute_toolbox.FIND_TOOLS for t in tools):
        return ""
    return "\n\n" + execute_toolbox.groups_line()


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
