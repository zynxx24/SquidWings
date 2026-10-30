"""L1 boot context.

The small context that sits at the top of every session: the boot file
(``CLAUDE.md``), current hubs, open questions, recent decisions and
sessions, plus health warnings. Target is roughly 1-2k tokens. Everything
else is loaded on demand through L2 (graph) and L3 (notes).

L1 is rebuilt from the vault every time and never hand-maintained, so it
cannot drift from the notes it summarizes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault import graph as G
from tool.execute.execute_local_memory.vault.index import VaultIndex
from tool.execute.execute_local_memory.vault.schema import NoteRecord
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TOKEN_BUDGET = 1200
_CHARS_PER_TOKEN = 4


def _find_boot_file(vault: Path) -> Path | None:
    for name in C.BOOT_FILENAMES:
        candidate = vault / name
        if candidate.is_file():
            return candidate
    return None


def _read_boot_file(vault: Path, max_chars: int) -> tuple[str, str]:
    """Return (filename, trimmed content). A missing boot file is not an error."""
    path = _find_boot_file(vault)
    if path is None:
        return "", ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        logger.warning("boot file unreadable: %s", exc)
        return path.name, ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n...[trimmed]"
    return path.name, text


def _by_type(
    records: dict[str, NoteRecord], note_type: str, limit: int
) -> list[NoteRecord]:
    items = [
        r for r in records.values()
        if r.note_type == note_type and r.is_current
    ]
    items.sort(key=lambda r: r.mtime, reverse=True)
    return items[:limit]


def build(
    vault: str | Path,
    *,
    index: VaultIndex | None = None,
    graph: dict[str, Any] | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    question_limit: int = 5,
    session_limit: int = 3,
    decision_limit: int = 3,
    concept_limit: int = 8,
) -> dict[str, Any]:
    """Assemble the L1 context. Never raises on a missing or empty vault."""
    vault_path = Path(vault).expanduser()
    budget_chars = max(400, token_budget) * _CHARS_PER_TOKEN

    if index is None:
        index = VaultIndex(vault_path)
    try:
        records = index.refresh()
    except (FileNotFoundError, NotADirectoryError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "vault": str(vault_path),
            "text": "",
            "token_estimate": 0,
        }

    if graph is None:
        graph = G.load(vault_path)

    boot_name, boot_text = _read_boot_file(vault_path, budget_chars // 2)
    health = (graph or {}).get("health", {})

    questions = _by_type(records, C.TYPE_QUESTION, question_limit)
    decisions = _by_type(records, C.TYPE_DECISION, decision_limit)
    sessions = _by_type(records, C.TYPE_SESSION, session_limit)
    # Concepts are the bulk of a knowledge vault. Without them L1 renders as
    # an empty wrapper and the agent gets no memory at all.
    concepts = _by_type(records, C.TYPE_CONCEPT, concept_limit)

    context: dict[str, Any] = {
        "ok": True,
        "vault": str(vault_path),
        "generated_at": time.time(),
        "boot_file": boot_name,
        "boot_text": boot_text,
        "hubs": (graph or {}).get("hubs", [])[:8],
        "open_questions": [
            {"path": r.path, "title": r.title, "summary": r.summary[:160]}
            for r in questions
        ],
        "recent_decisions": [
            {"path": r.path, "title": r.title, "summary": r.summary[:160]}
            for r in decisions
        ],
        "recent_sessions": [
            {"path": r.path, "title": r.title, "updated": r.updated}
            for r in sessions
        ],
        "concepts": [
            {"path": r.path, "title": r.title, "summary": r.summary[:120]}
            for r in concepts
        ],
        "health": {
            "node_count": health.get("node_count", len(records)),
            "edge_count": health.get("edge_count", 0),
            "orphan_count": len(health.get("orphans", [])),
            "stale_count": len(health.get("stale", [])),
            "review_count": len(health.get("needs_review", [])),
            "dangling_count": len(health.get("dangling", [])),
        },
        "graph_available": graph is not None,
    }

    text = render(context, token_budget=token_budget)
    context["text"] = text
    context["token_estimate"] = max(1, len(text) // _CHARS_PER_TOKEN)
    return context


def render(context: dict[str, Any], *, token_budget: int = DEFAULT_TOKEN_BUDGET) -> str:
    """Render the L1 context as compact text for the prompt."""
    if not context.get("ok"):
        return f"[BOOT] unavailable: {context.get('error', 'unknown')}"

    lines: list[str] = ["[BOOT CONTEXT]"]

    if context.get("boot_text"):
        lines.append(f"## {context['boot_file']}")
        lines.append(context["boot_text"])
        lines.append("")

    hubs = context.get("hubs") or []
    if hubs:
        lines.append("## Hubs")
        for hub in hubs:
            lines.append(f"- {hub['title']} ({hub['degree']}) - {hub['path']}")
        lines.append("")

    for key, title in (
        ("open_questions", "Open questions"),
        ("recent_decisions", "Recent decisions"),
        ("concepts", "Concepts"),
        ("recent_sessions", "Recent sessions"),
    ):
        items = context.get(key) or []
        if not items:
            continue
        lines.append(f"## {title}")
        for item in items:
            lines.append(f"- {item['title']} - {item['path']}")
        lines.append("")

    health = context.get("health") or {}
    flags = [
        f"{label}={health[key]}"
        for key, label in (
            ("orphan_count", "orphans"),
            ("stale_count", "stale"),
            ("review_count", "review"),
            ("dangling_count", "dangling"),
        )
        if health.get(key)
    ]
    if flags:
        lines.append("## Health")
        lines.append(
            f"nodes={health.get('node_count', 0)} "
            f"edges={health.get('edge_count', 0)} "
            f"{' '.join(flags)}"
        )
        lines.append("")

    lines.append("[/BOOT CONTEXT]")

    text = "\n".join(lines)
    limit = max(400, token_budget) * _CHARS_PER_TOKEN
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n[/BOOT CONTEXT]"
    return text


def text_for_prompt(vault: str | Path, token_budget: int = DEFAULT_TOKEN_BUDGET) -> str:
    """Convenience wrapper: build and return only the rendered text."""
    result = build(vault, token_budget=token_budget)
    text = result.get("text", "")
    return text if isinstance(text, str) else ""


__all__ = ["DEFAULT_TOKEN_BUDGET", "build", "render", "text_for_prompt"]
