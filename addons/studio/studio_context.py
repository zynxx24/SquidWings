"""The Studio's own harness: what a message sent from the video editor brings with it.

A message typed in the Studio's chat panel is about one video, often one layer at one moment
("geser logo ini ke kiri", "judul di detik 2 terlalu cepat"). The page sends where the user is:
project, playhead time, selected layer, a box drawn on the frame. The orchestrator then runs that
turn as a Studio turn: the studio skill is awake, its tools are offered, the operating protocol
for editing video data is in the prompt, and so is a compact picture of the video at that moment.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from addons.studio import studio_doc

_current: ContextVar[dict[str, Any] | None] = ContextVar("studio_context", default=None)
_PROTOCOL = Path(__file__).resolve().parent / "harness_studio.txt"
_MAX_LAYER_JSON = 4_000
_MAX_BLOCK = 9_000


def clean(raw: Any) -> dict[str, Any] | None:
    """The Studio fields of a chat message, checked. None when there are none."""
    if not isinstance(raw, dict) or not studio_doc.NAME_RE.match(str(raw.get("project") or "")):
        return None
    out: dict[str, Any] = {"project": raw["project"]}
    try:
        out["t"] = max(0.0, float(raw.get("t") or 0))
    except (TypeError, ValueError):
        out["t"] = 0.0
    if isinstance(raw.get("layer"), str) and studio_doc.ID_RE.match(raw["layer"]):
        out["layer"] = raw["layer"]
    box = raw.get("box")
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        out["box"] = [round(float(v)) for v in box]
    return out


def set_current(ctx: dict[str, Any] | None) -> Any:
    return _current.set(ctx)


def reset(token: Any) -> None:
    _current.reset(token)


def current() -> dict[str, Any] | None:
    return _current.get()


def _clock(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"


def prompt_block(workspace: str | Path) -> str:
    """Protocol plus the video as it is now, for the system prompt. "" when this is no Studio turn."""
    ctx = current()
    if not ctx:
        return ""
    try:
        protocol = _PROTOCOL.read_text(encoding="utf-8").strip()
    except OSError:
        protocol = ""
    name = ctx["project"]
    try:
        doc, stamp = studio_doc.load(workspace, name)
    except studio_doc.StudioError as exc:
        return f"\n\n{protocol}\n\n[STUDIO] Proyek {name} tidak bisa dibuka: {exc}"
    t = ctx.get("t", 0.0)
    lines = [
        f"[STUDIO] Pengguna mengetik ini dari editor video, proyek \"{name}\" (studio/{name}/motion.json, stamp {stamp}).",
        f"Video {doc.get('width')}x{doc.get('height')}, {doc.get('fps')} fps, {doc.get('duration')} dtk. Playhead di {_clock(t)}.",
    ]
    if ctx.get("box"):
        x, y, w, h = ctx["box"]
        lines.append(f"Pengguna menunjuk kotak x{x} y{y} {w}x{h} di frame itu. \"Ini\", \"di sini\" berarti bagian itu.")
    lines.append("Lapisan (bawah ke atas): id, tipe, waktu tampil, posisi di playhead, properti beranimasi:")
    for layer in doc.get("layers") or []:
        visible = (layer.get("start") or 0) <= t < (layer.get("end") or doc.get("duration") or 0)
        x, y = studio_doc._at(layer, "x", t, 0), studio_doc._at(layer, "y", t, 0)
        label = f" \"{str(layer.get('text'))[:40]}\"" if layer.get("type") == "text" else ""
        lines.append(
            f"- {layer.get('id')} {layer.get('type')}{label} {layer.get('start', 0)}-{layer.get('end', '')}s"
            f"{' (tampil sekarang)' if visible else ''} x{float(x):.0f} y{float(y):.0f}"
            f" anim: {', '.join(sorted((layer.get('keys') or {}).keys())) or '-'}")
    selected = next((layer for layer in doc.get("layers") or [] if layer.get("id") == ctx.get("layer")), None)
    if selected:
        body = json.dumps(selected, ensure_ascii=False)
        lines.append(f"Lapisan terpilih (\"ini\" = lapisan ini kecuali dikatakan lain):\n{body[:_MAX_LAYER_JSON]}")
    notes = studio_doc.open_notes(doc)
    if notes:
        lines.append("Catatan terbuka: " + "; ".join(f"#{n.get('n')} {_clock(float(n.get('t') or 0))} {n.get('text')}" for n in notes[:12]))
    if doc.get("audio"):
        lines.append("Audio: " + ", ".join(f"{a.get('id')} ({a.get('src')}, mulai {a.get('start', 0)}s)" for a in doc["audio"]))
    block = "\n".join(lines)[:_MAX_BLOCK]
    return f"\n\n{protocol}\n\n{block}"


STUDIO_TOOLS = ("studio_get", "studio_save", "studio_check", "studio_still", "studio_notes", "studio_note_done",
                "style_pick", "contrast_check")

__all__ = ["STUDIO_TOOLS", "clean", "current", "prompt_block", "reset", "set_current"]
