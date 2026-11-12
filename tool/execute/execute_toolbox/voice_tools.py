"""Voice-over with ElevenLabs: list the account's voices and turn text into an MP3 in the workspace.

The API key comes from the team page's Kredensial tab or ``ELEVENLABS_API_KEY`` in ``.env``
(see ``config.credentials``); it is read on every call, so adding it later needs no restart.
Without a key both tools answer with how to connect instead of failing.

``ELEVENLABS_VOICE_ID`` picks the default voice and ``ELEVENLABS_MODEL`` the model
(default ``eleven_multilingual_v2``, which speaks Indonesian). Only api.elevenlabs.io is
called, and the key is never put in a result or a log line. Generation costs about one
credit per character, so ``voice_speak`` reports the characters it used.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from config import credentials

from . import _files
from .catalog import group, param, spec
from .office_tools import deliver

group("voice", "Voice-over and text to speech (ElevenLabs)",
      "suara, voice, voice over, voice-over, voiceover, narasi, narator, narrator, dubbing, dubber, sulih suara, "
      "tts, text to speech, teks ke suara, bacakan, ucapkan, rekam suara, elevenlabs, eleven labs")

API = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
MAX_CHARS = 5000
_VOICE_ID = re.compile(r"[A-Za-z0-9]{10,40}")
_OUTPUT_FORMAT = "mp3_44100_128"
SETUP = ("Isi ElevenLabs API key di halaman Tim → tab Kredensial (atau ELEVENLABS_API_KEY di .env). "
         "Key-nya dari elevenlabs.io → Developers → API Keys. Ada paket gratis.")


class VoiceError(RuntimeError):
    pass


def _key() -> str:
    return credentials.get("elevenlabs_api_key")


def _not_connected() -> dict[str, Any]:
    return {"ok": False, "connected": False, "error": "ElevenLabs belum tersambung: belum ada API key.", "setup": SETUP}


def _model() -> str:
    return os.getenv("ELEVENLABS_MODEL", "").strip() or DEFAULT_MODEL


async def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None,
                   params: dict[str, str] | None = None, timeout: float = 30.0) -> httpx.Response:
    headers = {"xi-api-key": _key(), "Accept": "audio/mpeg" if method == "POST" else "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.request(method, f"{API}{path}", headers=headers, json=json_body, params=params)
    except httpx.HTTPError as exc:
        raise VoiceError(f"tidak bisa menghubungi ElevenLabs ({exc.__class__.__name__}); mesin ini mungkin offline") from exc
    if response.status_code == 401:
        raise VoiceError("ElevenLabs menolak API key-nya (401). Periksa key di tab Kredensial.")
    if response.status_code == 429:
        raise VoiceError("ElevenLabs membatasi permintaan atau kuota habis (429). Coba lagi nanti atau cek paketnya.")
    if response.status_code >= 400:
        raise VoiceError(f"ElevenLabs menjawab {response.status_code}: {_detail(response)}")
    return response


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or detail)[:200]
    return str(detail)[:200]


async def _voices() -> list[dict[str, Any]]:
    data = (await _request("GET", "/v1/voices")).json()
    out = []
    for v in data.get("voices", []) if isinstance(data, dict) else []:
        labels = v.get("labels") or {}
        out.append({
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", ""),
            "category": v.get("category", ""),
            "gender": labels.get("gender", ""),
            "age": labels.get("age", ""),
            "accent": labels.get("accent", ""),
            "style": labels.get("description") or labels.get("descriptive") or labels.get("use_case", ""),
            "preview_url": v.get("preview_url", ""),
        })
    return out


async def _quota() -> dict[str, Any] | None:
    try:
        data = (await _request("GET", "/v1/user/subscription")).json()
    except VoiceError:
        return None  # a key limited to text-to-speech cannot read the plan; the voices still work
    used, limit = int(data.get("character_count", 0)), int(data.get("character_limit", 0))
    return {"plan": data.get("tier", ""), "characters_used": used, "character_limit": limit,
            "characters_left": max(0, limit - used)}


async def _resolve_voice(voice: str) -> tuple[str, str]:
    """(voice_id, name) from an id, a name, or the default voice."""
    wanted = (voice or "").strip() or credentials.get("elevenlabs_voice_id")
    voices = await _voices()
    if not voices:
        raise VoiceError("akun ElevenLabs ini belum punya suara; tambahkan satu di Voice Library")
    if not wanted:
        return voices[0]["voice_id"], voices[0]["name"]
    for v in voices:
        if wanted == v["voice_id"] or wanted.lower() == v["name"].lower():
            return v["voice_id"], v["name"]
    partial = [v for v in voices if wanted.lower() in v["name"].lower()]
    if len(partial) == 1:
        return partial[0]["voice_id"], partial[0]["name"]
    if _VOICE_ID.fullmatch(wanted):
        return wanted, wanted  # a library voice that is not in "My voices" can still be used by id
    names = ", ".join(v["name"] for v in voices[:15])
    raise VoiceError(f"suara '{wanted}' tidak ada di akun ini. Pilihan: {names}")


@spec("voice_list", "List the ElevenLabs voices this account can use (id, name, gender, age, accent, style, preview "
      "link) and the characters left on the plan. Call it before voice_speak to pick a narrator. Tells how to connect "
      "when no API key is set.",
      "voice", [param("search", description="Optional filter on name, accent, gender or style, e.g. 'female'.",
                      required=False)],
      keywords="daftar suara, pilih suara, pilih narator, list voices, voice library, kuota elevenlabs",
      lead=True, network=True)
async def voice_list(search: str = "") -> dict[str, Any]:
    if not _key():
        return _not_connected()
    try:
        voices = await _voices()
        quota = await _quota()
    except VoiceError as exc:
        return {"ok": False, "connected": True, "error": str(exc)}
    needle = search.strip().lower()
    if needle:
        voices = [v for v in voices if needle in " ".join(str(x) for x in v.values()).lower()]
    return {"ok": True, "connected": True, "count": len(voices), "voices": voices[:40],
            "default_voice_id": credentials.get("elevenlabs_voice_id") or None, "model": _model(), "quota": quota}


@spec("voice_speak", "Turn text into a spoken MP3 with ElevenLabs and save it in the workspace (e.g. a voice-over line "
      "for a video). Pick the voice by name or id from voice_list; without one the default voice is used. Write "
      "numbers and names the way they should be said. Costs about one credit per character (max 5000 per call; "
      "split longer scripts). Shows the audio in the chat when done.",
      "voice", [param("text", description="What to say, as it should be spoken."),
                param("output", description="File to write, e.g. 'voice/open.mp3'."),
                param("voice", description="Voice name or voice_id (from voice_list).", required=False),
                param("stability", "num", "0..1. Lower is more expressive, higher is steadier. Default 0.5.",
                      required=False, default=0.5),
                param("speed", "num", "0.7..1.2. Default 1.0.", required=False, default=1.0),
                param("share", "bool", required=False, default=True)],
      keywords="buat suara, bikin voice over, rekam narasi, bacakan teks, text to speech, generate voice, dubbing",
      lead=True, network=True, workspace=True, read_only=False)
async def voice_speak(text: str, output: str, voice: str = "", stability: float = 0.5, speed: float = 1.0,
                      share: bool = True, workspace_path: str | None = None) -> dict[str, Any]:
    if not _key():
        return _not_connected()
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > MAX_CHARS:
        raise ValueError(f"text has {len(text)} characters; the limit per call is {MAX_CHARS}, so split it into lines")
    if not output.lower().endswith(".mp3"):
        output = f"{output}.mp3"
    dest = _files.destination(output, workspace_path)
    try:
        voice_id, voice_name = await _resolve_voice(voice)
        response = await _request(
            "POST", f"/v1/text-to-speech/{voice_id}", params={"output_format": _OUTPUT_FORMAT}, timeout=120.0,
            json_body={
                "text": text,
                "model_id": _model(),
                "voice_settings": {"stability": min(1.0, max(0.0, float(stability))), "similarity_boost": 0.75,
                                   "speed": min(1.2, max(0.7, float(speed)))},
            },
        )
    except VoiceError as exc:
        return {"ok": False, "connected": True, "error": str(exc)}
    if not response.content or not response.headers.get("content-type", "").startswith("audio/"):
        return {"ok": False, "connected": True, "error": f"ElevenLabs returned no audio ({response.headers.get('content-type', '?')})"}
    dest.write_bytes(response.content)
    out = deliver(dest, workspace_path, share, caption=f"Suara: {voice_name}")
    out.update(voice=voice_name, voice_id=voice_id, model=_model(), characters=len(text))
    return out
