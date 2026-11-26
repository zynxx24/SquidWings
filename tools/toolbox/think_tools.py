"""think_wider: more directions to think in, on demand, for any kind of work.

Every turn with room for choice already gets a small "think wider" block (``brain.harness.divergence``). This tool is
for when that is not enough: the user wants ideas, alternatives, "yang beda", or the first attempt came out flat.
It returns the autopilot answers to avoid, every axis along which approaches differ, a larger set of fresh lenses,
and the proof that fits the work.
"""

from __future__ import annotations

import random
from typing import Any

from brain.harness import divergence
from brain.utils import style_history

from .catalog import group, param, spec

group("thinking", "Think wider: alternatives, fresh angles, ideas for design, code, maths, writing, plans and data",
      "ide, ideas, brainstorm, alternatif, alternative, opsi, options, kreatif, creative, inovatif, innovative, beda, "
      "lain, variasi, out of the box, sudut pandang, angle, pendekatan, approach, cara lain, jangan sama")


@spec("think_wider", "More ways to think about a task when the first idea is the obvious one: the autopilot answers "
      "to avoid, every axis along which real alternatives differ, fresh lenses (reframings not used lately), and how "
      "to prove the chosen answer. Domains: design, code, math, writing, reason (plans, business, decisions), data. "
      "Use it for brainstorming, when the user asks for alternatives or something different, or before redoing work "
      "that came out generic. Then build the strongest option, not a list of options.",
      "thinking", [param("task", description="The task in a sentence, in the user's words."),
                   param("domain", description="design, code, math, writing, reason or data. Empty: guessed from "
                                               "the task.", required=False),
                   param("lenses", "int", "How many lenses to return (default 8).", required=False, default=8)],
      keywords="pikir lebih luas, think wider, brainstorm, ide lain, alternatif, cara lain, pendekatan lain, "
               "yang beda, jangan sama, kreatif, inovatif, lateral thinking",
      lead=True)
def think_wider(task: str, domain: str = "", lenses: int = 8) -> dict[str, Any]:
    data = divergence.catalog()
    wanted = (domain or "").strip().lower()
    if wanted and wanted not in data:
        raise ValueError(f"domain tidak dikenal: {wanted}. Pilih: {', '.join(sorted(data))}")
    domains = [wanted] if wanted else (divergence.domains_for(None, task) or ["reason"])
    entry = data[domains[0]]
    pool = [str(x) for x in entry.get("lenses") or []]
    recent = set(style_history.recent(f"think-lens-{domains[0]}"))
    fresh = [x for x in pool if x not in recent] or pool
    rng = random.SystemRandom()
    count = max(1, min(int(lenses or 8), len(pool)))
    picked = rng.sample(fresh, k=min(count, len(fresh)))
    if len(picked) < count:
        picked += rng.sample([x for x in pool if x not in picked], k=count - len(picked))
    for lens in picked:
        style_history.remember(f"think-lens-{domains[0]}", lens)
    return {
        "ok": True,
        "domain": domains[0],
        "also": domains[1:],
        "autopilot_to_beat": list(entry.get("autopilot") or []),
        "axes": list(entry.get("axes") or []),
        "lenses": picked,
        "proof": entry.get("proof", ""),
        "rule": ("Sketch at least three approaches that differ on two or more axes (one should feel risky), test them "
                 "against the lenses, pick or blend the strongest and say in one line why it beats the autopilot, then "
                 "build it fully and prove it as described. Show the user the result, not the brainstorm, unless they "
                 "asked for ideas."),
    }
