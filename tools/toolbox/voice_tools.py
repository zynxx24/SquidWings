"""Voice-over: list voices and turn text into an MP3 in the workspace, with captions for a video.

Two engines. ElevenLabs when its key is set (the most natural voices, paid per character), and Edge neural
voices otherwise or when asked (``engine="edge"``): free, no key, 300+ voices in every common language,
Indonesian included (id-ID-GadisNeural, id-ID-ArdiNeural). Both write the MP3; with ``captions`` (the
default) Edge also writes ``<name>.srt`` and ``<name>.words.json`` (each word with its start and end in
seconds), which a video (Remotion, HyperFrames, the Studio) uses for synced captions and cuts.

The API key comes from the team page's Kredensial tab or ``ELEVENLABS_API_KEY`` in ``.env``
(see ``brain.config.credentials``); it is read on every call, so adding it later needs no restart.
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

from brain.config import credentials

from . import _files
from .catalog import group, param, spec
from .office_tools import deliver

group("voice", "Voice-over and text to speech (ElevenLabs, free Edge neural voices)",
      "suara, voice, voice over, voice-over, voiceover, narasi, narator, narrator, dubbing, dubber, sulih suara, "
      "tts, text to speech, teks ke suara, bacakan, ucapkan, rekam suara, elevenlabs, eleven labs, edge tts, caption, subtitle")

API = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
MAX_CHARS = 4500                 # per ElevenLabs request; longer text is sent in pieces and joined
EDGE_DEFAULT = {"id": "id-ID-GadisNeural", "en": "en-US-AvaMultilingualNeural"}
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


# ── Edge neural voices (free) ─────────────────────────────────────────────
_edge_cache: list[dict[str, Any]] = []


async def _edge_voices() -> list[dict[str, Any]]:
    if not _edge_cache:
        import edge_tts

        for v in await edge_tts.list_voices():
            _edge_cache.append({"voice_id": v["ShortName"], "name": v.get("FriendlyName", v["ShortName"]),
                                "gender": v.get("Gender"), "locale": v.get("Locale"),
                                "styles": (v.get("VoiceTag") or {}).get("VoicePersonalities", [])})
    return _edge_cache


def _edge_pick(voice: str, text: str) -> str:
    if voice and re.fullmatch(r"[a-z]{2,3}-[A-Z]{2,4}-[A-Za-z]+Neural", voice):
        return voice
    v = (voice or "").lower()
    if v in {"pria", "cowok", "laki", "male", "ardi"}:
        return "id-ID-ArdiNeural"
    if v in {"wanita", "cewek", "perempuan", "female", "gadis"}:
        return "id-ID-GadisNeural"
    indonesian = re.search(r"\b(yang|dan|ini|itu|untuk|dengan|tidak|kamu|kita|adalah|dari)\b", text.lower())
    return EDGE_DEFAULT["id" if indonesian or not text.isascii() else "en"]


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


async def _edge_speak(text: str, dest: Any, voice: str, speed: float, pitch: int, captions: bool) -> dict[str, Any]:
    import json

    import edge_tts

    rate = f"{round((min(2.0, max(0.5, float(speed))) - 1) * 100):+d}%"
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=f"{int(pitch):+d}Hz", boundary="WordBoundary")
    words: list[dict[str, Any]] = []
    with open(dest, "wb") as audio:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                words.append({"word": chunk["text"], "start": round(start, 3), "end": round(start + chunk["duration"] / 10_000_000, 3)})
    extra: dict[str, Any] = {"words": len(words), "duration_s": words[-1]["end"] if words else None}
    if captions and words:
        # The voice reports bare words; find each in the script to learn where sentences and clauses end.
        pos = 0
        for w in words:
            at = text.find(w["word"], pos)
            if at >= 0:
                pos = at + len(w["word"])
                tail = text[pos:pos + 2]
                w["stop"] = bool(re.match(r"[.!?…]", tail))
                w["pause"] = bool(re.match(r"[,;:—-]", tail))
        lines, cue = [], []
        for w in words:                      # cues of up to 7 words, cut at a sentence end or a pause after 3
            cue.append(w)
            if len(cue) >= 7 or w.get("stop") or (w.get("pause") and len(cue) >= 3):
                lines.append(cue)
                cue = []
        if cue:
            lines.append(cue)
        srt = "\n".join(f"{i}\n{_srt_time(c[0]['start'])} --> {_srt_time(c[-1]['end'])}\n{' '.join(x['word'] for x in c)}\n"
                        for i, c in enumerate(lines, 1))
        srt_path = dest.with_suffix(".srt")
        json_path = dest.with_suffix(".words.json")
        srt_path.write_text(srt, encoding="utf-8")
        json_path.write_text(json.dumps([{k: w[k] for k in ("word", "start", "end")} for w in words], ensure_ascii=False),
                             encoding="utf-8")
        extra.update(captions_srt=srt_path.name, words_json=json_path.name)
    return extra


@spec("voice_list", "List the voices voice_speak can use. ElevenLabs voices (id, name, gender, accent, style, plan "
      "characters left) when its key is set; otherwise the free Edge neural voices (300+, search 'id-ID' for "
      "Indonesian). Call it before voice_speak to pick a narrator.",
      "voice", [param("search", description="Optional filter on name, locale, gender or style, e.g. 'id-ID' or 'female'.",
                      required=False),
                param("engine", description="auto (ElevenLabs if connected, else Edge), elevenlabs, or edge.",
                      required=False, default="auto")],
      keywords="daftar suara, pilih suara, pilih narator, list voices, voice library, kuota elevenlabs",
      lead=True, network=True)
async def voice_list(search: str = "", engine: str = "auto") -> dict[str, Any]:
    if engine == "edge" or (engine == "auto" and not _key()):
        try:
            voices = await _edge_voices()
        except Exception as exc:  # noqa: BLE001 - offline: say so plainly
            return {"ok": False, "engine": "edge", "error": f"Daftar suara Edge tidak bisa diambil: {exc}"}
        needle = search.strip().lower()
        if needle:
            voices = [v for v in voices if needle in " ".join(str(x) for x in v.values()).lower()]
        return {"ok": True, "engine": "edge", "count": len(voices), "voices": voices[:80],
                "default_voice_id": EDGE_DEFAULT["id"], "hint": "Gratis tanpa key. Cari 'id-ID' untuk suara Indonesia."}
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


@spec("voice_speak", "Turn text into a spoken MP3 in the workspace (a voice-over for a video, a narration). Engine "
      "auto: ElevenLabs when its key is set, else free Edge neural voices (Indonesian: id-ID-GadisNeural female, "
      "id-ID-ArdiNeural male; 'pria'/'wanita' also work). Any length: long scripts are handled in pieces. With "
      "captions (default) Edge also writes <name>.srt and <name>.words.json (every word with start/end seconds) for "
      "synced captions in Remotion/HyperFrames/Studio. Write numbers and names the way they should be said.",
      "voice", [param("text", description="What to say, as it should be spoken."),
                param("output", description="File to write, e.g. 'voice/open.mp3'."),
                param("voice", description="Voice name or id (from voice_list), or 'pria'/'wanita'. Empty: a fitting default.",
                      required=False),
                param("engine", description="auto, elevenlabs, or edge (free).", required=False, default="auto"),
                param("stability", "num", "ElevenLabs: 0..1. Lower is more expressive. Default 0.5.", required=False, default=0.5),
                param("speed", "num", "0.5..2.0 (ElevenLabs uses 0.7..1.2). Default 1.0.", required=False, default=1.0),
                param("pitch", "int", "Edge: pitch shift in Hz, e.g. -20 deeper, 15 brighter. Default 0.", required=False, default=0),
                param("captions", "bool", "Edge: also write .srt and .words.json. Default true.", required=False, default=True),
                param("share", "bool", required=False, default=True)],
      keywords="buat suara, bikin voice over, rekam narasi, bacakan teks, text to speech, generate voice, dubbing, "
               "suara video, narasi video, subtitle, caption",
      lead=True, network=True, workspace=True, read_only=False)
async def voice_speak(text: str, output: str, voice: str = "", engine: str = "auto", stability: float = 0.5,
                      speed: float = 1.0, pitch: int = 0, captions: bool = True, share: bool = True,
                      workspace_path: str | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    if not output.lower().endswith(".mp3"):
        output = f"{output}.mp3"
    dest = _files.destination(output, workspace_path)
    use_edge = engine == "edge" or (engine != "elevenlabs" and not _key())
    if engine == "elevenlabs" and not _key():
        return _not_connected()
    if use_edge:
        name = _edge_pick(voice, text)
        try:
            extra = await _edge_speak(text, dest, name, speed, pitch, captions)
        except Exception as exc:  # noqa: BLE001 - offline or a wrong voice name, said plainly
            return {"ok": False, "engine": "edge", "error": f"Edge TTS gagal: {exc}. Cek internet atau nama suara (voice_list)."}
        out = deliver(dest, workspace_path, share, caption=f"Suara: {name}")
        out.update(engine="edge", voice=name, characters=len(text), **extra)
        return out
    # ElevenLabs: one request per piece of at most MAX_CHARS, cut at sentence ends, MP3 frames joined
    pieces, cur = [], ""
    for sentence in re.split(r"(?<=[.!?…])\s+", text):
        while len(sentence) > MAX_CHARS:
            pieces.append(sentence[:MAX_CHARS])
            sentence = sentence[MAX_CHARS:]
        if len(cur) + len(sentence) + 1 > MAX_CHARS and cur:
            pieces.append(cur)
            cur = ""
        cur = f"{cur} {sentence}".strip()
    if cur:
        pieces.append(cur)
    try:
        voice_id, voice_name = await _resolve_voice(voice)
        audio = b""
        for piece in pieces:
            response = await _request(
                "POST", f"/v1/text-to-speech/{voice_id}", params={"output_format": _OUTPUT_FORMAT}, timeout=180.0,
                json_body={
                    "text": piece,
                    "model_id": _model(),
                    "voice_settings": {"stability": min(1.0, max(0.0, float(stability))), "similarity_boost": 0.75,
                                       "speed": min(1.2, max(0.7, float(speed)))},
                },
            )
            if not response.content or not response.headers.get("content-type", "").startswith("audio/"):
                return {"ok": False, "connected": True, "error": f"ElevenLabs returned no audio ({response.headers.get('content-type', '?')})"}
            audio += response.content
    except VoiceError as exc:
        return {"ok": False, "connected": True, "error": str(exc)}
    dest.write_bytes(audio)
    out = deliver(dest, workspace_path, share, caption=f"Suara: {voice_name}")
    out.update(engine="elevenlabs", voice=voice_name, voice_id=voice_id, model=_model(), characters=len(text), pieces=len(pieces))
    return out
