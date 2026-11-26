"""Gemini text to speech: a voice that takes direction (mood, pace, pauses), as in Google AI Studio's speech tab.

The request is the one AI Studio's code example makes, sent with httpx (no extra SDK): ``generateContent`` on a
``*-tts`` model with ``responseModalities: ["AUDIO"]`` and a prebuilt voice. The answer is WAV, or raw PCM
(``audio/L16;rate=24000``) which gets a WAV header here. The prompt follows AI Studio's structure: a director's note
(how to say it) above ``## Transcript:`` (what to say), so the note steers the voice and is not read aloud.

The key is ``gemini_api_key`` in Kredensial (or ``GEMINI_API_KEY``); ``gemini_tts_model`` and ``gemini_tts_voice``
change the defaults. The key is sent as a header, never put in a URL, a result or a log line.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import re
import struct
import wave

import httpx

from brain.config import credentials

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash-tts"
DEFAULT_VOICE = "Kira"
# One request speaks at most this much; longer text goes in pieces cut at sentence ends, joined in order.
MAX_CHARS = 1800
TIMEOUT = 180.0
# The free tier allows 3 requests a minute per model: wait out a 429 this many times, at most this long each.
MAX_RETRIES = 8
MAX_WAIT_S = 70.0
# A busy server (5xx) or a dropped connection is tried again this many times, waiting a little longer each time.
TRANSIENT = frozenset({500, 502, 503, 504})
HICCUP_RETRIES = 3
# Speech runs about 14 characters a second; audio shorter than text/SHORTEST_CPS seconds was cut off upstream.
SHORTEST_CPS = 45.0
VOICES = (
    "Kira", "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia",
    "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix",
    "Sadachbia", "Sadaltager", "Sulafat",
)


class TTSError(RuntimeError):
    pass


def key() -> str:
    return credentials.get("gemini_api_key")


def model() -> str:
    return credentials.get("gemini_tts_model") or DEFAULT_MODEL


def voice(name: str = "") -> str:
    """A voice name as Gemini spells it; an unknown name is passed on as given (new voices appear upstream)."""
    wanted = (name or "").strip() or credentials.get("gemini_tts_voice") or DEFAULT_VOICE
    return next((v for v in VOICES if v.lower() == wanted.lower()), wanted)


def prompt(text: str, direction: str = "") -> str:
    """AI Studio's shape: how to say it, then the transcript. The note is direction, never spoken."""
    note = " ".join((direction or "").split())
    head = (f"## Director's notes:\n{note}\nRead only the transcript below, in its language, following these notes.\n\n"
            if note else "")
    return f"{head}## Transcript:\n{text.strip()}"


def _mime_params(mime: str) -> tuple[int, int]:
    """(bits per sample, sample rate) from e.g. 'audio/L16;rate=24000'."""
    bits, rate = 16, 24000
    for part in (mime or "").split(";"):
        part = part.strip()
        if part.lower().startswith("rate="):
            with contextlib.suppress(ValueError):
                rate = int(part.split("=", 1)[1])
        match = re.match(r"audio/L(\d+)", part)
        if match:
            bits = int(match.group(1))
    return bits, rate


def to_wav(data: bytes, mime: str) -> bytes:
    """WAV bytes: returned as is when they already are, else raw PCM given a header (mono)."""
    if data[:4] == b"RIFF":
        return data
    bits, rate = _mime_params(mime)
    block = bits // 8
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, 1, rate,
                         rate * block, block, bits, b"data", len(data))
    return header + data


def pieces(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Text in pieces of at most ``limit`` characters, cut at sentence ends."""
    out: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
        while len(sentence) > limit:
            out.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + len(sentence) + 1 > limit:
            out.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def join_wavs(parts: list[bytes]) -> bytes:
    """One WAV from several with the same format, end to end."""
    if len(parts) == 1:
        return parts[0]
    frames = b""
    params = None
    for part in parts:
        with wave.open(io.BytesIO(part)) as clip:
            params = params or clip.getparams()
            frames += clip.readframes(clip.getnframes())
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        assert params is not None
        out.setparams(params)
        out.writeframes(frames)
    return buffer.getvalue()


async def _post(client: httpx.AsyncClient, body: dict, api_key: str) -> httpx.Response:
    """One request. The free tier's per-minute limit (429) waits the delay Google names; a busy server (5xx) or a
    dropped connection is tried again a few times; the daily limit says so and stops."""
    hiccups = 0
    attempt = 0
    while True:
        try:
            response = await client.post(API.format(model=model()), headers={"x-goog-api-key": api_key}, json=body)
        except httpx.HTTPError as exc:
            hiccups += 1
            if hiccups > HICCUP_RETRIES:
                raise TTSError(f"Gemini TTS tidak bisa dihubungi ({type(exc).__name__})") from None
            await asyncio.sleep(2.0 * hiccups)
            continue
        if response.status_code in TRANSIENT and hiccups < HICCUP_RETRIES:
            hiccups += 1
            await asyncio.sleep(2.0 * hiccups * hiccups)
            continue
        if response.status_code != 429 or attempt >= MAX_RETRIES:
            return response
        attempt += 1
        wait = _retry_delay(response)
        if wait is None or wait > MAX_WAIT_S:
            if "PerDay" in response.text:
                raise TTSError("Kuota harian Gemini TTS habis (paket gratis: 10 permintaan per hari per model). Coba "
                               "lagi besok, aktifkan billing di AI Studio, atau pakai engine edge (gratis) dulu.")
            return response          # a long wait: say so instead of hanging
        await asyncio.sleep(wait + 1)


def _retry_delay(response: httpx.Response) -> float | None:
    try:
        details = response.json().get("error", {}).get("details", [])
    except ValueError:
        return None
    for detail in details:
        if "PerDay" in json.dumps(detail):
            return None
    for detail in details:
        delay = str(detail.get("retryDelay") or "")
        if delay.endswith("s"):
            with contextlib.suppress(ValueError):
                return float(delay[:-1])
    return 30.0


def _wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as clip:
            frames: int = clip.getnframes()
            rate: int = clip.getframerate()
    except (wave.Error, EOFError):
        return 0.0
    return frames / float(rate or 1)


def _answer_audio(response: httpx.Response) -> tuple[bytes | None, str]:
    """(WAV bytes, why there are none). Gemini sometimes answers 200 with text or nothing instead of audio."""
    try:
        data = response.json()
        candidate = (data.get("candidates") or [{}])[0]
    except (ValueError, AttributeError):
        return None, "jawabannya bukan JSON"
    parts = (candidate.get("content") or {}).get("parts") or []
    inline = next((p.get("inlineData") for p in parts if p.get("inlineData")), None)
    if inline and inline.get("data"):
        return to_wav(base64.b64decode(inline["data"]), str(inline.get("mimeType") or "")), ""
    reason = candidate.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason") or "kosong"
    return None, f"tanpa audio ({reason})"


async def _speak_piece(client: httpx.AsyncClient, body: dict, api_key: str, text: str) -> bytes:
    """One piece of speech, asked once more when the answer has no audio or stops far short of the text."""
    why = ""
    best = b""
    for _ in range(2):
        response = await _post(client, body, api_key)
        if response.status_code != 200:
            try:
                reason = str(response.json().get("error", {}).get("message", ""))[:300]
            except ValueError:
                reason = response.text[:300]
            raise TTSError(f"Gemini TTS menolak ({response.status_code}): {reason}")
        wav, why = _answer_audio(response)
        if wav is None:
            continue
        if len(text) < 40 or _wav_seconds(wav) >= len(text) / SHORTEST_CPS:
            return wav
        why = "audionya terpotong"
        best = max(best, wav, key=len)
    if best:
        return best                 # still short: the track check after it hears which lines are missing
    raise TTSError(f"Gemini TTS tidak mengembalikan audio: {why}")


async def speak(text: str, direction: str = "", voice_name: str = "", temperature: float = 1.0) -> bytes:
    """WAV bytes of the text spoken in the voice, following the direction. Raises TTSError with a reason."""
    api_key = key()
    if not api_key:
        raise TTSError("Gemini belum tersambung: isi Gemini API key di Tim agent > Kredensial (atau GEMINI_API_KEY).")
    chosen = voice(voice_name)
    clips: list[bytes] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for piece in pieces(text):
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt(piece, direction)}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "temperature": temperature,
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": chosen}}},
                },
            }
            clips.append(await _speak_piece(client, body, api_key, piece))
    return join_wavs(clips)
