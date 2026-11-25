"""Design partner rotation: design requests take turns among the ``pool: design`` skills.

Design requests used to get design-ui every time: its triggers ("desain", "ui", "tampilan")
match nearly every request, while the specialist guides (ui-ux-pro-max, impeccable,
accessibility) only woke when named. Now a design request gets one of them, the one used longest
ago across all chats, and keeps it for the rest of that chat. Naming one ("pakai impeccable")
still picks that one.
"""

from __future__ import annotations

from skills.loader import Skill, load_all
from skills.selection import _hits, _score

DESIGN_POOL = "design"
_PARTNER_HISTORY = "design-skill"   # brain.utils.style_history medium; shared across chats on purpose


def pool_members(pool: str, task: str = "") -> list[Skill]:
    return [s for s in load_all().values() if s.pool == pool and not (task and s.tasks and task not in s.tasks)]


def _design_work(text: str, task: str) -> bool:
    """The request is design work: it matches a design-pool skill or a skill that wants a design partner."""
    return any(
        (s.pool == DESIGN_POOL or DESIGN_POOL in s.wants) and _score(s, text)
        and not (task and s.tasks and task not in s.tasks)
        for s in load_all().values()
    )


def design_partner(user_input: str, task: str = "", active: tuple[str, ...] | list[str] | set[str] = ()) -> Skill | None:
    """The design guide this request gets, or None when it is not design work. Pure: it reads the
    shared history but never writes it (``claim_design_partner`` does)."""
    text = user_input.lower()
    members = pool_members(DESIGN_POOL, task)
    if not members or not _design_work(text, task):
        return None
    named = [m for m in members if m.scope == "contextual" and _score(m, text)
             and (not m.context or _hits(m.context, text))]
    if named:
        return max(named, key=lambda m: _score(m, text))
    awake = [m for m in members if m.name in set(active)]
    if awake:
        return awake[0]
    try:
        from brain.utils import style_history

        history = style_history.recent(_PARTNER_HISTORY)
    except Exception:  # noqa: BLE001 - no history only costs the rotation
        history = []
    order = {name: i for i, name in enumerate(history)}   # oldest first; never used sorts before all
    return min(members, key=lambda m: (order.get(m.name, -1), members.index(m)))


def claim_design_partner(user_input: str, task: str = "", active: tuple[str, ...] | list[str] = ()) -> str | None:
    """Pick this request's design partner and, when it is new to the chat, record it in the shared
    history so the next chat gets another one. Returns its name, to be woken for the session."""
    partner = design_partner(user_input, task, active)
    if partner is None:
        return None
    if partner.name not in set(active):
        try:
            from brain.utils import style_history

            style_history.remember(_PARTNER_HISTORY, partner.name)
        except Exception:  # noqa: BLE001
            pass
    return partner.name

