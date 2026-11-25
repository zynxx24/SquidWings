"""Skills: short playbooks written by people, loaded when a task calls for one.

Each ``*.md`` file here starts with a small header::

    ---
    name: fix-bug
    description: One line shown in the skill index.
    triggers: bug, error, perbaiki
    ---

A bigger skill is a folder with a ``SKILL.md`` and companion files (more ``.md``
guides, code the skill runs). Its header may add ``requires: node, ffmpeg`` to
name the programs it needs; the index says which are missing.

Skills come in two scopes. ``core`` (the default) is the everyday kit: always in
the index, and its guide is added when a trigger matches. ``scope: contextual``
is for skills only some work needs, such as animation. It stays dormant, out of
the index and out of the prompt, until a request names one of its triggers. An
optional ``context:`` list must match as well, so "animasi hover di tombol" (CSS)
does not wake a video skill but "video animasi" does. ``tools:`` names the tools
an awake contextual skill must be offered, even on a small model's short list.
From then on it stays active for that session only, so "lanjutkan" does not
lose it halfway, and other chats never see it.

``scope: always`` is a short rule set that is in every prompt its ``tasks`` cover,
whatever the request says, small model or frontier, tools or none. Keep such a
body under ``ALWAYS_MAX_CHARS``: every request pays for it.

A skill with ``companion: build-web, animate`` is built in to those works: it is
always added when one of them is picked, or when its own triggers match, on top
of the usual limit. ``aislop`` is one: every web page, video or image comes with
its no-AI-slop check, even on image requests that otherwise get no skills.

The most relevant skills are placed in the prompt automatically, because small
models rarely ask for help on their own. The rest stay available through the
``load_skill`` tool, so they cost one line of prompt until needed. Set SKILLS_DIR
to add a folder of your own; a skill there replaces a built-in of the same name.
SKILLS_DISABLED takes a comma-separated list of skills to leave out.

Skills from GitHub are listed in ``sources.json`` and handled by ``sources.py``:
cloned automatically, the important ones active here like any other skill, the
rest stored (out of the index, loadable by exact name), and the chosen ones also
linked into Claude Code's ``~/.claude/skills``.
"""


from skills.design import DESIGN_POOL, claim_design_partner, design_partner, pool_members
from skills.loader import (
    ALWAYS_MAX_CHARS,
    Skill,
    _guides,
    _parse,
    always,
    always_text,
    load_all,
    stored,
)
from skills.runtime import GITHUB_SKILL_NOTE, _has_code, install, runnable
from skills.selection import (
    _hits,
    _score,
    _weights,
    companions,
    index_max,
    index_text,
    rank,
    select,
    stored_all,
    tools_for,
    visible,
    woken,
)

__all__ = ["ALWAYS_MAX_CHARS", "DESIGN_POOL", "GITHUB_SKILL_NOTE", "Skill", "_guides", "_has_code", "_hits",
           "_parse", "_score", "_weights", "always", "always_text", "claim_design_partner", "companions",
           "design_partner", "index_max", "index_text", "install", "load_all", "pool_members", "rank",
           "runnable", "select", "stored", "stored_all", "tools_for", "visible", "woken"]
