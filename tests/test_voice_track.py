"""A timed voice-over script becomes one voice track that lands every line on its second, and a rendered video
gets that voice with its music ducking. Gemini TTS is exercised with a fake HTTP client: the suite stays offline."""

from __future__ import annotations

import base64
import io
import math
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest

from brain.utils import gemini_tts, toolkit, voice_script

MIC = """NASKAH SUARA - PROMO
Gaya suara   : hangat, santai, kayak ngobrol sama teman.
               Senyum pas ngomong.

Tiap baris punya rentang detik. Jadi [00.56 - 01.00] artinya detik 56 sampai menit 1 pas.

[00.00 - 00.04] [pelan] Jujur ya... (↓) aku capek bayar langganan AI.
[00.04 - 00.08] Terus kepikiran, (↑) gimana kalau *SquidWings* jalan sendiri?
[00.08 - 00.10] (//) Dan semuanya... [semangat] (↑) gratis.

------------------------------------------------------------------
CATATAN UNTUK GENERATOR SUARA
- "SquidWings" dibaca: skuid-wings.
"""

needs_ffmpeg = pytest.mark.skipif(toolkit.ffmpeg_exe() is None, reason="ffmpeg is not installed")


def tone(seconds: float, rate: int = 24000, loud: int = 8000) -> bytes:
    """A WAV of a quiet beep, standing in for a spoken line."""
    frames = b"".join(struct.pack("<h", int(loud * math.sin(2 * math.pi * 440 * i / rate)))
                      for i in range(int(seconds * rate)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(frames)
    return buffer.getvalue()


def test_the_house_script_is_read_with_its_style_marks_and_pronunciations() -> None:
    script = voice_script.parse(MIC)
    assert [(ln.start, ln.end) for ln in script.lines] == [(0, 4), (4, 8), (8, 10)]   # the explanation is no line
    assert script.style == "hangat, santai, kayak ngobrol sama teman. Senyum pas ngomong."
    assert script.pronounce == {"SquidWings": "skuid-wings"}
    first, second, third = script.lines
    assert first.text == "Jujur ya... aku capek bayar langganan AI." and "pelan" in first.note and "turun" in first.note
    assert second.text == "Terus kepikiran, gimana kalau SquidWings jalan sendiri?"
    assert "skuid-wings" in second.spoken and "Tekankan kata: SquidWings" in second.note
    assert third.spoken.startswith("...") and "semangat" in third.note


def test_ai_studios_format_with_text_under_the_stamp() -> None:
    script = voice_script.parse("## Transcript:\n[00:00 - 00:03]\n\nJujur ya.\n\n\n[00:03 - 00:07]\n\nTerus kepikiran?\n")
    assert [(ln.start, ln.end, ln.text) for ln in script.lines] == [(0, 3, "Jujur ya."), (3, 7, "Terus kepikiran?")]
    plain = voice_script.parse("Halo semua, ini tanpa waktu.")
    assert len(plain.lines) == 1 and plain.lines[0].end is None


def test_lines_are_cut_where_the_take_breathes_even_when_a_name_is_misheard() -> None:
    lines = voice_script.parse("[00.00 - 00.04] Makanya kami bikin ini. AppCash.\n[00.04 - 00.08] Aplikasi kas kelas.\n").lines
    heard = [("Makanya", 0.0, 0.4), ("kami", 0.45, 0.7), ("bikin", 0.75, 1.0), ("ini.", 1.05, 1.3),
             ("AppGash.", 1.6, 2.1), ("Aplikasi", 2.9, 3.4), ("kas", 3.45, 3.6), ("kelas.", 3.65, 4.0)]
    (a_start, a_end), (b_start, b_end) = voice_script.boundaries(lines, heard, 4.2)
    assert a_end == b_start and 2.1 < a_end < 2.9          # "AppGash" stays with its line
    assert a_start < 0.01 and b_end == 4.2


def test_a_matched_last_word_stays_with_its_line_even_after_a_long_breath() -> None:
    lines = voice_script.parse("[00.00 - 00.03] Yang udah lunas. Beres.\n[00.03 - 00.06] Telat bayar?\n").lines
    heard = [("Yang", 0.0, 0.3), ("udah", 0.35, 0.6), ("lunas,", 0.65, 1.1), ("beres,", 1.76, 2.3),
             ("telat", 2.76, 3.1), ("bayar", 3.15, 3.5)]          # the breath before "beres" is the longer one
    (_, a_end), (b_start, _) = voice_script.boundaries(lines, heard, 3.8)
    assert a_end == b_start and 2.3 < a_end < 2.76


@needs_ffmpeg
async def test_every_line_lands_on_its_second_and_a_long_one_is_sped_up(tmp_path: Path) -> None:
    script = voice_script.parse("[00.00 - 00.02] satu dua.\n[00.03 - 00.05] tiga empat lima enam.\n")
    lengths = {"satu dua.": 1.0, "tiga empat lima enam.": 2.4}

    async def synth(text: str, note: str) -> bytes:
        return tone(lengths[text])

    dest = tmp_path / "voice.wav"
    report = await voice_script.build(script, synth, dest, total=6)
    first, second = report["lines"]
    assert first["placed"][0] == pytest.approx(0.08, abs=0.01) and first["speedup"] == 1.0
    assert second["placed"][0] == pytest.approx(3.08, abs=0.01) and 1.2 < second["speedup"] <= 1.3
    with wave.open(str(dest)) as track:
        assert track.getframerate() == 24000 and track.getnframes() / 24000 == pytest.approx(6, abs=0.05)
    assert (tmp_path / "voice.srt").read_text().startswith("1\n00:00:00,080 -->")
    assert report["spills_over"] == []


@needs_ffmpeg
def test_a_rendered_video_gets_the_voice_over_its_music(tmp_path: Path) -> None:
    exe = toolkit.ffmpeg_exe()
    assert exe
    video = tmp_path / "promo.mp4"
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-f", "lavfi",
                    "-i", "sine=f=220:d=2", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video)], check=True)
    voice = tmp_path / "voice.wav"
    voice.write_bytes(tone(1.5))
    out = tmp_path / "promo_suara.mp4"
    voice_script.add_to_video(video, voice, out)
    probe = subprocess.run([exe, "-hide_banner", "-i", str(out)], capture_output=True, text=True)
    assert "Video:" in probe.stderr and "Audio:" in probe.stderr


# --- What can go wrong, caught before or after the request ----------------------


def test_a_script_that_cannot_work_is_named_before_a_request_is_spent() -> None:
    script = voice_script.parse("[00.00 - 00.02] satu dua tiga empat lima enam tujuh delapan sembilan sepuluh.\n"
                                "[00.01 - 00.04] tumpang.\n[00.06 - 00.05] terbalik.\n[00.08 - 00.12] lewat.\n")
    said = " | ".join(voice_script.problems(script, total=10))
    assert "tidak muat" in said and "tumpang tindih" in said and "terbalik" in said and "melewati" in said
    fine = voice_script.parse("[00.00 - 00.04] Jujur ya, aku capek.\n[00.04 - 00.08] Terus kepikiran.\n")
    assert voice_script.problems(fine, total=8) == []


def placed(script_text: str, spans: list[tuple[float, float]]) -> list[voice_script.Line]:
    lines = voice_script.parse(script_text).lines
    for line, span in zip(lines, spans, strict=True):
        line.placed = span
    return lines


def test_listening_back_hears_a_word_cut_into_the_next_line() -> None:
    lines = placed("[00.00 - 00.03] Yang udah lunas. Beres.\n[00.03 - 00.06] Telat bayar?\n", [(0.08, 2.0), (3.08, 5.0)])
    heard = [("Yang", 0.1, 0.4), ("udah", 0.45, 0.8), ("lunas.", 0.9, 1.9), ("Beres!", 3.1, 3.6),
             ("Tolat", 3.7, 4.1), ("bayar.", 4.2, 4.9)]                 # whisper mishears "Telat": still the same line
    found = voice_script.issues(lines, heard)
    assert not found["ok"] and found["issues"] == [{"line": 1, "kind": "kata_pindah", "word": "beres", "belongs_to": 0,
                                                    "text": "Telat bayar?"}]


def test_a_word_whose_start_whisper_smears_into_the_gap_is_not_a_false_alarm() -> None:
    lines = placed("[00.00 - 00.05] terus ngecek hasilnya sendiri.\n[00.05 - 00.10] Tesnya gagal? Dia betulin.\n",
                   [(0.08, 4.9), (5.08, 9.0)])
    heard = [("terus", 0.1, 0.5), ("ngecek", 0.6, 1.2), ("hasilnya", 1.3, 2.0), ("sendiri.", 2.1, 4.8),
             ("Testnya", 4.1, 5.5), ("gagal,", 5.6, 6.2), ("dia", 6.3, 6.6), ("betulin.", 6.7, 8.9)]
    assert voice_script.issues(lines, heard)["ok"]


def test_a_line_that_was_never_spoken_is_heard_missing() -> None:
    lines = placed("[00.00 - 00.03] Halo semua.\n[00.03 - 00.06] Ini baris yang hilang.\n", [(0.08, 1.5), (3.08, 5.5)])
    found = voice_script.issues(lines, [("Halo", 0.1, 0.6), ("semua.", 0.7, 1.4)])
    assert [(i["line"], i["kind"]) for i in found["issues"]] == [(1, "tidak_terdengar")]


@needs_ffmpeg
async def test_a_line_heard_wrong_is_spoken_again_alone_and_the_track_rebuilt(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    script = voice_script.parse("[00.00 - 00.02] satu dua.\n[00.03 - 00.05] tiga empat.\n")
    asked: list[str] = []

    async def synth(text: str, note: str) -> bytes:
        asked.append(text)
        return tone(1.0)

    verdicts = [{"ok": False, "issues": [{"line": 1, "kind": "tidak_terdengar", "text": "tiga empat.", "heard": ""}],
                 "stray": []}, {"ok": True, "issues": [], "stray": []}]

    async def listen(*_: Any) -> dict[str, Any]:
        return verdicts.pop(0)

    monkeypatch.setattr(voice_script, "check_track", listen)
    report = await voice_script.build(script, synth, tmp_path / "voice.wav", total=6, check=True)
    assert asked == ["satu dua.", "tiga empat.", "tiga empat."]       # only the missing line again
    assert report["repaired"] == ["tiga empat."] and report["check"]["ok"]


@needs_ffmpeg
async def test_when_the_quota_ends_during_a_repair_the_track_is_kept(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    script = voice_script.parse("[00.00 - 00.02] satu dua.\n[00.03 - 00.05] tiga empat.\n")
    calls = 0

    async def synth(text: str, note: str) -> bytes:
        nonlocal calls
        calls += 1
        if calls > 2:
            raise gemini_tts.TTSError("Kuota harian Gemini TTS habis")
        return tone(1.0)

    async def listen(*_: Any) -> dict[str, Any]:
        return {"ok": False, "issues": [{"line": 0, "kind": "beda", "text": "satu dua.", "heard": "satu"}], "stray": []}

    monkeypatch.setattr(voice_script, "check_track", listen)
    report = await voice_script.build(script, synth, tmp_path / "voice.wav", total=6, check=True)
    assert (tmp_path / "voice.wav").is_file() and "Kuota harian" in report["repair_failed"]
    assert not report["check"]["ok"]


@needs_ffmpeg
async def test_a_line_too_long_for_its_range_never_talks_over_the_next(tmp_path: Path) -> None:
    script = voice_script.parse("[00.00 - 00.02] panjang sekali.\n[00.02 - 00.04] berikutnya.\n")
    lengths = {"panjang sekali.": 4.0, "berikutnya.": 1.0}

    async def synth(text: str, note: str) -> bytes:
        return tone(lengths[text])

    report = await voice_script.build(script, synth, tmp_path / "voice.wav", total=6)
    first, second = report["lines"]
    assert first["speedup"] == pytest.approx(1.3, abs=0.01) and second["placed"][0] >= first["placed"][1]
    assert report["shifted"] and report["shifted"][0]["text"] == "berikutnya." and report["spills_over"]


def test_lines_spoken_apart_come_out_equally_loud(tmp_path: Path) -> None:
    soft, loud = tmp_path / "a.wav", tmp_path / "b.wav"
    soft.write_bytes(tone(0.5, loud=3000))
    loud.write_bytes(tone(0.5, loud=12000))
    voice_script._level([soft, loud])
    peaks = [max(abs(x) for x in voice_script._samples(p)) for p in (soft, loud)]
    assert max(peaks) / min(peaks) < 2.1                           # was 4x apart


async def test_recut_without_a_kept_take_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.utils import whisper_stt
    from tools.toolbox.catalog import CATALOG

    monkeypatch.setattr(gemini_tts, "key", lambda: "test-key")
    monkeypatch.setattr(whisper_stt, "available", lambda: True)
    (tmp_path / "mic.txt").write_text("[00.00 - 00.02] halo.\n[00.02 - 00.04] lagi.", encoding="utf-8")
    result = await CATALOG["voice_track"].func(script="mic.txt", output="voice.wav", engine="gemini", mode="recut",
                                               workspace_path=str(tmp_path))
    assert result["ok"] is False and "rekaman utuh" in result["error"]


@needs_ffmpeg
async def test_adding_a_voice_never_overwrites_the_video_and_names_a_voice_cut_off(tmp_path: Path) -> None:
    from tools.toolbox.catalog import CATALOG

    exe = toolkit.ffmpeg_exe()
    assert exe
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-c:v",
                    "libx264", str(tmp_path / "promo.mp4")], check=True)
    (tmp_path / "voice.wav").write_bytes(tone(2.0))
    same = await CATALOG["video_add_voice"].func(video="promo.mp4", voice="voice.wav", output="promo.mp4",
                                                 workspace_path=str(tmp_path))
    assert same["ok"] is False and "tertimpa" in same["error"]
    longer = await CATALOG["video_add_voice"].func(video="promo.mp4", voice="voice.wav", workspace_path=str(tmp_path))
    assert (tmp_path / "promo_suara.mp4").is_file() and "terpotong" in longer["warnings"][0]


# --- Gemini TTS, with a fake HTTP client ----------------------------------------


class FakeResponse:
    def __init__(self, status: int, data: dict[str, Any]) -> None:
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self) -> dict[str, Any]:
        return self._data


def audio_answer(raw: bytes, mime: str) -> FakeResponse:
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": mime, "data": base64.b64encode(raw).decode()}}]}}]})


def fake_client(monkeypatch: pytest.MonkeyPatch, answers: list[FakeResponse], seen: list[dict[str, Any]]) -> None:
    class Client:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
            seen.append({"url": url, "headers": headers, "json": json})
            return answers.pop(0)

    monkeypatch.setattr(gemini_tts.httpx, "AsyncClient", Client)

    async def no_wait(_s: float) -> None:
        return None

    monkeypatch.setattr(gemini_tts.asyncio, "sleep", no_wait)


async def test_gemini_speaks_with_direction_and_turns_raw_pcm_into_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_tts, "key", lambda: "test-key")
    seen: list[dict[str, Any]] = []
    fake_client(monkeypatch, [audio_answer(b"\x00\x01" * 2400, "audio/L16;codec=pcm;rate=24000")], seen)
    wav = await gemini_tts.speak("Halo semua.", "hangat, santai", "kira")
    assert wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav)) as clip:
        assert clip.getframerate() == 24000 and clip.getnframes() == 2400
    body = seen[0]["json"]
    text = body["contents"][0]["parts"][0]["text"]
    assert text.startswith("## Director's notes:\nhangat, santai") and text.endswith("## Transcript:\nHalo semua.")
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kira"
    assert seen[0]["headers"] == {"x-goog-api-key": "test-key"} and "test-key" not in seen[0]["url"]


async def test_gemini_waits_out_the_per_minute_limit_and_names_the_daily_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_tts, "key", lambda: "test-key")
    minute = FakeResponse(429, {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]},
                                                       {"retryDelay": "21s"}]}})
    seen: list[dict[str, Any]] = []
    fake_client(monkeypatch, [minute, audio_answer(tone(0.2), "audio/wav")], seen)
    assert (await gemini_tts.speak("Tes."))[:4] == b"RIFF" and len(seen) == 2
    day = FakeResponse(429, {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]},
                                                    {"retryDelay": "64658s"}]}})
    fake_client(monkeypatch, [day], [])
    with pytest.raises(gemini_tts.TTSError, match="Kuota harian"):
        await gemini_tts.speak("Tes.")


async def test_without_a_key_the_tool_says_how_to_connect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tools.toolbox.catalog import CATALOG

    monkeypatch.setattr(gemini_tts, "key", lambda: "")
    (tmp_path / "mic.txt").write_text("[00.00 - 00.02] halo.", encoding="utf-8")
    result = await CATALOG["voice_track"].func(script="mic.txt", output="voice.wav", engine="gemini",
                                               workspace_path=str(tmp_path))
    assert result["ok"] is False and "Kredensial" in result["setup"]


def test_long_text_goes_in_sentence_pieces() -> None:
    text = " ".join(["Ini satu kalimat yang cukup panjang untuk diuji."] * 80)
    parts = gemini_tts.pieces(text, 500)
    assert len(parts) > 1 and all(len(p) <= 500 for p in parts) and " ".join(parts) == text


async def test_gemini_rides_out_a_busy_server_and_an_answer_without_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_tts, "key", lambda: "test-key")
    busy = FakeResponse(503, {"error": {"message": "The model is overloaded."}})
    silent = FakeResponse(200, {"candidates": [{"finishReason": "OTHER", "content": {"parts": [{"text": "hm"}]}}]})
    seen: list[dict[str, Any]] = []
    fake_client(monkeypatch, [busy, silent, audio_answer(tone(0.3), "audio/wav")], seen)
    assert (await gemini_tts.speak("Tes."))[:4] == b"RIFF" and len(seen) == 3
    fake_client(monkeypatch, [silent, silent], [])
    with pytest.raises(gemini_tts.TTSError, match="OTHER"):
        await gemini_tts.speak("Tes.")


async def test_gemini_asks_again_when_the_audio_stops_far_short_of_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_tts, "key", lambda: "test-key")
    text = "Ini kalimat yang cukup panjang untuk diucapkan dalam beberapa detik, bukan sekejap."
    seen: list[dict[str, Any]] = []
    fake_client(monkeypatch, [audio_answer(tone(0.2), "audio/wav"), audio_answer(tone(5.0), "audio/wav")], seen)
    wav = await gemini_tts.speak(text)
    assert len(seen) == 2 and gemini_tts._wav_seconds(wav) == pytest.approx(5.0, abs=0.01)
