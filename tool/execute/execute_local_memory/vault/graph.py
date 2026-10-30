"""Local graph builder (L2).

Builds ``graphify-out/graph.json`` from the vault index — no external
service, no network. This is the piece that removes the dependency on a
third-party Graphify endpoint.

Two kinds of edges, never mixed:

* **explicit** — wikilinks and frontmatter relations. Trusted.
* **inferred** — shared tags above a threshold. A guess, always labelled.

``graph.json`` is a cache. Delete it and :func:`build` regenerates it from
the vault. Nothing downstream may treat it as authoritative.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault.index import VaultIndex
from tool.execute.execute_local_memory.vault.schema import NoteRecord
from utils.logger import get_logger

logger = get_logger(__name__)


# -- link resolution ----------------------------------------------------
class LinkResolver:
    """Resolve Obsidian-style link targets to vault paths.

    Obsidian links by title or filename, not by path, so ``[[Foo]]`` must
    match ``20-concepts/Foo.md``. Ambiguous targets resolve to the shallowest
    path, which is deterministic and therefore predictable.
    """

    def __init__(self, records: dict[str, NoteRecord]) -> None:
        self._by_path: dict[str, str] = {}
        self._by_title: dict[str, list[str]] = defaultdict(list)
        self._by_stem: dict[str, list[str]] = defaultdict(list)
        for path, rec in records.items():
            self._by_path[path.casefold()] = path
            if path.lower().endswith(".md"):
                self._by_path.setdefault(path[:-3].casefold(), path)
            self._by_title[rec.title.casefold()].append(path)
            stem = path.rsplit("/", 1)[-1]
            if stem.lower().endswith(".md"):
                stem = stem[:-3]
            self._by_stem[stem.casefold()].append(path)
        for bucket in (self._by_title, self._by_stem):
            for key in bucket:
                bucket[key].sort(key=lambda p: (p.count("/"), p))

    def resolve(self, target: str) -> str | None:
        key = (target or "").strip().strip("/")
        if not key:
            return None
        # Exact path match wins over title/stem guessing. This must return the
        # stored path, never the key: graph nodes are keyed by full vault path,
        # so returning a bare stem silently drops every wikilink at add_edge().
        exact = self._by_path.get(key.casefold())
        if exact is not None:
            return exact
        if key.lower().endswith(".md"):
            key = key[:-3]
            exact = self._by_path.get(key.casefold())
            if exact is not None:
                return exact
        for bucket in (self._by_title, self._by_stem):
            hits = bucket.get(key.casefold())
            if hits:
                return hits[0]
        return None


# -- community detection ------------------------------------------------
def _label_propagation(
    node_ids: list[str], adjacency: dict[str, dict[str, float]]
) -> dict[str, int]:
    """Deterministic label propagation.

    Each node adopts the dominant label among its neighbours; ties break on
    the smallest label id. Iteration order is sorted, so the same graph
    always yields the same communities — important because the result is
    cached and compared across runs.
    """
    labels = {nid: i for i, nid in enumerate(sorted(node_ids))}
    for _ in range(C.COMMUNITY_MAX_ITERATIONS):
        changed = False
        for nid in sorted(node_ids):
            neighbours = adjacency.get(nid, {})
            if not neighbours:
                continue
            weight: dict[int, float] = defaultdict(float)
            for other, w in neighbours.items():
                weight[labels[other]] += w
            best = max(weight.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if labels[nid] != best:
                labels[nid] = best
                changed = True
        if not changed:
            break

    remap: dict[int, int] = {}
    for nid in sorted(node_ids):
        old = labels[nid]
        if old not in remap:
            remap[old] = len(remap)
        labels[nid] = remap[old]
    return labels


# -- builder ------------------------------------------------------------
def build(
    index: VaultIndex,
    *,
    force: bool = False,
    write: bool = True,
) -> dict[str, Any]:
    """Build the graph from the index and optionally write it to disk."""
    records = index.refresh(force=force)
    resolver = LinkResolver(records)

    nodes: dict[str, dict[str, Any]] = {}
    for path, rec in records.items():
        node = rec.to_node()
        node["degree"] = 0
        node["community"] = -1
        nodes[path] = node

    edges: list[dict[str, Any]] = []
    dangling: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(src: str, dst: str, etype: str, origin: str) -> None:
        if src == dst or src not in nodes or dst not in nodes:
            return
        key = (src, dst, etype)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({
            "source": src,
            "target": dst,
            "type": etype,
            "origin": origin,
            "weight": C.EDGE_ORIGIN_WEIGHT[origin],
        })

    # 1. Explicit: wikilinks in the body.
    for path, rec in records.items():
        for target in rec.links:
            resolved = resolver.resolve(target)
            if resolved is None:
                dangling.append({"source": path, "target": target})
                continue
            add_edge(path, resolved, C.EDGE_LINKS, C.ORIGIN_EXPLICIT)

    # 2. Explicit: typed relations in frontmatter.
    for path, rec in records.items():
        for etype, target in rec.relations:
            resolved = resolver.resolve(target)
            if resolved is None:
                dangling.append({"source": path, "target": target})
                continue
            add_edge(path, resolved, etype, C.ORIGIN_EXPLICIT)

    # 3. Inferred: shared tags. Labelled as a guess, never mixed with above.
    tag_index: dict[str, list[str]] = defaultdict(list)
    for path, rec in records.items():
        for tag in rec.tags:
            tag_index[tag.casefold()].append(path)

    pair_tags: dict[tuple[str, str], int] = defaultdict(int)
    for paths in tag_index.values():
        unique = sorted(set(paths))
        for i, a in enumerate(unique):
            for b in unique[i + 1:]:
                pair_tags[(a, b)] += 1

    for (a, b), shared in pair_tags.items():
        if shared < C.MIN_SHARED_TAGS_FOR_EDGE:
            continue
        add_edge(a, b, C.EDGE_SHARES_TAG, C.ORIGIN_INFERRED)

    # -- degrees (undirected) ----------------------------------------
    adjacency: dict[str, dict[str, float]] = defaultdict(dict)
    for edge in edges:
        src, dst, w = edge["source"], edge["target"], edge["weight"]
        adjacency[src][dst] = max(adjacency[src].get(dst, 0.0), w)
        adjacency[dst][src] = max(adjacency[dst].get(src, 0.0), w)

    for path, node in nodes.items():
        node["degree"] = len(adjacency.get(path, {}))

    # -- communities --------------------------------------------------
    labels = _label_propagation(list(nodes), adjacency)
    communities: dict[int, list[str]] = defaultdict(list)
    for path, label in labels.items():
        communities[label].append(path)
        nodes[path]["community"] = label

    community_list = [
        {"id": cid, "size": len(members), "members": sorted(members)}
        for cid, members in sorted(communities.items())
    ]

    # -- hubs ---------------------------------------------------------
    hubs = sorted(
        (n for n in nodes.values() if n["degree"] >= C.HUB_MIN_DEGREE),
        key=lambda n: (-n["degree"], n["path"]),
    )[: C.HUB_TOP_N]

    # -- health -------------------------------------------------------
    orphans = sorted(p for p, n in nodes.items() if n["degree"] == 0)
    stale = sorted(p for p, r in records.items() if r.is_stale)
    review = sorted(p for p, r in records.items() if r.needs_review)

    graph = {
        "version": C.GRAPH_VERSION,
        "built_at": time.time(),
        "vault": str(index.vault),
        "nodes": nodes,
        "edges": edges,
        "communities": community_list,
        "hubs": [
            {"path": h["path"], "title": h["title"], "degree": h["degree"]}
            for h in hubs
        ],
        "health": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "explicit_edges": sum(
                1 for e in edges if e["origin"] == C.ORIGIN_EXPLICIT
            ),
            "inferred_edges": sum(
                1 for e in edges if e["origin"] == C.ORIGIN_INFERRED
            ),
            "community_count": len(community_list),
            "orphans": orphans,
            "dangling": dangling,
            "stale": stale,
            "needs_review": review,
        },
        "index_stats": dict(index.stats),
    }

    if write:
        _write_graph(index.vault, graph)
        _write_report(index.vault, graph)

    return graph


def _write_graph(vault: Path, graph: dict[str, Any]) -> None:
    path = C.graph_path(Path(vault))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("graph write failed: %s", exc)


def _write_report(vault: Path, graph: dict[str, Any]) -> None:
    """Human-readable health report. Cheap to regenerate, worth keeping."""
    health = graph["health"]
    lines = [
        "# Graph report",
        "",
        "Built: " + time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(graph["built_at"])
        ),
        "Vault: `" + graph["vault"] + "`",
        "",
        "## Counts",
        "",
        f"- Nodes: {health['node_count']}",
        f"- Edges: {health['edge_count']} "
        f"({health['explicit_edges']} explicit, "
        f"{health['inferred_edges']} inferred)",
        f"- Communities: {health['community_count']}",
        "",
    ]

    if graph["hubs"]:
        lines += ["## Hubs", ""]
        for hub in graph["hubs"]:
            lines.append(
                f"- `{hub['path']}` — {hub['title']} (degree {hub['degree']})"
            )
        lines.append("")

    for key, title in (
        ("orphans", "Orphans"),
        ("stale", "Superseded / archived"),
        ("needs_review", "Low confidence (needs review)"),
    ):
        if health.get(key):
            lines += [f"## {title}", ""]
            lines += [f"- `{p}`" for p in health[key]]
            lines.append("")

    if health["dangling"]:
        lines += ["## Dangling links", ""]
        for item in health["dangling"]:
            lines.append(f"- `{item['source']}` → `{item['target']}`")
        lines.append("")

    try:
        path = vault / C.FOLDER_GRAPH_OUT / C.REPORT_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        logger.warning("graph report write failed: %s", exc)


def load(vault: str | Path) -> dict[str, Any] | None:
    """Read ``graph.json`` if present. None when absent, corrupt, or stale."""
    path = C.graph_path(Path(vault))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("graph unreadable: %s", exc)
        return None
    if not isinstance(data, dict) or data.get("version") != C.GRAPH_VERSION:
        return None
    return data


def load_or_build(vault: str | Path, *, force: bool = False) -> dict[str, Any]:
    """Return the cached graph, building it only when missing or forced."""
    if not force:
        cached = load(vault)
        if cached is not None:
            return cached
    return build(VaultIndex(vault), force=force)


# -- queries ------------------------------------------------------------
def _adjacency(
    graph: dict[str, Any], include_inferred: bool = True
) -> dict[str, list[dict[str, Any]]]:
    adj: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in graph.get("edges", []):
        if not include_inferred and edge.get("origin") == C.ORIGIN_INFERRED:
            continue
        adj[edge["source"]].append({
            "path": edge["target"], "type": edge["type"],
            "origin": edge["origin"], "direction": "out",
        })
        adj[edge["target"]].append({
            "path": edge["source"], "type": edge["type"],
            "origin": edge["origin"], "direction": "in",
        })
    return adj


def neighbours(
    graph: dict[str, Any],
    path: str,
    depth: int = 1,
    *,
    include_inferred: bool = True,
) -> dict[str, Any]:
    """BFS from ``path`` up to ``depth`` hops, with the edges that got there."""
    nodes = graph.get("nodes", {})
    if path not in nodes:
        return {"ok": False, "error": f"node not found: {path}"}

    adj = _adjacency(graph, include_inferred)
    hop_of: dict[str, int] = {path: 0}
    links: list[dict[str, Any]] = []
    frontier = [path]

    for hop in range(1, max(1, depth) + 1):
        nxt: list[str] = []
        for cur in frontier:
            for link in adj.get(cur, []):
                links.append({"from": cur, "hop": hop, **link})
                if link["path"] not in hop_of:
                    hop_of[link["path"]] = hop
                    nxt.append(link["path"])
        frontier = nxt
        if not frontier:
            break

    reached = [
        {
            "path": p,
            "hop": h,
            "title": nodes.get(p, {}).get("title", ""),
            "type": nodes.get(p, {}).get("type", ""),
            "status": nodes.get(p, {}).get("status", ""),
        }
        for p, h in sorted(hop_of.items(), key=lambda kv: (kv[1], kv[0]))
    ]
    return {"ok": True, "root": path, "depth": depth,
            "reached": reached, "links": links}


def shortest_path(
    graph: dict[str, Any], source: str, target: str, *, include_inferred: bool = True
) -> dict[str, Any]:
    """Shortest undirected path between two notes, for 'how are these related'."""
    nodes = graph.get("nodes", {})
    for p in (source, target):
        if p not in nodes:
            return {"ok": False, "error": f"node not found: {p}"}
    if source == target:
        return {"ok": True, "path": [source], "length": 0}

    adj = _adjacency(graph, include_inferred)
    parent: dict[str, str | None] = {source: None}
    queue = [source]
    while queue:
        cur = queue.pop(0)
        for link in adj.get(cur, []):
            nxt = link["path"]
            if nxt in parent:
                continue
            parent[nxt] = cur
            if nxt == target:
                chain = [target]
                while parent[chain[-1]] is not None:
                    chain.append(parent[chain[-1]])  # type: ignore[arg-type]
                chain.reverse()
                return {"ok": True, "path": chain, "length": len(chain) - 1}
            queue.append(nxt)
    return {"ok": True, "path": [], "length": -1, "note": "not connected"}


def orphans(graph: dict[str, Any]) -> list[str]:
    return list(graph.get("health", {}).get("orphans", []))


def dangling(graph: dict[str, Any]) -> list[dict[str, str]]:
    return list(graph.get("health", {}).get("dangling", []))


def stale(graph: dict[str, Any]) -> list[str]:
    return list(graph.get("health", {}).get("stale", []))


def needs_review(graph: dict[str, Any]) -> list[str]:
    return list(graph.get("health", {}).get("needs_review", []))


def hubs(graph: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    return list(graph.get("hubs", []))[: max(1, limit)]


def node(graph: dict[str, Any], path: str) -> dict[str, Any] | None:
    candidate = graph.get("nodes", {}).get(path)
    return candidate if isinstance(candidate, dict) else None


def community_of(graph: dict[str, Any], path: str) -> dict[str, Any] | None:
    n = node(graph, path)
    if not n:
        return None
    cid = n.get("community", -1)
    for comm in graph.get("communities", []):
        if comm["id"] == cid:
            return comm if isinstance(comm, dict) else None
    return None


def summary(graph: dict[str, Any]) -> str:
    """One-screen text summary, used by L1 and by the graphify_query tool."""
    health = graph.get("health", {})
    lines = [
        f"nodes={health.get('node_count', 0)} "
        f"edges={health.get('edge_count', 0)} "
        f"(explicit={health.get('explicit_edges', 0)}, "
        f"inferred={health.get('inferred_edges', 0)}) "
        f"communities={health.get('community_count', 0)}",
    ]
    top = graph.get("hubs", [])[:5]
    if top:
        lines.append("hubs: " + ", ".join(
            f"{h['title']}({h['degree']})" for h in top
        ))
    for key, label in (
        ("orphans", "orphans"),
        ("stale", "stale"),
        ("needs_review", "review"),
        ("dangling", "dangling"),
    ):
        items = health.get(key) or []
        if items:
            lines.append(f"{label}: {len(items)}")
    return "\n".join(lines)


__all__ = [
    "LinkResolver",
    "build",
    "load",
    "load_or_build",
    "neighbours",
    "shortest_path",
    "orphans",
    "dangling",
    "stale",
    "needs_review",
    "hubs",
    "node",
    "community_of",
    "summary",
]
