"""Choosing skills for a request: trigger scoring, waking contextual skills, the prompt's index.

Scores come from ``brain/harness/relevance.py``: a trigger shared by many skills says little, a
rare one says a lot, and overlapping phrases count once. ``select`` picks the best guides for the
prompt (plus the built-in companions and, for design work, one rotating design partner from
``design.py``); ``index_text`` lists only the most relevant skills when there are many.
"""

from __future__ import annotations

import os
from functools import lru_cache

from brain.harness.relevance import Index, TriggerWeights, has_phrase
from skills.loader import Skill, _load_all_cached, load_all


def clear_indexes() -> None:
    _weights.cache_clear()
    _index.cache_clear()


def _hits(words: tuple[str, ...], text: str) -> int:
    return sum(1 for word in words if has_phrase(word, text))


@lru_cache(maxsize=1)
def _weights() -> TriggerWeights:
    """Trigger weights over every routable skill; rebuilt with load_all."""
    return TriggerWeights({s.name: s.triggers for s in _load_all_cached().values() if s.scope != "always"})


def _score(skill: Skill, text: str) -> float:
    """How strongly this request calls for the skill. 0 when no trigger matches.

    A specific trigger ("gsap", "landing page") outweighs a common one ("desain"): see
    brain/harness/relevance.py. The gate is unchanged: at least one whole trigger must appear.
    """
    if not skill.triggers:
        return 0.0
    weights = _weights()
    if skill.name not in weights.triggers:
        return float(_hits(skill.triggers, text))
    return weights.score(skill.name, text)


@lru_cache(maxsize=1)
def _index() -> Index:
    """BM25 over active and stored skills, for load_skill(search=...)."""
    pool = {**{s.name: s for s in stored_all().values()}, **_load_all_cached()}
    return Index({name: [(name.replace("-", " "), 3.0), (" ".join(s.triggers), 2.0), (s.description, 1.0)]
                  for name, s in pool.items()})


def stored_all() -> dict[str, Skill]:
    try:
        from skills import sources

        return sources.stored()
    except Exception:  # noqa: BLE001
        return {}


def rank(query: str, limit: int = 10) -> list[tuple[str, float]]:
    """Active and stored skills for a free-text need, best first."""
    return _index().rank(query, limit=limit)


def woken(user_input: str, task: str = "") -> list[str]:
    """Contextual skills this request calls for. A skill limited to other tasks stays asleep:
    "buat parser csv" in a coding request must not wake the document skill."""
    text = user_input.lower()
    return [
        s.name for s in load_all().values()
        if s.scope == "contextual" and _score(s, text) and (not s.context or _hits(s.context, text))
        and not (task and s.tasks and task not in s.tasks)
    ]


def select(user_input: str, limit: int = 2, active: tuple[str, ...] | list[str] = (), task: str = "") -> list[Skill]:
    """Skills whose triggers appear in the request, most matches first.

    A contextual skill counts only once it is active in this session, or this
    request wakes it.
    """
    text = user_input.lower()
    now = set(woken(user_input, task))
    awake = set(active) | now
    scored: list[tuple[float, Skill]] = []
    for skill in load_all().values():
        if skill.scope == "always":
            continue   # already in the prompt through always_text
        if skill.scope == "contextual" and skill.name not in awake:
            continue
        if task and skill.tasks and task not in skill.tasks:
            continue
        score = _score(skill, text)
        if score:
            # A specialised skill this very request woke ("gsap", "remotion") counts double, so it
            # beats a general one that merely shares a few words with it ("landing page" for build-web).
            if skill.name in now:
                score *= 2.0
            scored.append((score, skill))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = [skill for _, skill in scored if not skill.companion]
    from skills.design import DESIGN_POOL, design_partner  # design.py builds on the scores here

    partner = design_partner(user_input, task, awake)
    if partner is not None:
        # One design lens per chat, rotated across chats, instead of the same general guide every time.
        chosen = [skill for skill in chosen if skill.pool != DESIGN_POOL][:limit] + [partner]
    else:
        chosen = chosen[:limit]
    return chosen + companions(text, [skill.name for skill in chosen])


# ─── design partner rotation ──────────────────────────────────────────
# Design requests used to get design-ui every time: its triggers ("desain", "ui", "tampilan")
# match nearly every request, while the specialist guides (ui-ux-pro-max, impeccable,
# accessibility) only woke when named. Skills with ``pool: design`` now take turns: a design
# request gets one of them, the one used longest ago across all chats, and keeps it for the
# rest of that chat. Naming one ("pakai impeccable") still picks that one.

def companions(text: str, picked: list[str]) -> list[Skill]:
    """Built-in skills that come with this work: a picked skill they accompany, or their own trigger."""
    lowered = text.lower()
    return [
        s for s in load_all().values()
        if s.companion and (set(s.companion) & set(picked) or _score(s, lowered))
    ]


def tools_for(active: tuple[str, ...] | list[str]) -> list[str]:
    """Tools the awake contextual skills need."""
    found = load_all()
    return [tool for name in active if name in found for tool in found[name].tools]


def visible(active: tuple[str, ...] | list[str] = (), task: str = "") -> list[Skill]:
    """Skills the model is told about: core ones meant for this task, plus contextual ones awake in the session."""
    out = []
    for s in load_all().values():
        if s.scope == "always":
            continue   # its rules are already in the prompt
        if s.scope == "contextual" and s.name not in active:
            continue
        if task and s.tasks and task not in s.tasks and s.name not in active:
            continue
        out.append(s)
    return out


def index_max() -> int:
    """Most skills listed in one prompt's index. 0 (default) lists them all, the most relevant first:
    no harness limit on what a model sees. ``SKILL_INDEX_MAX`` > 0 shortens the list."""
    try:
        value = int(os.getenv("SKILL_INDEX_MAX", "0"))
    except ValueError:
        return 0
    return max(3, value) if value > 0 else 0


def index_text(active: tuple[str, ...] | list[str] = (), task: str = "", query: str = "") -> str:
    """One line per skill, so the model knows what it can ask for.

    Dormant skills are left out, and so are core skills meant for other kinds of task:
    a heart-to-heart does not need the backend checklist in its prompt.

    With many skills the index would cost more than the guides, so at most ``index_max()`` are
    listed: the ones awake in this chat, then those this request (``query``) calls for, then the
    best BM25 matches. The rest are counted, and ``load_skill(search=...)`` finds them.
    """
    skills = visible(active, task)
    if not skills:
        return ""
    limit = index_max()
    hidden = 0
    if query or (limit and len(skills) > limit):
        text = query.lower()
        awake = set(active)
        by_name = {s.name: s for s in skills}
        ranked = {name: score for name, score in rank(query, limit=len(skills) * 2)} if query else {}

        def priority(skill: Skill) -> tuple[int, float, float, str]:
            return (0 if skill.name in awake else 1, -_score(skill, text) if text else 0.0,
                    -ranked.get(skill.name, 0.0), skill.name)

        ordered = sorted(skills, key=priority)
        keep = ordered[:limit] if limit else ordered
        hidden = len(skills) - len(keep)
        skills = [by_name[s.name] for s in keep]   # most relevant first
    lines = []
    for skill in skills:
        missing = skill.missing()
        note = f" (belum terpasang: {', '.join(missing)})" if missing else ""
        lines.append(f"- {skill.name}: {skill.description}{note}")
    header = "[SKILLS: call load_skill(name) for the full guide]"
    if hidden:
        header += f"\n({hidden} more skills fit less well and are not listed: load_skill(search=\"what you need\") finds them.)"
    if _stored_count():
        header += f"\n(Plus {_stored_count()} stored skills not listed here: load_skill(search=\"what you need\") finds them.)"
    return header + "\n" + "\n".join(lines)


def _stored_count() -> int:
    try:
        from skills import sources

        return len(sources.stored())
    except Exception:  # noqa: BLE001
        return 0


# Skills from GitHub were written for Claude Code. Their text stays as the authors wrote it;
