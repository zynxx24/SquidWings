"""Our own adjustments for skills cloned from GitHub. The upstream skill itself is never edited.

An adapter is ``skills/adapters/<skill name>.md``::

    ---
    skill: google-maps
    upstream: guide      # inline (default) or guide
    fallback: true       # optional
    ---
    What this harness does differently: which of our tools to use, when the guide applies...

* ``upstream: inline``: the skill's text is the adapter, then the upstream guide unchanged.
  For short guides (Ponytail, Obsidian).
* ``upstream: guide``: the skill's text is the adapter alone, and the upstream guide is one
  ``load_skill(name, file="SKILL.md")`` away. For long guides the model rarely needs in full
  (Google Maps Platform, graphify), so a question about a cafe does not cost 25 KB of prompt.
* ``fallback: true``: while the upstream clone is missing (first start, offline), the adapter
  alone stands in as the skill, so the feature keeps working.

Routing (triggers, context, tools, scope) stays in ``sources.json`` like every other GitHub skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ADAPTERS_DIR = Path(__file__).resolve().parent / "adapters"
UPSTREAM_GUIDE = "SKILL.md"
_MODES = ("inline", "guide")


@dataclass(frozen=True)
class Adapter:
    skill: str
    body: str
    upstream: str = "inline"
    fallback: bool = False
    path: Path | None = None


def _parse(path: Path) -> Adapter | None:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    fields: dict[str, str] = {}
    body = text
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        body = match.group(2)
    mode = fields.get("upstream", "inline").lower()
    return Adapter(
        skill=fields.get("skill") or path.stem,
        body=body.strip(),
        upstream=mode if mode in _MODES else "inline",
        fallback=fields.get("fallback", "").lower() in {"1", "true", "yes"},
        path=path,
    )


@lru_cache(maxsize=1)
def all_adapters() -> dict[str, Adapter]:
    found: dict[str, Adapter] = {}
    if not ADAPTERS_DIR.is_dir():
        return found
    for path in sorted(ADAPTERS_DIR.glob("*.md")):
        adapter = _parse(path)
        if adapter:
            found[adapter.skill] = adapter
    return found


def get(name: str) -> Adapter | None:
    return all_adapters().get(name)


def combine(adapter: Adapter | None, upstream_body: str, origin: str) -> str:
    """The text a skill carries: the adapter, then (inline) the upstream guide or (guide) a pointer to it."""
    if adapter is None or not adapter.body:
        return upstream_body
    if adapter.upstream == "guide":
        return (f"{adapter.body}\n\n[The full official guide from {origin} is not shown here: "
                f"load_skill(name=\"{adapter.skill}\", file=\"{UPSTREAM_GUIDE}\") opens it unchanged.]")
    return f"{adapter.body}\n\n[Upstream guide from {origin}, unchanged:]\n\n{upstream_body}"


def reload() -> None:
    all_adapters.cache_clear()


__all__ = ["Adapter", "ADAPTERS_DIR", "UPSTREAM_GUIDE", "all_adapters", "combine", "get", "reload"]
