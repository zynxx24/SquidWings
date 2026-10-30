"""Retrieval cascade L1 -> L2 -> L3.

The whole point of the layered design is token economy: start with the
cheap layer and only descend when the answer is not already there.

* **L1** - boot context (boot file, hubs, open questions). ~1-2k tokens.
* **L2** - graph structure (neighbours, communities, hubs, health). Cheap,
  no note bodies.
* **L3** - full note text. Expensive, so only fetched for the few notes
  that survived L1 and L2.

Every result reports which layer answered, so the caller can tell whether
it paid for L3 or got away with L2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tool.execute.execute_local_memory.vault import boot as B
from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault import graph as G
from tool.execute.execute_local_memory.vault.index import VaultIndex
from tool.execute.execute_local_memory.vault.schema import NoteRecord
from utils.logger import get_logger

logger = get_logger(__name__)


def _read_note(vault: Path, rel_path: str, max_chars: int = C.MAX_BODY_SCAN_CHARS) -> str:
    """Read a note body, tolerating a missing file."""
    try:
        text = (vault / rel_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:max_chars]


def _record_payload(rec: NoteRecord, *, with_body: bool, vault: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": rec.path,
        "title": rec.title,
        "type": rec.note_type,
        "status": rec.status,
        "confidence": rec.confidence,
        "tags": list(rec.tags),
        "summary": rec.summary,
        "updated": rec.updated,
    }
    if with_body:
        payload["body"] = _read_note(vault, rec.path)
    return payload


# -- L1 -----------------------------------------------------------------
def l1_context(vault: str | Path, token_budget: int = B.DEFAULT_TOKEN_BUDGET) -> dict[str, Any]:
    """Layer 1: the always-on boot context."""
    context = B.build(vault, token_budget=token_budget)
    return {
        "layer": "L1",
        "ok": context.get("ok", False),
        "text": context.get("text", ""),
        "token_estimate": context.get("token_estimate", 0),
        "hubs": context.get("hubs", []),
        "open_questions": context.get("open_questions", []),
        "health": context.get("health", {}),
    }


# -- L2 -----------------------------------------------------------------
def l2_neighbours(
    vault: str | Path,
    path: str,
    depth: int = 1,
    *,
    include_inferred: bool = True,
) -> dict[str, Any]:
    """Layer 2: structural neighbourhood of one note. No bodies read."""
    graph = G.load_or_build(vault)
    result = G.neighbours(graph, path, depth, include_inferred=include_inferred)
    if not result.get("ok"):
        return {"layer": "L2", **result}
    nodes = graph.get("nodes", {})
    for item in result["reached"]:
        node = nodes.get(item["path"], {})
        item["summary"] = node.get("summary", "")
    return {"layer": "L2", **result}


def l2_search(
    vault: str | Path,
    query: str = "",
    *,
    tag: str | None = None,
    note_type: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Layer 2: index search. Titles, summaries, tags, paths — no bodies."""
    index = VaultIndex(vault)
    try:
        index.refresh()
    except (FileNotFoundError, NotADirectoryError) as exc:
        return {"layer": "L2", "ok": False, "error": str(exc), "hits": []}
    hits = index.search(
        query, tag=tag, note_type=note_type, limit=limit
    )
    return {
        "layer": "L2",
        "ok": True,
        "query": query,
        "hits": [_record_payload(r, with_body=False, vault=Path(vault)) for r in hits],
    }


# -- L3 -----------------------------------------------------------------
def l3_read(vault: str | Path, rel_path: str) -> dict[str, Any]:
    """Layer 3: full note text. The expensive layer."""
    vault_path = Path(vault).expanduser()
    index = VaultIndex(vault_path)
    try:
        index.refresh()
    except (FileNotFoundError, NotADirectoryError) as exc:
        return {"layer": "L3", "ok": False, "error": str(exc)}
    rec = index.record(rel_path)
    if rec is None:
        return {"layer": "L3", "ok": False, "error": f"note not found: {rel_path}"}
    payload = _record_payload(rec, with_body=True, vault=vault_path)
    payload["layer"] = "L3"
    payload["ok"] = True
    return payload


# -- cascade ------------------------------------------------------------
def recall(
    vault: str | Path,
    query: str = "",
    *,
    depth: int = 1,
    limit: int = 5,
    token_budget: int = B.DEFAULT_TOKEN_BUDGET,
    with_bodies: bool = False,
    include_inferred: bool = True,
) -> dict[str, Any]:
    """Answer from the cheapest layer that suffices.

    Strategy:

    1. Always include L1 (it is the session's shared context).
    2. Search the index (L2). No hits -> stop, report L1 only.
    3. Expand the top hits through the graph (L2) for structure.
    4. Read bodies (L3) only when ``with_bodies`` is set, and only for the
       top ``limit`` hits.

    The returned ``layers`` list records what was actually paid for.
    """
    vault_path = Path(vault).expanduser()
    layers = ["L1"]

    boot = l1_context(vault_path, token_budget=token_budget)

    search = l2_search(vault_path, query, limit=max(1, limit))
    if not search.get("ok"):
        return {
            "ok": False,
            "error": search.get("error", "search failed"),
            "layers": layers,
            "l1": boot,
            "hits": [],
        }

    hits = search.get("hits", [])
    if not hits:
        return {
            "ok": True,
            "layers": layers,
            "l1": boot,
            "hits": [],
            "note": "no L2 hits; L1 only",
        }

    layers.append("L2")
    graph = G.load_or_build(vault_path)
    expanded: list[dict[str, Any]] = []
    for hit in hits:
        entry = dict(hit)
        near = G.neighbours(
            graph, hit["path"], depth, include_inferred=include_inferred
        )
        if near.get("ok"):
            entry["neighbours"] = [
                {"path": r["path"], "hop": r["hop"], "title": r["title"]}
                for r in near["reached"]
                if r["path"] != hit["path"]
            ][:10]
        expanded.append(entry)

    if with_bodies:
        layers.append("L3")
        for entry in expanded:
            entry["body"] = _read_note(vault_path, entry["path"])

    return {
        "ok": True,
        "layers": layers,
        "l1": boot,
        "hits": expanded,
        "graph_summary": G.summary(graph),
    }


def explain_connection(
    vault: str | Path, source: str, target: str
) -> dict[str, Any]:
    """Why are these two notes related? Answered from L2 alone."""
    graph = G.load_or_build(vault)
    result = G.shortest_path(graph, source, target)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "unknown")}
    nodes = graph.get("nodes", {})
    chain = [
        {"path": p, "title": nodes.get(p, {}).get("title", "")}
        for p in result.get("path", [])
    ]
    return {
        "ok": True,
        "layer": "L2",
        "length": result.get("length", -1),
        "path": chain,
        "note": result.get("note", ""),
    }


def health(vault: str | Path) -> dict[str, Any]:
    """Graph + index health. Rebuilds the graph when missing."""
    graph = G.load_or_build(vault)
    return {
        "ok": True,
        "layer": "L2",
        "graph": graph.get("health", {}),
        "index": graph.get("index_stats", {}),
        "summary": G.summary(graph),
    }


__all__ = [
    "l1_context",
    "l2_neighbours",
    "l2_search",
    "l3_read",
    "recall",
    "explain_connection",
    "health",
]
