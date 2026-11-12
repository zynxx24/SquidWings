"""Talk to the agent from the terminal: record the microphone, turn it into text, read answers aloud.

Everything is optional and checked at the moment of use, so the CLI works the same without any of it:

* recording needs ``arecord`` (alsa-utils) or ``parecord`` (pulseaudio-utils);
* speech to text uses ``faster-whisper`` when it is installed (local, nothing leaves the machine),
  otherwise the ``SpeechRecognition`` package, which sends the audio to Google's free web endpoint;
* reading aloud uses the ElevenLabs key from ``config.credentials`` (same one as ``voice_speak``)
  and plays the MP3 with ``ffplay``, ``mpv`` or ``ffmpeg`` piped into ``paplay``/``aplay``.

``VOICE_LANG`` (default ``id``) is the spoken language and ``VOICE_STT_MODEL`` (default ``base``)
the faster-whisper model size. Only the recording and the reply text are touched; no key is logged.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_RECORD_SECONDS = 60
# A spoken answer is a summary, not a read-out of a long reply: ElevenLabs bills per character.
MAX_SPOKEN_CHARS = 700
_SAMPLE_RATE = 16000


class VoiceIOError(RuntimeError):
    """Something the user can act on: a missing program, no speech, a refused key."""


@dataclass(frozen=True)
class Capabilities:
    recorder: str | None
    player: str | None
    stt: str | None  # "faster-whisper", "google" or None
    tts: bool        # an ElevenLabs key is set

    @property
    def can_listen(self) -> bool:
        return self.recorder is not None and self.stt is not None

    @property
    def can_speak(self) -> bool:
        return self.tts and self.player is not None


def _module_available(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _recorder() -> str | None:
    return next((p for p in ("arecord", "parecord") if shutil.which(p)), None)


def _player() -> str | None:
    for program in ("ffplay", "mpv"):
        if shutil.which(program):
            return program
    if shutil.which("ffmpeg") and (shutil.which("paplay") or shutil.which("aplay")):
        return "ffmpeg"
    return None


def _stt_engine() -> str | None:
    if _module_available("faster_whisper"):
        return "faster-whisper"
    return "google" if _module_available("speech_recognition") else None


def capabilities() -> Capabilities:
    from config import credentials

    return Capabilities(_recorder(), _player(), _stt_engine(), bool(credentials.get("elevenlabs_api_key")))


def language() -> str:
    return os.getenv("VOICE_LANG", "").strip().lower() or "id"


# ── Recording ────────────────────────────────────────────────────────────────

def _record_command(recorder: str, path: Path) -> list[str]:
    if recorder == "arecord":
        return ["arecord", "-q", "-f", "S16_LE", "-r", str(_SAMPLE_RATE), "-c", "1", "-t", "wav", str(path)]
    return ["parecord", "--file-format=wav", f"--rate={_SAMPLE_RATE}", "--channels=1", str(path)]


def record(path: Path, wait_for_stop: Callable[[], object], max_seconds: int = MAX_RECORD_SECONDS) -> Path:
    """Record to ``path`` until ``wait_for_stop()`` returns or ``max_seconds`` pass.

    ``wait_for_stop`` is a blocking callable (the CLI passes ``input``). The recorder is stopped
    with SIGINT, the one signal that makes arecord finish the WAV header properly.
    """
    recorder = _recorder()
    if recorder is None:
        raise VoiceIOError("Tidak ada perekam suara. Pasang alsa-utils (arecord) atau pulseaudio-utils (parecord).")
    try:
        proc = subprocess.Popen(_record_command(recorder, path), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as exc:
        raise VoiceIOError(f"Perekam gagal dijalankan: {exc}") from exc

    def stop() -> None:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)

    timer = threading.Timer(max_seconds, stop)
    timer.daemon = True
    timer.start()
    try:
        # A recorder that dies at once (no device, busy) must not leave us waiting for Enter.
        try:
            proc.wait(timeout=0.4)
        except subprocess.TimeoutExpired:
            wait_for_stop()
        else:
            raise VoiceIOError(_recorder_error(proc))
    finally:
        timer.cancel()
        stop()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if not path.exists() or path.stat().st_size < 1000:
        raise VoiceIOError("Rekaman kosong. Cek mikrofon dan volume input.")
    return path


def _recorder_error(proc: subprocess.Popen[bytes]) -> str:
    err = (proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else "")[:200]
    return f"Mikrofon tidak bisa dipakai: {err or 'perekam langsung berhenti'}"


# ── Speech to text ───────────────────────────────────────────────────────────

def _transcribe_whisper(path: Path) -> str:
    from utils import whisper_stt

    try:
        return whisper_stt.transcribe(path, language()).text
    except whisper_stt.STTError as exc:
        raise VoiceIOError(str(exc)) from exc


def _transcribe_google(path: Path) -> str:
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    with sr.AudioFile(str(path)) as source:
        audio = recognizer.record(source)
    locale = {"id": "id-ID", "en": "en-US"}.get(language(), language())
    try:
        return str(recognizer.recognize_google(audio, language=locale)).strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as exc:
        raise VoiceIOError(f"Pengenalan suara online gagal ({exc}). Cek koneksi, atau pasang faster-whisper agar lokal.") from exc


def transcribe(path: Path) -> str:
    """The words in a recording; empty when nothing was said."""
    engine = _stt_engine()
    if engine == "faster-whisper":
        return _transcribe_whisper(path)
    if engine == "google":
        return _transcribe_google(path)
    raise VoiceIOError("Belum ada pengenal suara. Pasang: pip install faster-whisper (lokal) atau SpeechRecognition.")


def listen(wait_for_stop: Callable[[], object], max_seconds: int = MAX_RECORD_SECONDS,
           on_recorded: Callable[[], object] | None = None) -> str:
    """Record one utterance and return what was said. ``on_recorded`` runs once the recording is done."""
    with tempfile.TemporaryDirectory(prefix="voice-") as tmp:
        recording = record(Path(tmp) / "in.wav", wait_for_stop, max_seconds)
        if on_recorded is not None:
            on_recorded()
        return transcribe(recording)


# ── Text to speech ───────────────────────────────────────────────────────────

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[#*_>|~]+")
_BULLET = re.compile(r"^\s*(?:[-+•]|\d+[.)])\s+", re.M)


def speakable(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    """The part of a reply worth reading aloud: no code, links or Markdown, cut at a sentence."""
    text = _FENCE.sub(" (ada kode di layar) ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _BULLET.sub("", text)
    text = _MARKUP.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0]).strip()


async def synthesize(text: str, dest: Path, voice: str = "") -> Path:
    """Write the spoken MP3 for ``text`` with the ElevenLabs account of this machine."""
    from tool.execute.execute_toolbox import voice_tools

    if not voice_tools._key():
        raise VoiceIOError(voice_tools.SETUP)
    try:
        voice_id, _ = await voice_tools._resolve_voice(voice)
        response = await voice_tools._request(
            "POST", f"/v1/text-to-speech/{voice_id}", params={"output_format": voice_tools._OUTPUT_FORMAT}, timeout=60.0,
            json_body={"text": text, "model_id": voice_tools._model()},
        )
    except voice_tools.VoiceError as exc:
        raise VoiceIOError(str(exc)) from exc
    if not response.content:
        raise VoiceIOError("ElevenLabs tidak mengirim audio.")
    dest.write_bytes(response.content)
    return dest


def _play_command(player: str, path: Path) -> list[str]:
    if player == "ffplay":
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
    if player == "mpv":
        return ["mpv", "--no-video", "--really-quiet", str(path)]
    sink = "paplay" if shutil.which("paplay") else "aplay"
    return ["sh", "-c", f'ffmpeg -loglevel quiet -i "$1" -f wav - | {sink}', "sh", str(path)]


def play(path: Path) -> None:
    """Play an audio file and wait until it ends. Ctrl+C stops it."""
    player = _player()
    if player is None:
        raise VoiceIOError("Tidak ada pemutar audio. Pasang ffmpeg (ffplay) atau mpv.")
    try:
        subprocess.run(_play_command(player, path), stdin=subprocess.DEVNULL, check=False, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VoiceIOError(f"Pemutar audio gagal: {exc}") from exc


async def say(text: str, voice: str = "") -> bool:
    """Read a reply aloud. False when there was nothing worth saying."""
    spoken = speakable(text)
    if not spoken:
        return False
    with tempfile.TemporaryDirectory(prefix="voice-") as tmp:
        mp3 = await synthesize(spoken, Path(tmp) / "out.mp3", voice)
        await asyncio.to_thread(play, mp3)
    return True
