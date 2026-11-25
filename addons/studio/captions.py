"""Captions from speech: Whisper's word timings turned into Studio text layers.

``captions(doc, project, source)`` transcribes the music/voice clip or the video clip ``source``
(default: the first clip that has sound), maps every word from the file's own time to the video's
timeline (start, offset, speed), groups words into short lines (a few words, broken at pauses and
punctuation), and adds one text layer per line with a small pop-in. Running it again replaces the
captions it made before (layer ids ``cap-*``), and keeps everything else.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from addons.studio import renderer

PREFIX = "cap-"
_BREAK_AFTER = re.compile(r"[.!?,;:]$")


def _sources(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Clips with sound, as {src, start, offset, rate, end} on the timeline."""
    duration = float(doc.get("duration") or 1)
    found = []
    for clip in doc.get("audio") or []:
        if clip.get("src") and not clip.get("muted"):
            found.append({"id": clip.get("id"), "src": clip["src"], "start": float(clip.get("start") or 0),
                          "offset": float(clip.get("offset") or 0), "rate": 1.0, "end": duration})
    for layer in doc.get("layers") or []:
        if layer.get("type") == "video" and layer.get("src") and not layer.get("muted"):
            found.append({"id": layer.get("id"), "src": layer["src"], "start": float(layer.get("start") or 0),
                          "offset": float(layer.get("clipStart") or 0), "rate": float(layer.get("speed") or 1) or 1,
                          "end": float(layer.get("end") if layer.get("end") is not None else duration)})
    return found


def group_words(words: list[tuple[float, float, str]], max_words: int = 4, gap: float = 0.6) -> list[tuple[float, float, str]]:
    """(start, end, text) lines: at most max_words, broken at pauses longer than gap and after punctuation."""
    lines: list[tuple[float, float, str]] = []
    current: list[tuple[float, float, str]] = []
    for word in words:
        if current and (len(current) >= max_words or word[0] - current[-1][1] > gap or _BREAK_AFTER.search(current[-1][2])):
            lines.append((current[0][0], current[-1][1], " ".join(w[2] for w in current)))
            current = []
        current.append(word)
    if current:
        lines.append((current[0][0], current[-1][1], " ".join(w[2] for w in current)))
    return lines


def caption_layers(doc: dict[str, Any], lines: list[tuple[float, float, str]], position: str = "bottom",
                   font: str = "") -> list[dict[str, Any]]:
    width, height = float(doc["width"]), float(doc["height"])
    size = round(min(width, height) * 0.075)
    y = {"top": height * 0.18, "middle": height * 0.5}.get(position, height * 0.78)
    family = font or next(iter(doc.get("fonts") or []), "Plus Jakarta Sans")
    duration = float(doc.get("duration") or 1)
    layers = []
    for i, (start, end, text) in enumerate(lines, 1):
        end = min(duration, max(end, start + 0.35) + 0.1)
        if i < len(lines):
            end = min(end, lines[i][0])   # never two captions on screen at once
        layers.append({
            "id": f"{PREFIX}{i}", "type": "text", "name": f"Caption {i}", "text": text, "font": family, "weight": 800,
            "size": size, "fill": "#FFFFFF", "stroke": "#111111", "strokeWidth": max(2, round(size * 0.14)),
            "align": "center", "width": round(width * 0.86), "lineHeight": 1.1,
            "props": {"x": width / 2, "y": y, "scale": 1, "rotation": 0, "opacity": 1},
            "keys": {"scale": [{"t": round(start, 3), "v": 0.82, "ease": "outBack"}, {"t": round(start + 0.16, 3), "v": 1}],
                     "opacity": [{"t": round(start, 3), "v": 0, "ease": "out"}, {"t": round(start + 0.08, 3), "v": 1}]},
            "start": round(start, 3), "end": round(end, 3),
        })
    return layers


def captions(doc: dict[str, Any], project: Path, source: str = "", language: str = "", max_words: int = 4,
             position: str = "bottom", font: str = "") -> tuple[dict[str, Any], int, str]:
    """The document with fresh captions, how many lines, and which clip they came from."""
    from brain.utils import whisper_stt

    clips = _sources(doc)
    if source:
        clips = [c for c in clips if source in (c.get("id"), c.get("src"))]
    clips = [c for c in clips if (project / c["src"]).is_file() and renderer.probe(project / c["src"]).audio]
    if not clips:
        raise renderer.RenderError("Tidak ada klip bersuara untuk dibuatkan caption (musik/voice di Audio, atau klip video).")
    clip = clips[0]
    transcript = whisper_stt.transcribe(project / clip["src"], language, words=True)
    words: list[tuple[float, float, str]] = []
    for segment in transcript.segments:
        for w in segment.words or []:
            start = clip["start"] + (float(w.start) - clip["offset"]) / clip["rate"]
            end = clip["start"] + (float(w.end) - clip["offset"]) / clip["rate"]
            if end <= clip["start"] or start >= clip["end"] or not w.text:
                continue
            words.append((float(max(clip["start"], start)), float(min(clip["end"], end)), str(w.text).strip()))
    lines = group_words(words, max_words=max(1, int(max_words)))
    kept = [layer for layer in doc.get("layers") or [] if not str(layer.get("id", "")).startswith(PREFIX)]
    new_doc = {**doc, "layers": kept + caption_layers(doc, lines, position, font)}
    if font and font not in (new_doc.get("fonts") or []):
        new_doc["fonts"] = [*(new_doc.get("fonts") or []), font]
    return new_doc, len(lines), str(clip["src"])


__all__ = ["captions", "caption_layers", "group_words"]
