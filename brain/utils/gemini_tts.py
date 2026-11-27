"""Gemini text to speech: a voice that takes direction (mood, pace, pauses), as in Google AI Studio's speech tab.

The request is the one AI Studio's code example makes, sent with httpx (no extra SDK): ``generateContent`` on a
``*-tts`` model with ``responseModalities: ["AUDIO"]`` and a prebuilt voice. The answer is WAV, or raw PCM
(``audio/L16;rate=24000``) which gets a WAV header here. The prompt follows AI Studio's structure: a director's note
(how to say it) above ``## Transcript:`` (what to say), so the note steers the voice and is not read aloud.

The key is ``gemini_api_key`` in Kredensial (or ``GEMINI_API_KEY``); several keys may be given, separated by commas
or new lines. ``gemini_tts_model`` and ``gemini_tts_voice`` change the defaults. A key is sent as a header, never put
in a URL, a result or a log line.

Routing: the free tier counts per key and per model, so each request goes to a (key, model) pair. The best model is
tried on every key in turn first. A pair that answers 429 rests: until the delay Google names for the per-minute
limit, for hours for the daily one, half an hour for a refused key. Only when every key is out for the day on a model
does the voice move to the next model in ``FALLBACK_MODELS``; a per-minute limit is waited out on the same model, so
one track keeps one sound.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import itertools
import json
import re
import struct
import time
import wave

import httpx

from brain.config import credentials
from brain.utils import cache

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash-tts"
# Where the voice goes when every key is out for the day on the model before it; best first.
FALLBACK_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview",
                   "gemini-2.5-flash-preview-tts")
DAY_REST_S = 6 * 3600.0
AUTH_REST_S = 30 * 60.0
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


# (key fingerprint, model or "*") -> (kind, monotonic time it may serve again). Bounded; the key itself is never kept.
_rest = cache.cache("gemini_tts_rest", max_entries=512)
_turn = itertools.count()
_clock = time.monotonic


def key() -> str:
    """Everything typed in the key field: one key, or several separated by commas or new lines."""
    return credentials.get("gemini_api_key")


def keys() -> list[str]:
    found: list[str] = []
    for part in re.split(r"[\s,;]+", key() or ""):
        if part and part not in found:
            found.append(part)
    return found


def model() -> str:
    return credentials.get("gemini_tts_model") or DEFAULT_MODEL


def models() -> list[str]:
    first = model()
    return [first, *(m for m in FALLBACK_MODELS if m != first)]


def _tag(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _resting(api_key: str, name: str, now: float) -> tuple[str, float] | None:
    for scope in (name, "*"):
        state = _rest.get((_tag(api_key), scope))
        if state and state[1] > now:
            return str(state[0]), float(state[1])
    return None


def _set_rest(api_key: str, scope: str, kind: str, seconds: float) -> None:
    _rest.set((_tag(api_key), scope), (kind, _clock() + seconds), ttl=seconds)


def pick_route() -> tuple[str, str] | float | None:
    """(key, model) to send to now; or seconds to wait for the best model's per-minute limit; or None when every
    key is out for the day (or refused) on every model."""
    pool = keys()
    if not pool:
        return None
    now = _clock()
    start = next(_turn) % len(pool)
    for name in models():
        waits: list[float] = []
        for i in range(len(pool)):
            api_key = pool[(start + i) % len(pool)]
            state = _resting(api_key, name, now)
            if state is None:
                return api_key, name
            if state[0] == "minute":
                waits.append(state[1] - now)
        if waits:
            return max(0.5, min(waits))
    return None


def route_status() -> list[dict[str, object]]:
    """Which key (by its last four characters) is resting on which model, for the page and for errors."""
    now = _clock()
    out: list[dict[str, object]] = []
    for api_key in keys():
        for name in [*models(), "*"]:
            state = _rest.get((_tag(api_key), name))
            if state and state[1] > now:
                out.append({"key": f"…{api_key[-4:]}", "model": name, "why": state[0], "seconds": round(state[1] - now)})
    return out


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


async def _post(client: httpx.AsyncClient, body: dict) -> httpx.Response:
    """One request, routed over every (key, model) pair. A 429 rests that pair and the next one is tried; a busy
    server (5xx) or a dropped connection is tried again a few times; when every pair is out, the reason is raised."""
    hiccups = 0
    waits = 0
    while True:
        route = pick_route()
        if route is None:
            raise TTSError("Kuota harian Gemini TTS habis di semua key dan model (paket gratis: 10 permintaan per hari "
                           "per model per key). Tambah key lain di Kredensial (pisahkan dengan koma), coba lagi besok, "
                           "aktifkan billing di AI Studio, atau pakai engine edge (gratis) dulu.")
        if isinstance(route, float):
            waits += 1
            if waits > MAX_RETRIES or route > MAX_WAIT_S:
                raise TTSError(f"Semua key Gemini TTS sedang kena batas per menit; coba lagi sekitar {round(route)} detik.")
            await asyncio.sleep(route + 1)
            continue
        api_key, name = route
        try:
            response = await client.post(API.format(model=name), headers={"x-goog-api-key": api_key}, json=body)
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
        if response.status_code == 429:
            delay = _retry_delay(response)
            if delay is None:
                _set_rest(api_key, name, "day", DAY_REST_S)
            else:
                _set_rest(api_key, name, "minute", min(delay, MAX_WAIT_S))
            continue
        if response.status_code in (401, 403) and len(keys()) > 1:
            _set_rest(api_key, "*", "key ditolak", AUTH_REST_S)
            continue
        if response.status_code == 404 and name != models()[-1]:
            _set_rest(api_key, name, "day", DAY_REST_S)       # this key cannot use that model; try the next one
            continue
        return response


def _retry_delay(response: httpx.Response) -> float | None:
    try:
        details = response.json().get("error", {}).get("details", [])
    except ValueError:
        return 30.0
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


async def _speak_piece(client: httpx.AsyncClient, body: dict, text: str) -> bytes:
    """One piece of speech, asked once more when the answer has no audio or stops far short of the text."""
    why = ""
    best = b""
    for _ in range(2):
        response = await _post(client, body)
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
    if not keys():
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
            clips.append(await _speak_piece(client, body, piece))
    return join_wavs(clips)
