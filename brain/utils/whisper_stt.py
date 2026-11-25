"""Local speech to text with faster-whisper, shared by ``/voice``, uploads and the ``transcribe`` tool.

Everything runs on this machine: the audio never leaves it. The model is downloaded once (to the
Hugging Face cache) the first time it is used, then loaded once per process and kept.

* ``VOICE_STT_MODEL``: tiny, base (default), small, medium, large-v3, turbo... Bigger is slower
  and more accurate; ``small`` is a good step up for Indonesian.
* ``VOICE_STT_DEVICE``: auto (default: a CUDA GPU when there is one, else the CPU), cpu, or cuda.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "base"
INSTALL = "pip install faster-whisper"

_models: dict[tuple[str, str], Any] = {}
_lock = threading.Lock()


class STTError(RuntimeError):
    pass


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    text: str
    language: str
    language_probability: float
    duration: float
    segments: list[Segment]


def available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def model_name(override: str = "") -> str:
    return (override or os.getenv("VOICE_STT_MODEL", "")).strip() or DEFAULT_MODEL


def _device() -> tuple[str, str]:
    wanted = os.getenv("VOICE_STT_DEVICE", "").strip().lower() or "auto"
    if wanted == "cpu":
        return "cpu", "int8"
    try:
        import ctranslate2

        has_gpu = ctranslate2.get_cuda_device_count() > 0
    except (ImportError, RuntimeError, OSError):
        has_gpu = False
    if wanted == "cuda" and not has_gpu:
        raise STTError("VOICE_STT_DEVICE=cuda, tetapi tidak ada GPU CUDA yang terbaca.")
    return ("cuda", "float16") if has_gpu else ("cpu", "int8")


def _model(name: str) -> Any:
    if not available():
        raise STTError(f"faster-whisper belum terpasang. Pasang: {INSTALL}")
    device, compute = _device()
    key = (name, device)
    with _lock:
        if key not in _models:
            from faster_whisper import WhisperModel

            try:
                _models[key] = WhisperModel(name, device=device, compute_type=compute)
            except (ValueError, RuntimeError, OSError) as exc:
                raise STTError(f"Model Whisper '{name}' tidak bisa dimuat: {exc}") from exc
        return _models[key]


def transcribe(path: str | Path, language: str = "", *, words: bool = False, model: str = "") -> Transcript:
    """The speech in an audio or video file. ``language`` empty lets Whisper detect it."""
    source = Path(path)
    if not source.is_file():
        raise STTError(f"{source.name} tidak ditemukan")
    engine = _model(model_name(model))
    try:
        parts, info = engine.transcribe(
            str(source), language=language.strip().lower() or None, vad_filter=True, beam_size=5,
            word_timestamps=words,
        )
        segments = [
            Segment(round(s.start, 2), round(s.end, 2), s.text.strip(),
                    [Word(round(w.start, 2), round(w.end, 2), w.word.strip()) for w in (s.words or [])])
            for s in parts
        ]
    except (ValueError, RuntimeError, OSError) as exc:
        raise STTError(f"{source.name} tidak bisa ditranskripsi: {exc}") from exc
    text = " ".join(s.text for s in segments if s.text).strip()
    return Transcript(text, str(info.language), round(float(info.language_probability), 3),
                      round(float(info.duration), 2), segments)


def _stamp(seconds: float, separator: str) -> str:
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def to_srt(segments: list[Segment]) -> str:
    blocks = [f"{i}\n{_stamp(s.start, ',')} --> {_stamp(s.end, ',')}\n{s.text}\n"
              for i, s in enumerate((s for s in segments if s.text), start=1)]
    return "\n".join(blocks)


def to_vtt(segments: list[Segment]) -> str:
    cues = [f"{_stamp(s.start, '.')} --> {_stamp(s.end, '.')}\n{s.text}\n" for s in segments if s.text]
    return "WEBVTT\n\n" + "\n".join(cues)


__all__ = ["STTError", "Segment", "Transcript", "Word", "available", "model_name", "to_srt", "to_vtt", "transcribe"]
