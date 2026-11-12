"""Talking to the terminal agent: recording, speech to text, reading answers aloud. No microphone, no network."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import main
from config import credentials
from utils import voice_io


def test_speakable_drops_code_links_and_markdown() -> None:
    text = "## Hasil\n\nIni **penting**: lihat [dokumen](https://x.io/a) dan https://x.io/b.\n\n```py\nprint(1)\n```\n- satu\n- dua"
    spoken = voice_io.speakable(text)
    assert "```" not in spoken and "print" not in spoken and "http" not in spoken and "**" not in spoken
    assert "dokumen" in spoken and "penting" in spoken and "satu" in spoken
    assert "ada kode di layar" in spoken


def test_speakable_cuts_a_long_reply_at_a_sentence() -> None:
    text = "Kalimat pendek. " * 200
    spoken = voice_io.speakable(text, limit=100)
    assert len(spoken) <= 100 and spoken.endswith(".")


def test_speakable_of_only_code_is_a_short_note() -> None:
    assert voice_io.speakable("```\nx=1\n```") == "(ada kode di layar)"


def test_capabilities_reflect_what_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "_recorder", lambda: "arecord")
    monkeypatch.setattr(voice_io, "_player", lambda: None)
    monkeypatch.setattr(voice_io, "_stt_engine", lambda: "google")
    monkeypatch.setattr(credentials, "get", lambda key: "k" if key == "elevenlabs_api_key" else "")
    caps = voice_io.capabilities()
    assert caps.can_listen and caps.tts and not caps.can_speak  # a key without a player cannot speak


def test_stt_prefers_the_local_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "_module_available", lambda name: True)
    assert voice_io._stt_engine() == "faster-whisper"
    monkeypatch.setattr(voice_io, "_module_available", lambda name: name == "speech_recognition")
    assert voice_io._stt_engine() == "google"
    monkeypatch.setattr(voice_io, "_module_available", lambda name: False)
    assert voice_io._stt_engine() is None


def test_record_without_a_recorder_says_what_to_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(voice_io, "_recorder", lambda: None)
    with pytest.raises(voice_io.VoiceIOError, match="alsa-utils"):
        voice_io.record(tmp_path / "a.wav", lambda: None)


def test_a_recorder_that_dies_at_once_does_not_wait_for_enter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(voice_io, "_recorder", lambda: "arecord")
    monkeypatch.setattr(voice_io, "_record_command", lambda r, p: ["sh", "-c", "echo 'no such device' >&2; exit 1"])
    waited = []
    with pytest.raises(voice_io.VoiceIOError, match="no such device"):
        voice_io.record(tmp_path / "a.wav", lambda: waited.append(1))
    assert waited == []


def test_record_stops_the_recorder_when_enter_is_pressed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dest = tmp_path / "a.wav"
    # A stand-in recorder: writes some bytes, then sleeps until the SIGINT that stops a real arecord.
    script = f"python3 -c \"import time,sys; open(sys.argv[1],'wb').write(b'x'*4000); time.sleep(30)\" {dest}"
    monkeypatch.setattr(voice_io, "_recorder", lambda: "arecord")
    monkeypatch.setattr(voice_io, "_record_command", lambda r, p: ["sh", "-c", f"exec {script}"])
    assert voice_io.record(dest, lambda: None) == dest


def test_an_empty_recording_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(voice_io, "_recorder", lambda: "arecord")
    monkeypatch.setattr(voice_io, "_record_command", lambda r, p: ["sleep", "30"])
    with pytest.raises(voice_io.VoiceIOError, match="kosong"):
        voice_io.record(tmp_path / "none.wav", lambda: None)


def test_transcribe_without_an_engine_says_how_to_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(voice_io, "_stt_engine", lambda: None)
    with pytest.raises(voice_io.VoiceIOError, match="faster-whisper"):
        voice_io.transcribe(tmp_path / "a.wav")


def test_listen_reports_progress_between_recording_and_recognition(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(voice_io, "record", lambda path, wait, secs: (order.append("record"), path)[1])
    monkeypatch.setattr(voice_io, "transcribe", lambda path: (order.append("transcribe"), "halo dunia")[1])
    assert voice_io.listen(lambda: None, on_recorded=lambda: order.append("recorded")) == "halo dunia"
    assert order == ["record", "recorded", "transcribe"]


def test_play_command_per_player(tmp_path: Path) -> None:
    mp3 = tmp_path / "a.mp3"
    assert voice_io._play_command("ffplay", mp3)[0] == "ffplay"
    assert "--no-video" in voice_io._play_command("mpv", mp3)
    assert voice_io._play_command("ffmpeg", mp3)[:2] == ["sh", "-c"]


def test_say_skips_text_with_nothing_to_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a, **k):
        raise AssertionError("must not synthesize")

    monkeypatch.setattr(voice_io, "synthesize", boom)
    assert asyncio.run(voice_io.say("   ")) is False


def test_say_synthesizes_then_plays(monkeypatch: pytest.MonkeyPatch) -> None:
    spoken: list[str] = []
    played: list[Path] = []

    async def fake(text: str, dest: Path, voice: str = "") -> Path:
        spoken.append(text)
        dest.write_bytes(b"mp3")
        return dest

    monkeypatch.setattr(voice_io, "synthesize", fake)
    monkeypatch.setattr(voice_io, "play", lambda path: played.append(path))
    assert asyncio.run(voice_io.say("Halo **dunia**")) is True
    assert spoken == ["Halo dunia"] and len(played) == 1


def test_synthesize_without_a_key_explains_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(credentials, "get", lambda key: "")
    with pytest.raises(voice_io.VoiceIOError, match="Kredensial"):
        asyncio.run(voice_io.synthesize("halo", tmp_path / "a.mp3"))


# ── the /voice command in the chat loop ──────────────────────────────────────

def _caps(**kw) -> voice_io.Capabilities:
    base = {"recorder": "arecord", "player": "ffplay", "stt": "faster-whisper", "tts": True}
    return voice_io.Capabilities(**{**base, **kw})


def run_command(arg: str, state: main._VoiceState) -> str | None:
    return asyncio.run(main._voice_command(arg, state))


def test_voice_on_needs_a_key_and_a_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "capabilities", lambda: _caps(tts=False))
    state = main._VoiceState()
    assert run_command("on", state) is None and state.speak is False
    monkeypatch.setattr(voice_io, "capabilities", lambda: _caps())
    assert run_command("on", state) is None and state.speak is True
    assert run_command("off", state) is None and state.speak is False


def test_voice_records_and_sends_what_was_heard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "capabilities", lambda: _caps())
    monkeypatch.setattr(voice_io, "listen", lambda wait, secs, done: "buatkan file hello.py")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert run_command("", main._VoiceState()) == "buatkan file hello.py"


def test_voice_lets_the_user_fix_or_cancel_what_was_heard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "capabilities", lambda: _caps())
    monkeypatch.setattr(voice_io, "listen", lambda wait, secs, done: "buat file hallo.py")
    monkeypatch.setattr("builtins.input", lambda prompt="": "buat file hello.py")
    assert run_command("", main._VoiceState()) == "buat file hello.py"
    monkeypatch.setattr("builtins.input", lambda prompt="": "x")
    assert run_command("", main._VoiceState()) is None


def test_voice_without_a_microphone_stays_in_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "capabilities", lambda: _caps(recorder=None))
    assert run_command("", main._VoiceState()) is None


def test_voice_with_no_speech_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "capabilities", lambda: _caps())
    monkeypatch.setattr(voice_io, "listen", lambda wait, secs, done: "")
    assert run_command("", main._VoiceState()) is None


def test_voice_errors_do_not_leave_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_io, "capabilities", lambda: _caps())

    def broken(wait, secs, done):
        raise voice_io.VoiceIOError("Mikrofon tidak bisa dipakai")

    monkeypatch.setattr(voice_io, "listen", broken)
    assert run_command("", main._VoiceState()) is None


def test_a_failed_read_aloud_is_only_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(text: str, voice: str = "") -> bool:
        raise voice_io.VoiceIOError("kuota habis")

    monkeypatch.setattr(voice_io, "say", fail)
    asyncio.run(main._speak_reply("halo"))  # must not raise
