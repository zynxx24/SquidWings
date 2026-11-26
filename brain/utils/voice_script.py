"""A timed voice-over script in, one voice track that lands on its seconds out.

A video in code (Remotion, HyperFrames, the Studio) cuts its scenes and captions on the lines of its script, each line
with a range: ``[00.04 - 00.08] text`` (the house format of ``mic.txt``) or AI Studio's ``[00:00 - 00:03]`` with the
text on the lines under it. Speaking the whole script in one go drifts: by the last line the voice is seconds off its
scene. So every line is spoken on its own, its silence trimmed, sped up a little when it runs past its range (never
more than ``MAX_SPEEDUP``), and placed at its start second in one track as long as the video. The captions (``.srt``)
and word timings (``.words.json``) come from where each line really landed.

The script may carry delivery marks, read here and turned into direction for the voice, never spoken:

* ``(↑)`` rising, ``(↓)`` falling, ``(~)`` swinging: tone, folded into the line's note;
* ``(...)`` short pause, ``(//)`` long pause: become pauses in the spoken text;
* ``*word*`` stressed; ``[pelan] [senyum] [semangat] [bisik]`` (any word in brackets) how the line is said;
* a ``Gaya suara :`` (or ``Style:``) header is the note for every line;
* ``"SquidWings" dibaca: skuid-wings.`` (or ``pronounced``) says how a name sounds; the caption keeps the spelling.
"""

from __future__ import annotations

import array
import asyncio
import json
import re
import subprocess
import tempfile
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.utils import toolkit

RATE = 24000
MAX_SPEEDUP = 1.3
LEAD_S = 0.08
PARALLEL = 4
# Indonesian said casually runs about 2.5 words a second; past this even MAX_SPEEDUP cannot fit a line in its range.
WORDS_PER_S = 3.2
LEVEL_GAIN = 2.0          # loudness levelling moves a line at most this much either way
MIN_GAP_S = 0.05          # the breath left after a line that ran long into the next one
MIN_SIMILARITY = 0.55     # a line heard back less like its script than this was not spoken right
STRAY_S = 1.0             # a word heard this far from every line is speech in a gap
_STAMP = re.compile(r"\[\s*(\d{1,2})[.:](\d{2})(?:[.,](\d{1,3}))?\s*-\s*(\d{1,2})[.:](\d{2})(?:[.,](\d{1,3}))?\s*\]")
_CUE = re.compile(r"\((↑|↓|~|\.\.\.|…|//)\)")
_MOOD = re.compile(r"\[([A-Za-zÀ-ÿ ]{2,30})\]")
_STRESS = re.compile(r"\*([^*\n]{1,60})\*")
_SAY = re.compile(r"\"([^\"]{1,60})\"\s*(?:dibaca|pronounced|baca)\s*:?\s*([^.\"\n;]{1,60})", re.IGNORECASE)
_STYLE = re.compile(r"^\s*(gaya suara|gaya|style|voice style|nada)\s*:\s*(.+)$", re.IGNORECASE)
_RULE = re.compile(r"^\s*(-{5,}|={5,}|catatan|notes?\b)", re.IGNORECASE)
_TONE = {"↑": "nada naik", "↓": "nada turun dan kalem", "~": "nada mengayun santai"}


@dataclass
class Line:
    start: float
    end: float | None
    text: str                     # as shown in captions
    spoken: str = ""              # as sent to the voice (pauses, pronunciations)
    note: str = ""                # how this line is said
    placed: tuple[float, float] | None = None
    speedup: float = 1.0
    shifted: float = 0.0           # seconds placed late because the line before ran long
    words: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Script:
    lines: list[Line]
    style: str = ""
    pronounce: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> float:
        ends = [ln.end if ln.end is not None else ln.start for ln in self.lines]
        return max(ends) if ends else 0.0


def _seconds(minutes: str, secs: str, frac: str | None) -> float:
    return int(minutes) * 60 + int(secs) + (int(frac) / 10 ** len(frac) if frac else 0.0)


def _clean(text: str) -> str:
    text = _CUE.sub(" ", text)
    text = _MOOD.sub(" ", text)
    text = _STRESS.sub(r"\1", text)
    return re.sub(r"\s+([,.!?…])", r"\1", " ".join(text.split())).strip()


def _spoken(text: str, pronounce: dict[str, str]) -> str:
    text = text.replace("(//)", " ... ").replace("(...)", " ... ").replace("(…)", " ... ")
    text = _clean(text)
    for word, sound in pronounce.items():
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", sound, text)
    return text


def _note(raw: str, style: str) -> str:
    moods = [m.strip().lower() for m in _MOOD.findall(raw)]
    tones = [_TONE[c] for c in _CUE.findall(raw) if c in _TONE]
    stressed = _STRESS.findall(raw)
    bits = [style] if style else []
    if moods:
        bits.append("Baris ini diucapkan " + ", ".join(dict.fromkeys(moods)) + ".")
    if tones:
        bits.append("Ikuti alur nada: " + ", lalu ".join(tones) + ".")
    if stressed:
        bits.append("Tekankan kata: " + ", ".join(stressed) + ".")
    return " ".join(bits)


def parse(text: str) -> Script:
    """Read a voice-over script. Without any time ranges the whole text is one line, spoken from second 0."""
    style_lines: list[str] = []
    pronounce = {word.strip(): sound.strip() for word, sound in _SAY.findall(text)}
    rows = text.splitlines()
    for i, row in enumerate(rows):
        match = _STYLE.match(row)
        if match:
            style_lines.append(match.group(2).strip())
            for follow in rows[i + 1:]:
                if follow.startswith((" ", "\t")) and follow.strip() and not _STAMP.search(follow):
                    style_lines.append(follow.strip())
                else:
                    break
    style = " ".join(style_lines)
    lines: list[Line] = []
    current: dict[str, Any] | None = None
    for row in rows:
        # A range opens a line only at the start of a row; one inside a sentence is an explanation, not a cue.
        stamp = _STAMP.match(row.lstrip())
        if stamp:
            row = row.lstrip()
            if current:
                lines.append(current["line"])
            g = stamp.groups()
            start, end = _seconds(g[0], g[1], g[2]), _seconds(g[3], g[4], g[5])
            raw = row[stamp.end():].strip()
            current = {"raw": raw, "line": Line(start, end, "")}
            current["line"].text = raw
            continue
        if current is None:
            continue
        if _RULE.match(row):
            lines.append(current["line"])
            current = None
            continue
        if row.strip():
            current["line"].text = f"{current['line'].text} {row.strip()}".strip()
    if current:
        lines.append(current["line"])
    if not lines:
        body = "\n".join(r for r in rows if r.strip() and not _STYLE.match(r) and not _SAY.search(r))
        lines = [Line(0.0, None, body)]
    for line in lines:
        raw = line.text
        line.note = _note(raw, style)
        line.spoken = _spoken(raw, pronounce)
        line.text = _clean(raw)
    return Script([ln for ln in lines if ln.spoken], style, pronounce)


def _ffmpeg() -> str:
    exe = toolkit.ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg tidak ada. Pasang ffmpeg (atau paket imageio-ffmpeg).")
    return exe


def _normalise(source: Path, dest: Path, speed: float = 1.0) -> None:
    """Mono 24 kHz 16-bit, silence trimmed at both ends, optionally faster (pitch kept)."""
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,"
            "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse")
    chain = trim + (f",atempo={speed:.3f}" if abs(speed - 1.0) > 0.005 else "")
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(source), "-af", chain, "-ac", "1", "-ar",
                    str(RATE), "-sample_fmt", "s16", str(dest)], check=True, capture_output=True, timeout=300)


def _duration(path: Path) -> float:
    with wave.open(str(path)) as clip:
        frames: int = clip.getnframes()
        rate: int = clip.getframerate()
    return frames / float(rate)


def _samples(path: Path) -> array.array:
    with wave.open(str(path)) as clip:
        data = array.array("h")
        data.frombytes(clip.readframes(clip.getnframes()))
    return data


def _words(line: Line) -> list[dict[str, Any]]:
    """Word timings spread over where the line was placed, by length: good enough for captions that pop in."""
    tokens = line.text.split()
    if not tokens or not line.placed:
        return []
    start, end = line.placed
    weights = [len(t) + 1 for t in tokens]
    total = sum(weights)
    out, at = [], start
    for token, weight in zip(tokens, weights, strict=True):
        span = (end - start) * weight / total
        out.append({"word": token, "start": round(at, 3), "end": round(at + span, 3)})
        at += span
    return out


def _stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _cut(source: Path, dest: Path, begin: float, finish: float) -> None:
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(source), "-ss", f"{max(0.0, begin):.3f}",
                    "-to", f"{finish:.3f}", "-ac", "1", "-ar", str(RATE), "-sample_fmt", "s16", str(dest)],
                   check=True, capture_output=True, timeout=300)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def boundaries(lines: list[Line], heard: list[tuple[str, float, float]], length: float) -> list[tuple[float, float]]:
    """Where each script line sits in one take, as (start, end) seconds that cover the whole take with no gaps.

    The script's words and the heard words (whisper: text, start, end) are matched in order with difflib, which
    survives a word heard wrong. Between two lines the heard words that matched nothing (a name, a misheard word)
    are shared out by how many of the script's words each line still misses: "ini. AppCash." keeps "AppCash" when
    the next line starts with a word that did match. The cut then goes in the pause after that word, so every
    spoken word ends up in one line and none is cut in half or lost."""
    import difflib

    words = [(w, a, b) for text, a, b in heard for w in (_tokens(text) or [text.strip().lower()])]
    if not words:
        step = length / max(1, len(lines))
        return [(i * step, (i + 1) * step) for i in range(len(lines))]
    script_words = [(i, w) for i, ln in enumerate(lines) for w in _tokens(ln.spoken)]
    first_of: dict[int, int] = {}
    last_of: dict[int, int] = {}
    for index, (line_index, _) in enumerate(script_words):
        first_of.setdefault(line_index, index)
        last_of[line_index] = index
    matcher = difflib.SequenceMatcher(None, [w for _, w in script_words], [w for w, _, _ in words], autojunk=False)
    pairs: dict[int, list[tuple[int, int]]] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            pairs.setdefault(script_words[block.a + k][0], []).append((block.a + k, block.b + k))
    cuts: list[float] = []
    floor = 0
    for i in range(len(lines) - 1):
        mine = pairs.get(i) or []
        following = next((j for j in range(i + 1, len(lines)) if pairs.get(j)), None)
        theirs = pairs.get(following) if following is not None else []
        lo = max([floor - 1, *[h for _, h in mine]]) if mine else floor - 1
        hi = min(h for _, h in theirs) if theirs else len(words)
        between = max(0, hi - lo - 1)
        tail = (last_of.get(i, 0) - max(sp for sp, _ in mine)) if mine else len(_tokens(lines[i].spoken))
        head = (min(sp for sp, _ in theirs) - first_of.get(following, 0)) if theirs and following is not None else 0
        share = tail / (tail + head) if tail + head else 0.5
        expected = lo + round(between * share)      # the last heard word of line i, by the count
        expected = max(floor - 1, min(expected, len(words) - 2))
        # A line break is usually the longest breath nearby: move the cut one word either way when that pause is
        # clearly longer than the one the count points at. Never before a word matched to this line, nor past one
        # matched to the next: "lunas. Beres." keeps "Beres" even when the breath before it is the longer one.
        def pause(k: int) -> float:
            return words[k + 1][1] - words[k][2] if 0 <= k < len(words) - 1 else -1.0
        after = max((k for k in (expected - 1, expected, expected + 1) if lo <= k <= min(hi - 1, len(words) - 2)),
                    key=lambda k: pause(k) - 0.12 * abs(k - expected), default=expected)
        if after < 0:
            cut = max(0.0, words[0][1] - 0.05)
        else:
            cut = (words[after][2] + words[after + 1][1]) / 2
        cuts.append(max(cut, cuts[-1] if cuts else 0.0))
        floor = after + 1
    edges = [max(0.0, words[0][1] - 0.15), *cuts, length]
    return [(edges[i], edges[i + 1]) for i in range(len(lines))]


def _quietest(samples: array.array, around: float, reach: float = 0.5) -> float:
    """Move a cut into the silence that best fits it: long (a breath between lines) and near the cut whisper chose.
    Whisper's word times smear into silence, the waveform does not; but a pause inside a sentence can be long too,
    so distance from the chosen cut counts against a silence (score = length - 0.8 x distance)."""
    frame = RATE // 100                                     # 10 ms
    first = max(0, int((around - reach) * RATE) // frame)
    last = min(len(samples) // frame, int((around + reach) * RATE) // frame)
    if last - first < 3:
        return around
    levels = []
    for f in range(first, last):
        chunk = samples[f * frame:(f + 1) * frame]
        levels.append(sum(abs(x) for x in chunk) / max(1, len(chunk)))
    threshold = max(min(levels) * 3.0, max(levels) * 0.04, 60.0)
    runs: list[tuple[int, int]] = []
    run_start = None
    for i, level in enumerate([*levels, threshold + 1]):
        if level <= threshold and run_start is None:
            run_start = i
        elif level > threshold and run_start is not None:
            runs.append((run_start, i))
            run_start = None
    if not runs:
        return around
    centre = (around * RATE / frame) - first

    def score(run: tuple[int, int]) -> float:
        distance = 0.0 if run[0] <= centre <= run[1] else min(abs(run[0] - centre), abs(run[1] - centre))
        return (run[1] - run[0]) / 100 - 0.8 * distance / 100

    best = max(runs, key=score)
    return (first + (best[0] + best[1]) / 2) * frame / RATE


def take_path(dest: Path) -> Path:
    """Where the whole take of a track is kept, so its lines can be cut again without a new request."""
    return dest.with_name(f"{dest.stem}.take.wav")


Synth = Callable[[str, str], Awaitable[bytes | Path]]


async def _per_line(lines: list[Line], synth: Synth, folder: Path, parallel: int, tag: str = "raw") -> list[Path]:
    gate = asyncio.Semaphore(max(1, parallel))

    async def one(index: int, line: Line) -> Path:
        async with gate:
            made = await synth(line.spoken, line.note)
        raw = made if isinstance(made, Path) else folder / f"{tag}{index}.audio"
        if not isinstance(made, Path):
            raw.write_bytes(made)
        return raw

    return list(await asyncio.gather(*(one(i, ln) for i, ln in enumerate(lines))))


async def _whole_take(script: Script, synth: Synth | None, folder: Path, language: str, keep: Path) -> list[Path]:
    """The whole script in one request (one voice, one breath, one quota unit), then cut into its lines. The take
    is kept at ``keep``; without ``synth`` that kept take is cut again, with no new request."""

    take = folder / "take.wav"
    if synth is None:
        if not keep.is_file():
            raise ValueError(f"Belum ada rekaman utuh ({keep.name}) untuk dipotong ulang. Buat dulu dengan "
                             "mode whole.")
        await asyncio.to_thread(_cut, keep, take, 0.0, 10_000.0)
        return await _cut_take(script, take, folder, language)
    text = "\n\n".join(ln.spoken for ln in script.lines)
    notes = [f"Baris {i}: {ln.note[len(script.style):].strip()}" for i, ln in enumerate(script.lines, 1)
             if ln.note[len(script.style):].strip()]
    direction = " ".join([script.style, "Beri jeda napas di antara baris.", *notes]).strip()
    made = await synth(text, direction)
    raw = made if isinstance(made, Path) else folder / "take.audio"
    if not isinstance(made, Path):
        raw.write_bytes(made)
    await asyncio.to_thread(_cut, raw, take, 0.0, 10_000.0)
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(take.read_bytes())
    return await _cut_take(script, take, folder, language)


async def _cut_take(script: Script, take: Path, folder: Path, language: str) -> list[Path]:
    from brain.utils import whisper_stt

    if not whisper_stt.available():
        raise RuntimeError("Memotong rekaman utuh per baris butuh faster-whisper. Pasang faster-whisper, atau pakai "
                           "mode per_line.")
    result = await asyncio.to_thread(whisper_stt.transcribe, take, language, words=True)
    heard = [(w.text, w.start, w.end) for seg in result.segments for w in seg.words]
    spans = boundaries(script.lines, heard, _duration(take))
    samples = _samples(take)
    edges = [spans[0][0], *[_quietest(samples, end) for _, end in spans[:-1]], spans[-1][1]]
    spans = [(edges[i], max(edges[i] + 0.05, edges[i + 1])) for i in range(len(spans))]
    clips = []
    for i, (begin, finish) in enumerate(spans):
        clip = folder / f"take{i}.wav"
        await asyncio.to_thread(_cut, take, clip, begin, finish)
        clips.append(clip)
    return clips


# --- Script problems, levelling, placing --------------------------------------


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}.{int(seconds) % 60:02d}"


def problems(script: Script, total: float | None = None) -> list[str]:
    """What in a script will go wrong before a request is spent: ranges reversed, overlapping or past the video, and
    lines with more words than their range holds even at MAX_SPEEDUP."""
    out: list[str] = []
    timed = sorted((ln for ln in script.lines if ln.end is not None), key=lambda ln: ln.start)
    for i, line in enumerate(timed):
        assert line.end is not None
        label = f"[{_clock(line.start)} - {_clock(line.end)}]"
        slot = line.end - line.start
        if slot <= 0:
            out.append(f"{label} selesai sebelum mulai: rentangnya terbalik atau nol.")
            continue
        count = len(_tokens(line.text))
        if count / max(0.3, slot - LEAD_S) > WORDS_PER_S:
            out.append(f"{label} berisi {count} kata untuk {slot:g} detik: tidak muat walau dipercepat "
                       f"{MAX_SPEEDUP}x. Pendekkan teksnya atau lebarkan rentangnya.")
        previous = timed[i - 1].end if i else None
        if previous is not None and line.start < previous - 0.01:
            out.append(f"{label} mulai sebelum baris sebelumnya selesai: rentangnya tumpang tindih.")
        if total and line.end > total + 0.01:
            out.append(f"{label} melewati panjang video ({total:g} detik).")
    return out


def _level(clips: list[Path]) -> None:
    """Even out loudness between lines (each spoken apart can come back louder or softer): every clip is scaled
    towards the median level, at most LEVEL_GAIN either way and never past full scale."""
    levels: list[float] = []
    for clip in clips:
        data = _samples(clip)
        loud = [abs(x) for x in data if abs(x) > 300]
        levels.append((sum(x * x for x in loud) / len(loud)) ** 0.5 if loud else 0.0)
    heard = sorted(x for x in levels if x)
    if len(heard) < 2:
        return
    target = heard[len(heard) // 2]
    for clip, level in zip(clips, levels, strict=True):
        if not level:
            continue
        data = _samples(clip)
        peak = max(1, max(abs(x) for x in data))
        gain = min(LEVEL_GAIN, max(1 / LEVEL_GAIN, target / level), 32000 / peak)
        if abs(gain - 1.0) < 0.05:
            continue
        scaled = array.array("h", (int(x * gain) for x in data))
        with wave.open(str(clip), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(scaled.tobytes())


async def _assemble(script: Script, raws: list[Path], folder: Path, dest: Path, total: float | None, round_no: int) -> float:
    """Fit every raw line to its range, place it on its second and write the track. Returns its length in seconds."""

    async def fit(index: int, line: Line, raw: Path) -> Path:
        clean = folder / f"line{round_no}_{index}.wav"
        await asyncio.to_thread(_normalise, raw, clean)
        slot = (line.end - line.start) if line.end is not None else None
        length = _duration(clean)
        line.speedup = 1.0
        if slot and length > slot - LEAD_S:
            # Runs past its range: faster, within reason; past that it spills over rather than being cut off.
            line.speedup = min(MAX_SPEEDUP, length / max(0.3, slot - LEAD_S))
            fitted = folder / f"fit{round_no}_{index}.wav"
            await asyncio.to_thread(_normalise, raw, fitted, line.speedup)
            clean = fitted
        return clean

    clips = list(await asyncio.gather(*(fit(i, ln, raw) for i, (ln, raw) in enumerate(zip(script.lines, raws,
                                                                                            strict=True)))))
    await asyncio.to_thread(_level, clips)
    # Each line starts on its second. One that still runs long would talk over the next, so the next waits for it
    # (reported in ``shifted``): two voices at once is worse than a line a moment late.
    cursor = 0.0
    previous_end = None
    for line, clip in zip(script.lines, clips, strict=True):
        begin = line.start + LEAD_S if line.end is not None else cursor
        line.shifted = 0.0
        if previous_end is not None and begin < previous_end:
            line.shifted = previous_end + MIN_GAP_S - begin
            begin = previous_end + MIN_GAP_S
        line.placed = (begin, begin + _duration(clip))
        previous_end = line.placed[1]
        cursor = line.placed[1] + 0.35
    length = max(total or 0.0, script.total, max(ln.placed[1] for ln in script.lines if ln.placed) + 0.2)
    track = array.array("h", bytes(int(length * RATE) * 2))
    for line, clip in zip(script.lines, clips, strict=True):
        assert line.placed is not None
        offset = int(line.placed[0] * RATE)
        for i, sample in enumerate(_samples(clip)):
            at = offset + i
            if at >= len(track):
                break
            track[at] = max(-32768, min(32767, track[at] + sample))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(track.tobytes())
    return length


# --- Listening back -------------------------------------------------------------


def _ratio(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


def assign(lines: list[Line], heard: list[tuple[str, float, float]]) -> tuple[list[list[str]], list[str]]:
    """The heard words of each placed line, and the words heard outside every line. A word goes by where it ENDS:
    whisper stretches a word's start back into the silence before it (by up to a second), so its start, midpoint
    and even its overlap can fall in the line before, but its end sits where it was said."""
    per_line: list[list[str]] = [[] for _ in lines]
    stray: list[str] = []
    placed = [(i, ln.placed) for i, ln in enumerate(lines) if ln.placed]
    for text, _start, end in heard:
        point = end - 0.05
        best, best_distance = -1, 1e9
        for i, (a, b) in placed:
            distance = 0.0 if a <= point <= b else min(abs(point - a), abs(point - b))
            if distance < best_distance:
                best, best_distance = i, distance
        if best < 0 or best_distance > STRAY_S:
            stray.append(text.strip())
        else:
            per_line[best].append(text.strip())
    return per_line, stray


def issues(lines: list[Line], heard: list[tuple[str, float, float]]) -> dict[str, Any]:
    """Listen to the finished track (whisper words) against the script: a line not heard (Gemini skipped it or a
    cut lost it), and a word cut into the neighbouring line. Names misheard by whisper pass: a line is compared in
    both its caption and spoken spelling, on letters, so 'App Cache' still matches 'AppCash'."""
    per_line, stray = assign(lines, heard)
    found: list[dict[str, Any]] = []

    def tokens(words: list[str]) -> list[str]:
        return [t for w in words for t in _tokens(w)]

    def similar(line: Line, words: list[str]) -> float:
        got = " ".join(tokens(words))
        return max(_ratio(" ".join(_tokens(form)), got) for form in (line.text, line.spoken))

    def same(a: str, b: str) -> bool:
        return bool(a and b) and _ratio(a, b) >= 0.75

    for i, line in enumerate(lines):
        score = similar(line, per_line[i])
        if score < MIN_SIMILARITY:
            found.append({"line": i, "kind": "tidak_terdengar" if not per_line[i] else "beda",
                          "text": line.text, "heard": " ".join(per_line[i]), "similarity": round(score, 2)})
    for i in range(len(lines) - 1):
        mine, theirs = tokens(per_line[i]), tokens(per_line[i + 1])
        my_script, their_script = _tokens(lines[i].text), _tokens(lines[i + 1].text)
        if not (my_script and their_script):
            continue
        if theirs and same(theirs[0], my_script[-1]) and not same(theirs[0], their_script[0]) \
                and not (mine and same(mine[-1], my_script[-1])):
            found.append({"line": i + 1, "kind": "kata_pindah", "word": theirs[0], "belongs_to": i,
                          "text": lines[i + 1].text})
        if mine and same(mine[-1], their_script[0]) and not same(mine[-1], my_script[-1]) \
                and not (theirs and same(theirs[0], their_script[0])):
            found.append({"line": i, "kind": "kata_pindah", "word": mine[-1], "belongs_to": i + 1,
                          "text": lines[i].text})
    return {"ok": not found, "issues": found, "stray": stray}


async def check_track(script: Script, track: Path, language: str) -> dict[str, Any]:
    from brain.utils import whisper_stt

    result = await asyncio.to_thread(whisper_stt.transcribe, track, language, words=True)
    heard = [(w.text, w.start, w.end) for seg in result.segments for w in seg.words]
    return issues(script.lines, heard)


def _to_redo(found: dict[str, Any]) -> list[int]:
    redo: set[int] = set()
    for issue in found["issues"]:
        redo.add(int(issue["line"]))
        if "belongs_to" in issue:
            redo.add(int(issue["belongs_to"]))
    return sorted(redo)


async def build(script: Script, synth: Synth | None, dest: Path, total: float | None = None, *,
                whole_take: bool = False, parallel: int = PARALLEL, language: str = "id",
                check: bool = False) -> dict[str, Any]:
    """Speak the script with ``synth(text, note)`` (audio bytes, or a path to an audio file), fit every line to its
    range, and write ``dest`` (WAV, 24 kHz mono) plus ``.srt`` and ``.words.json`` beside it.

    ``whole_take`` speaks the whole script in one request and cuts it into lines with local whisper (fewer requests,
    one continuous delivery); otherwise every line is its own request, ``parallel`` at a time. Without ``synth`` the
    kept take is cut again. With ``check`` the finished track is listened back to (whisper); a line not heard right
    or a word cut into its neighbour is spoken again on its own, once, and the track rebuilt."""
    if not script.lines:
        raise ValueError("naskah kosong")
    repaired: list[str] = []
    repair_failed = ""
    verdict: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(prefix="voice-track-") as tmp:
        folder = Path(tmp)
        if whole_take or synth is None:
            raws = await _whole_take(script, synth, folder, language, take_path(dest))
        else:
            raws = await _per_line(script.lines, synth, folder, parallel)
        length = await _assemble(script, raws, folder, dest, total, 0)
        if check:
            verdict = await check_track(script, dest, language)
            redo = _to_redo(verdict)
            if redo and synth is not None:
                try:
                    again = await _per_line([script.lines[i] for i in redo], synth, folder, parallel, "again")
                except (RuntimeError, OSError) as exc:      # the quota ran out: keep the track, say what is left
                    repair_failed = str(exc)
                else:
                    for i, raw in zip(redo, again, strict=True):
                        raws[i] = raw
                    length = await _assemble(script, raws, folder, dest, total, 1)
                    repaired = [script.lines[i].text for i in redo]
                    verdict = await check_track(script, dest, language)
    for line in script.lines:
        line.words = _words(line)
    srt = "\n".join(f"{i}\n{_stamp(ln.placed[0])} --> {_stamp(ln.placed[1])}\n{ln.text}\n"
                    for i, ln in enumerate(script.lines, 1) if ln.placed)
    dest.with_suffix(".srt").write_text(srt, encoding="utf-8")
    dest.with_suffix(".words.json").write_text(
        json.dumps([w for ln in script.lines for w in ln.words], ensure_ascii=False), encoding="utf-8")
    spills = [ln for ln in script.lines if ln.end is not None and ln.placed and ln.placed[1] > ln.end + 0.05]
    report: dict[str, Any] = {
        "duration_s": round(length, 2),
        "mode": "recut" if synth is None else "whole take" if whole_take else "per line",
        "lines": [{"range": [ln.start, ln.end], "placed": [round(ln.placed[0], 2), round(ln.placed[1], 2)],
                   "speedup": round(ln.speedup, 2), "text": ln.text} for ln in script.lines if ln.placed],
        "spills_over": [ln.text for ln in spills],
        "shifted": [{"text": ln.text, "late_s": round(ln.shifted, 2)} for ln in script.lines if ln.shifted > 0.01],
        "captions_srt": dest.with_suffix(".srt").name,
        "words_json": dest.with_suffix(".words.json").name,
    }
    if verdict is not None:
        report["check"] = verdict
    if repaired:
        report["repaired"] = repaired
    if repair_failed:
        report["repair_failed"] = repair_failed
    return report


def media_seconds(path: Path) -> float | None:
    """A media file's length from ffmpeg's header line, or None when ffmpeg cannot read it."""
    probe = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
    if not match:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def voice_end(path: Path, floor: int = 300) -> float:
    """Where the last spoken sound of a WAV track ends (its trailing silence ignored)."""
    data = _samples(path)
    with wave.open(str(path)) as clip:
        rate: int = clip.getframerate()
    for i in range(len(data) - 1, -1, -1):
        if abs(data[i]) > floor:
            return (i + 1) / float(rate)
    return 0.0


def add_to_video(video: Path, voice: Path, dest: Path, music_volume: float = 0.35) -> None:
    """The video with the voice track over its own sound: the music ducks under the voice and comes back between
    lines (sidechain), so nothing has to be re-rendered. A video without sound gets the voice alone."""
    probe = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True, timeout=60)
    has_audio = "Audio:" in (probe.stderr or "")
    if has_audio:
        graph = (f"[0:a]volume={music_volume:.2f}[bed];[1:a]asplit=2[vo][key];"
                 "[bed][key]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[ducked];"
                 "[ducked][vo]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
        argv = [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(voice), "-filter_complex", graph,
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(dest)]
    else:
        argv = [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(voice), "-map", "0:v", "-map",
                "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(dest)]
    subprocess.run(argv, check=True, capture_output=True, timeout=900)
