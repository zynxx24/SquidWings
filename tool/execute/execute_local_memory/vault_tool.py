"""Layered memory tools (L1 boot, L2 graph, L3 notes).

Design rule: the code does the bookkeeping so the model never has to.

* The vault is created on first use — no setup step for the agent.
* The graph is built on demand and cached; a stale cache rebuilds itself.
* Note paths are resolved leniently: ``Foo``, ``Foo.md``, ``20-concepts/Foo.md``
  and the note title all reach the same file.
* Every executor returns ``{"ok": bool, ...}`` plus a ``text`` block that is
  ready to paste into a prompt. No post-validation needed by the caller.
* Nothing raises. Failures come back with ``error`` and ``hint``.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from config.settings import get_settings
from tool.execute.execute_local_memory.vault import boot as B
from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault import graph as G
from tool.execute.execute_local_memory.vault import retrieval as R
from tool.execute.execute_local_memory.vault.index import VaultIndex
from tool.execute.execute_local_memory.vault.schema import make_record
from tool.execute.execute_local_ops.path_guard import (
    PathGuardError,
    resolve_workspace,
)
from utils.logger import get_logger
from utils.workspace_context import get_active_workspace

logger = get_logger(__name__)

SESSION_VAULT_DIR = ".vault"

_MAX_BODY_CHARS = 40_000


# --------------------------------------------------------------------------- #
# Vault resolution and bootstrap
# --------------------------------------------------------------------------- #
def _vault(workspace_path: str | None, scope: str = "session") -> tuple[Path, str]:
    """Resolve the vault root and say where it came from.

    Order: the current chat's own vault (unless ``scope`` is global), then the
    explicit setting, then ``<workspace>/vault``. The default is created on first
    use so the agent never hits a missing-directory error.
    """
    active = get_active_workspace()
    if active and scope != "global":
        return Path(active) / SESSION_VAULT_DIR, "session"
    settings = get_settings()
    configured = (settings.obsidian_vault_path or "").strip()
    if configured:
        vault = Path(configured).expanduser()
        source = "settings.obsidian_vault_path"
    else:
        try:
            root = resolve_workspace(workspace_path, settings.project_root)
        except PathGuardError:
            root = settings.project_root
        vault = root / "vault"
        source = "default"
    return vault, source


def _bootstrap(vault: Path) -> list[str]:
    """Create the folder skeleton. Idempotent, never raises."""
    try:
        return C.ensure_layout(vault)
    except OSError as exc:
        logger.warning("vault bootstrap failed: %s", exc)
        return []


def _err(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return payload


def _ok(**payload: Any) -> dict[str, Any]:
    payload.setdefault("ok", True)
    return payload


def _resolve_note(vault: Path, raw: str) -> str | None:
    """Map a loose reference to a real vault path.

    Accepts ``Foo``, ``Foo.md``, ``20-concepts/Foo.md``, or the note title.
    Ambiguity resolves to the shallowest path, matching ``LinkResolver`` so
    the tool and the graph always agree.
    """
    key = (raw or "").strip().strip("/").replace("\\", "/")
    if not key:
        return None
    if key.lower().endswith(".md"):
        key = key[:-3]

    index = VaultIndex(vault)
    try:
        records = index.refresh()
    except (FileNotFoundError, NotADirectoryError):
        return None

    # 1. Exact path as written. This is the common case: the model copies a
    #    path straight out of ``vault_search`` results, which already carry the
    #    folder prefix. Checking only ``key`` here (without ``.md``) missed
    #    every qualified reference, because index keys always end in ``.md``.
    if key in records:
        return key
    if key + ".md" in records:
        return key + ".md"

    target = key.casefold()
    by_path = {p.casefold(): p for p in records}

    # 2. Same path, different case — ``20-concepts/foo.md`` vs ``Foo.md``.
    if target in by_path:
        return by_path[target]
    if target + ".md" in by_path:
        return by_path[target + ".md"]

    # 3. Bare stem or title. A qualified reference that survived steps 1-2 has
    #    the wrong folder, so fall back to the basename rather than failing:
    #    the design goal is that any loose reference reaches the note.
    stem_target = target.rsplit("/", 1)[-1]

    by_stem: dict[str, list[str]] = {}
    by_title: dict[str, list[str]] = {}
    for path, rec in records.items():
        stem = path.rsplit("/", 1)[-1]
        if stem.lower().endswith(".md"):
            stem = stem[:-3]
        by_stem.setdefault(stem.casefold(), []).append(path)
        by_title.setdefault(rec.title.casefold(), []).append(path)

    for bucket in (by_stem, by_title):
        hits = bucket.get(stem_target)
        if hits:
            return sorted(hits, key=lambda p: (p.count("/"), p))[0]
    return None


def _ensure_graph(vault: Path, *, force: bool = False) -> dict[str, Any]:
    """Return a usable graph, building it when missing or forced."""
    if force:
        return G.build(VaultIndex(vault), force=True)
    cached = G.load(vault)
    if cached is not None:
        return cached
    return G.build(VaultIndex(vault))


# --------------------------------------------------------------------------- #
# L1 — boot context
# --------------------------------------------------------------------------- #
async def vault_boot(
    workspace_path: str | None = None,
    token_budget: int = B.DEFAULT_TOKEN_BUDGET,
) -> dict[str, Any]:
    """Load the always-on session context (L1)."""
    vault, source = _vault(workspace_path)
    _bootstrap(vault)
    try:
        context = B.build(vault, token_budget=int(token_budget))
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        logger.exception("vault_boot failed")
        return _err(f"boot failed: {exc}", hint="Check the vault path in settings.")

    if not context.get("ok"):
        return _err(
            context.get("error", "boot unavailable"),
            vault=str(vault),
            hint="Create the vault folder or set OBSIDIAN_VAULT_PATH.",
        )

    return _ok(
        layer="L1",
        vault=str(vault),
        vault_source=source,
        token_estimate=context.get("token_estimate", 0),
        hubs=context.get("hubs", []),
        open_questions=context.get("open_questions", []),
        health=context.get("health", {}),
        text=context.get("text", ""),
    )


# --------------------------------------------------------------------------- #
# L2 — graph
# --------------------------------------------------------------------------- #
async def vault_graph_build(
    workspace_path: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build or refresh the local graph and return a health summary."""
    vault, source = _vault(workspace_path)
    _bootstrap(vault)
    try:
        graph = _ensure_graph(vault, force=bool(force))
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_graph_build failed")
        return _err(f"graph build failed: {exc}", vault=str(vault))

    health = graph.get("health", {})
    return _ok(
        layer="L2",
        vault=str(vault),
        vault_source=source,
        nodes=health.get("node_count", 0),
        edges=health.get("edge_count", 0),
        explicit_edges=health.get("explicit_edges", 0),
        inferred_edges=health.get("inferred_edges", 0),
        communities=health.get("community_count", 0),
        orphans=health.get("orphans", []),
        dangling=health.get("dangling", []),
        stale=health.get("stale", []),
        needs_review=health.get("needs_review", []),
        hubs=graph.get("hubs", []),
        graph_path=str(C.graph_path(vault)),
        index_stats=graph.get("index_stats", {}),
        text=G.summary(graph),
    )


async def vault_neighbours(
    workspace_path: str | None = None,
    path: str = "",
    depth: int = 1,
    include_inferred: bool = True,
) -> dict[str, Any]:
    """Structural neighbourhood of a note (L2). Accepts loose references."""
    vault, _ = _vault(workspace_path)
    _bootstrap(vault)
    resolved = _resolve_note(vault, path)
    if resolved is None:
        return _err(
            f"note not found: {path}",
            vault=str(vault),
            hint="Call vault_search first to find the exact path.",
        )
    try:
        result = R.l2_neighbours(
            vault, resolved, int(depth), include_inferred=bool(include_inferred)
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_neighbours failed")
        return _err(f"neighbours failed: {exc}")

    reached = result.get("reached", [])
    lines = [f"{resolved} (depth {depth}):"]
    for item in reached[1:]:
        lines.append(f"- hop {item['hop']}: {item['path']} — {item.get('title', '')}")
    if len(lines) == 1:
        lines.append("- no neighbours (orphan node)")

    return _ok(
        layer="L2",
        root=resolved,
        depth=depth,
        reached=reached,
        links=result.get("links", []),
        text="\n".join(lines),
    )


async def vault_explain(
    workspace_path: str | None = None,
    source: str = "",
    target: str = "",
) -> dict[str, Any]:
    """Explain how two notes are connected (L2 shortest path)."""
    vault, _ = _vault(workspace_path)
    _bootstrap(vault)
    src = _resolve_note(vault, source)
    dst = _resolve_note(vault, target)
    if src is None or dst is None:
        missing = source if src is None else target
        return _err(f"note not found: {missing}", hint="Use vault_search to locate it.")

    try:
        result = R.explain_connection(vault, src, dst)
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_explain failed")
        return _err(f"explain failed: {exc}")

    chain = result.get("path", [])
    if chain:
        text = " -> ".join(item["title"] or item["path"] for item in chain)
    else:
        text = "not connected"
    return _ok(
        layer="L2",
        source=src,
        target=dst,
        length=result.get("length", -1),
        path=chain,
        note=result.get("note", ""),
        text=text,
    )


async def vault_health(workspace_path: str | None = None) -> dict[str, Any]:
    """Graph and index health, plus the maintenance to-do list."""
    vault, _ = _vault(workspace_path)
    _bootstrap(vault)
    try:
        result = R.health(vault)
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_health failed")
        return _err(f"health failed: {exc}", vault=str(vault))

    graph_health = result.get("graph", {})
    lines = [result.get("summary", "")]
    for key, label in (
        ("orphans", "orphans"),
        ("stale", "stale"),
        ("needs_review", "needs review"),
        ("dangling", "dangling links"),
    ):
        items = graph_health.get(key) or []
        if items:
            lines.append(f"{label} ({len(items)}):")
            for item in items[:10]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('source')} -> {item.get('target')}")
                else:
                    lines.append(f"- {item}")

    return _ok(
        layer="L2",
        vault=str(vault),
        graph=graph_health,
        index=result.get("index", {}),
        text="\n".join(x for x in lines if x),
    )


# --------------------------------------------------------------------------- #
# L2/L3 — search, read, recall, write
# --------------------------------------------------------------------------- #
async def vault_search(
    workspace_path: str | None = None,
    query: str = "",
    tag: str | None = None,
    note_type: str | None = None,
    limit: int = 10,
    include_stale: bool = False,
    scope: str = "session",
) -> dict[str, Any]:
    """Search titles, summaries, tags, and paths (no bodies read)."""
    vault, _ = _vault(workspace_path, scope)
    _bootstrap(vault)
    index = VaultIndex(vault)
    try:
        index.refresh()
        hits = index.search(
            query,
            tag=tag or None,
            note_type=note_type or None,
            include_stale=bool(include_stale),
            limit=int(limit),
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        return _err(str(exc), vault=str(vault), hint="Set OBSIDIAN_VAULT_PATH.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_search failed")
        return _err(f"search failed: {exc}")

    results = [
        {
            "path": r.path,
            "title": r.title,
            "type": r.note_type,
            "status": r.status,
            "confidence": r.confidence,
            "tags": r.tags,
            "summary": r.summary,
        }
        for r in hits
    ]
    if not results:
        text = "no matches"
    else:
        text = "\n".join(
            f"- {r['path']} — {r['title']} [{r['type']}/{r['status']}]"
            + (f" {r['summary'][:120]}" if r["summary"] else "")
            for r in results
        )
    return _ok(
        vault=str(vault),
        layer="L2",
        count=len(results),
        results=results,
        text=text,
    )


async def vault_read(
    workspace_path: str | None = None,
    path: str = "",
    max_chars: int = _MAX_BODY_CHARS,
    scope: str = "session",
) -> dict[str, Any]:
    """Read one note in full (L3). Accepts loose references."""
    vault, _ = _vault(workspace_path, scope)
    _bootstrap(vault)
    resolved = _resolve_note(vault, path)
    if resolved is None:
        return _err(
            f"note not found: {path}",
            hint="Call vault_search first to find the exact path.",
        )

    try:
        result = R.l3_read(vault, resolved)
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_read failed")
        return _err(f"read failed: {exc}")

    if not result.get("ok"):
        return _err(result.get("error", "read failed"), path=resolved)

    body = str(result.get("body", ""))
    truncated = False
    if max_chars and len(body) > int(max_chars):
        body = body[: int(max_chars)] + "\n...[truncated]"
        truncated = True

    return _ok(
        layer="L3",
        path=resolved,
        title=result.get("title", ""),
        type=result.get("type", ""),
        status=result.get("status", ""),
        confidence=result.get("confidence", ""),
        tags=result.get("tags", []),
        body=body,
        truncated=truncated,
        text=body,
    )


async def vault_recall(
    workspace_path: str | None = None,
    query: str = "",
    depth: int = 1,
    limit: int = 5,
    token_budget: int = B.DEFAULT_TOKEN_BUDGET,
    with_bodies: bool = False,
    scope: str = "session",
) -> dict[str, Any]:
    """Answer from the cheapest layer that suffices (L1 -> L2 -> L3)."""
    vault, _ = _vault(workspace_path, scope)
    _bootstrap(vault)
    try:
        result = R.recall(
            vault,
            query,
            depth=int(depth),
            limit=int(limit),
            token_budget=int(token_budget),
            with_bodies=bool(with_bodies),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("vault_recall failed")
        return _err(f"recall failed: {exc}", vault=str(vault))

    if not result.get("ok"):
        return _err(
            result.get("error", "recall failed"),
            vault=str(vault),
            hint="Call vault_search first to check the index.",
        )

    hits = result.get("hits", []) or []
    layers = result.get("layers", ["L1"])
    l1 = result.get("l1", {}) or {}

    parts: list[str] = []
    if l1.get("text"):
        parts.append(l1["text"])
    if hits:
        parts.append("")
        parts.append(f"[{'+'.join(layers)}] {len(hits)} note(s):")
        for hit in hits:
            parts.append(f"- {hit.get('path')} — {hit.get('title', '')}")
            if hit.get("summary"):
                parts.append(f"  {hit['summary'][:200]}")
            for near in (hit.get("neighbours") or [])[:5]:
                parts.append(f"    ~ hop {near['hop']}: {near['path']}")
    else:
        parts.append("")
        parts.append("no L2 hits; L1 only")

    text = "\n".join(parts).strip()
    return _ok(
        layer="+".join(layers),
        layers=layers,
        vault=str(vault),
        notes=hits,
        token_estimate=l1.get("token_estimate", 0),
        graph_summary=result.get("graph_summary", ""),
        text=text,
    )


async def vault_write(
    workspace_path: str | None = None,
    path: str = "",
    body: str = "",
    note_type: str | None = None,
    status: str = C.STATUS_ACTIVE,
    confidence: str = C.CONFIDENCE_MEDIUM,
    tags: list[str] | None = None,
    append: bool = False,
    scope: str = "session",
) -> dict[str, Any]:
    """Write a note with valid frontmatter.

    Frontmatter is generated by the code, so the model never has to remember
    the schema. A bare name like ``Foo`` lands in the folder for its type.
    """
    vault, _ = _vault(workspace_path, scope)
    _bootstrap(vault)

    raw = (path or "").strip().strip("/")
    if not raw:
        return _err("path is required", hint="Pass a note name, e.g. 'Foo'.")
    if not raw.lower().endswith(".md"):
        raw += ".md"
    if "/" not in raw:
        raw = f"{C.folder_for_type(C.normalize_type(note_type))}/{raw}"

    try:
        target = (vault / raw).resolve()
        target.relative_to(vault.resolve())
    except (ValueError, OSError) as exc:
        return _err(f"path escapes vault: {path}", detail=str(exc))

    record, text = make_record(
        raw,
        body,
        note_type=note_type,
        status=status,
        confidence=confidence,
        tags=tags,
    )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if append and target.exists():
            existing = target.read_text(encoding="utf-8", errors="replace")
            text = existing.rstrip() + "\n\n" + body
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        return _err(f"write failed: {exc}", path=str(target))

    # Keep the cache honest: the graph is now stale by definition.
    index = VaultIndex(vault)
    with contextlib.suppress(FileNotFoundError, NotADirectoryError):
        index.refresh()

    return _ok(
        layer="L3",
        path=raw,
        bytes=len(text.encode("utf-8")),
        type=record.note_type,
        status=record.status,
        confidence=record.confidence,
        tags=record.tags,
        appended=bool(append),
        graph_stale=True,
        text=f"wrote {raw} ({len(text.encode('utf-8'))} bytes)",
    )


__all__ = [
    "vault_boot",
    "vault_graph_build",
    "vault_neighbours",
    "vault_explain",
    "vault_health",
    "vault_search",
    "vault_read",
    "vault_recall",
    "vault_write",
]
