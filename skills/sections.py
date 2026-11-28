"""Big guides arrive as an outline; the model opens the part it needs.

A 33k-character guide injected whole costs about 8k tokens on every turn that selects it, most of it about
cases the request never touches. A guide longer than ``SKILL_SECTION_CHARS`` (12k) is injected as its
introduction plus a table of its ``##`` sections, each with the ``load_skill(..., section=...)`` call that opens
it. ``load_skill(name)`` without a section still returns the whole guide.
"""

from __future__ import annotations

import os
import re

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_INTRO_MAX = 5_000


def threshold() -> int:
    try:
        return max(2_000, int(os.getenv("SKILL_SECTION_CHARS", "") or 12_000))
    except ValueError:
        return 12_000


def headings(body: str) -> list[str]:
    return [m.group(2) for m in _HEADING.finditer(body) if m.group(1) == "##"]


def outline(name: str, body: str) -> str:
    """The guide itself when it is small; else its introduction and the sections to open."""
    if len(body) <= threshold():
        return body
    titles = headings(body)
    if not titles:
        return body[:threshold()] + f"\n\n[... guide continues: load_skill(name=\"{name}\") for all of it]"
    first = _HEADING.search(body)
    intro = body[: first.start()].strip() if first else ""
    if len(intro) > _INTRO_MAX:
        intro = intro[:_INTRO_MAX].rsplit("\n", 1)[0] + "\n[...]"
    table = "\n".join(f"- {title}: load_skill(name=\"{name}\", section=\"{title}\")" for title in titles)
    return (f"{intro}\n\n[GUIDE OUTLINE: this guide is long ({len(body):,} characters). Open only the sections this "
            f"request needs:]\n{table}")


def section(body: str, wanted: str) -> str | None:
    """The ``##`` section whose title starts with ``wanted`` (case and spacing ignored), up to the next ``##``."""
    key = " ".join(wanted.lower().split())
    matches = [m for m in _HEADING.finditer(body) if m.group(1) == "##"]
    for i, match in enumerate(matches):
        if " ".join(match.group(2).lower().split()).startswith(key):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            return body[match.start():end].strip()
    return None
