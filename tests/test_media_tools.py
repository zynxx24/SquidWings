"""Media tools: download, transcribe, video projects. Offline: programs and models are stubbed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tool.execute.execute_toolbox import CATALOG
from tool.execute.execute_toolbox import media_tools as media
from utils import toolkit, whisper_stt


def call(tool: str, **kwargs: Any) -> Any:
    return CATALOG[tool].func(**kwargs)


# ── links ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1:8080/", "http://localhost/x", "ftp://example.com/a"])
def test_only_public_web_links_are_fetched(url: str) -> None:
    with pytest.raises(media.MediaError):
        media._public_url(url)


def test_ytdlp_uses_node_for_youtube_when_deno_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolkit, "ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(toolkit, "ffmpeg_exe", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.shutil, "which", lambda name: None if name == "deno" else f"/usr/bin/{name}")
    argv = media._ytdlp("-J")
    assert argv[argv.index("--js-runtimes") + 1] == "node"
    assert "--no-playlist" in argv and argv[argv.index("--ffmpeg-location") + 1] == "/usr/bin/ffmpeg"


async def test_download_reports_the_new_files_inside_the_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "_public_url", lambda url: url)
    monkeypatch.setattr(toolkit, "ytdlp_command", lambda: ["yt-dlp"])
    seen: dict[str, Any] = {}

    async def fake_run(argv: list[str], cwd: Path, timeout_s: float, env: Any = None) -> tuple[int, str, bool]:
        seen["argv"] = argv
        folder = Path(argv[argv.index("-P") + 1])
        (folder / "Lagu [abc].mp3").write_bytes(b"x" * 2000)
        (folder / "Lagu [abc].mp3.part").write_bytes(b"x")      # left over: never reported
        return 0, "", False

    monkeypatch.setattr(media, "_run", fake_run)
    out = await call("media_download", url="https://www.youtube.com/watch?v=abc", kind="audio", workspace_path=str(tmp_path))
    assert out["ok"] and out["main"] == "downloads/Lagu [abc].mp3" and len(out["files"]) == 1
    assert "-x" in seen["argv"] and "--max-filesize" in seen["argv"]


async def test_a_download_that_saved_nothing_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "_public_url", lambda url: url)
    monkeypatch.setattr(toolkit, "ytdlp_command", lambda: ["yt-dlp"])

    async def fake_run(*_: Any, **__: Any) -> tuple[int, str, bool]:
        return 1, "ERROR: Video unavailable", False

    monkeypatch.setattr(media, "_run", fake_run)
    out = await call("media_download", url="https://x.example/v", workspace_path=str(tmp_path))
    assert out["ok"] is False and "Video unavailable" in out["error"]


# ── speech to text ───────────────────────────────────────────────────────────

def test_subtitle_formats() -> None:
    segments = [whisper_stt.Segment(0.0, 1.5, "Halo semua"), whisper_stt.Segment(61.25, 3725.0, "sampai jumpa")]
    assert whisper_stt.to_srt(segments).splitlines()[:3] == ["1", "00:00:00,000 --> 00:00:01,500", "Halo semua"]
    assert "00:01:01.250 --> 01:02:05.000" in whisper_stt.to_vtt(segments)
    assert whisper_stt.to_vtt(segments).startswith("WEBVTT\n")


async def test_transcribe_without_faster_whisper_says_how_to_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whisper_stt, "available", lambda: False)
    out = await call("transcribe", path="a.wav", workspace_path=str(tmp_path))
    assert out["ok"] is False and "faster-whisper" in out["install"]


async def test_transcribe_writes_the_files_next_to_the_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "rapat.m4a").write_bytes(b"audio")
    monkeypatch.setattr(whisper_stt, "available", lambda: True)
    words = [whisper_stt.Word(0.0, 0.4, "Halo")]
    fake = whisper_stt.Transcript("Halo semua", "id", 0.98, 2.0, [whisper_stt.Segment(0.0, 2.0, "Halo semua", words)])
    monkeypatch.setattr(whisper_stt, "transcribe", lambda *a, **k: fake)
    out = await call("transcribe", path="rapat.m4a", formats=["txt", "srt"], words=True, workspace_path=str(tmp_path))
    assert out["ok"] and out["language"] == "id" and out["text"] == "Halo semua"
    assert set(out["saved"]) == {"rapat.txt", "rapat.srt", "rapat.words.json"}
    assert json.loads((tmp_path / "rapat.words.json").read_text())[0]["word"] == "Halo"


async def test_transcribe_stays_inside_the_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whisper_stt, "available", lambda: True)
    out = await call("transcribe", path="../../etc/hostname", workspace_path=str(tmp_path))
    assert out["ok"] is False


# ── video projects ───────────────────────────────────────────────────────────

def test_the_engine_is_read_from_the_project(tmp_path: Path) -> None:
    hf, rm, other = tmp_path / "hf", tmp_path / "rm", tmp_path / "other"
    for folder in (hf, rm, other):
        folder.mkdir()
    (hf / "hyperframes.json").write_text("{}")
    (rm / "package.json").write_text(json.dumps({"dependencies": {"remotion": "4.0.0", "react": "19"}}))
    (other / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    assert media._engine(hf) == "hyperframes" and media._engine(rm) == "remotion"
    with pytest.raises(media.MediaError):
        media._engine(other)


async def test_remotion_ids_come_from_the_last_line_not_the_chrome_download_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_run(*_: Any, **__: Any) -> tuple[int, str, bool]:
        return 0, "Downloading Chrome Headless Shell 12 Mb\nGot Headless Shell\nLogo Title HelloWorld\n", False

    monkeypatch.setattr(media, "_run", fake_run)
    assert await media._remotion_compositions(tmp_path) == ["Logo", "Title", "HelloWorld"]


async def test_render_asks_which_remotion_composition_when_there_are_several(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "videos" / "react"
    project.mkdir(parents=True)
    (project / "package.json").write_text(json.dumps({"dependencies": {"remotion": "4"}}))

    async def ids(_: Path) -> list[str]:
        return ["Logo", "HelloWorld"]

    monkeypatch.setattr(media, "_remotion_compositions", ids)
    out = await call("video_render", project="videos/react", workspace_path=str(tmp_path))
    assert out["ok"] is False and out["compositions"] == ["Logo", "HelloWorld"]


async def test_a_new_project_never_lands_in_a_folder_with_files(tmp_path: Path) -> None:
    (tmp_path / "videos" / "intro").mkdir(parents=True)
    (tmp_path / "videos" / "intro" / "index.html").write_text("<html></html>")
    out = await call("video_new", name="videos/intro", workspace_path=str(tmp_path))
    assert out["ok"] is False and "sudah berisi" in out["error"]


async def test_hyperframes_init_never_installs_skills_in_the_users_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(media, "_hyperframes", lambda: ["hyperframes"])

    async def fake_run(argv: list[str], cwd: Path, timeout_s: float, env: Any = None) -> tuple[int, str, bool]:
        seen.update(argv=argv, env=env)
        (cwd / argv[2]).mkdir()
        (cwd / argv[2] / "hyperframes.json").write_text("{}")
        return 0, "", False

    monkeypatch.setattr(media, "_run", fake_run)
    out = await call("video_new", name="videos/intro", resolution="portrait", workspace_path=str(tmp_path))
    assert out["ok"] and "--skip-skills" in seen["argv"] and seen["env"]["HYPERFRAMES_SKIP_SKILLS"] == "1"
    assert "--resolution=portrait" in seen["argv"]


# ── the programs ─────────────────────────────────────────────────────────────

def test_versions_are_compared_by_number() -> None:
    assert toolkit._meets("2026.10.26", "2026.1") and not toolkit._meets("2024.04.09", "2026.1")
    assert toolkit._meets("25.2.1", "22") and not toolkit._meets("20.11.0", "22")
    assert toolkit._meets("per proyek", "22")


def test_status_lists_every_program_with_a_way_to_get_it() -> None:
    entries = {e["key"]: e for e in toolkit.status()}
    assert set(entries) == {"ffmpeg", "yt-dlp", "faster-whisper", "node", "deno", "hyperframes", "remotion", "homebrew"}
    assert all(e.get("install") for e in entries.values() if not e["ready"])


def test_installing_without_any_installer_gives_the_manual_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolkit, "_manager_ready", lambda manager: False)
    out = toolkit.install("ffmpeg")
    assert out["ok"] is False and "apt install ffmpeg" in out["manual"]
    assert toolkit.install("nope")["ok"] is False
    assert "install.sh" in toolkit.install("homebrew")["manual"]      # never run for the user: it asks for sudo


def test_homebrew_is_found_without_brew_shellenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server started from a shell that never ran `brew shellenv` still finds what brew installed."""
    brew = tmp_path / "linuxbrew" / "bin" / "brew"
    brew.parent.mkdir(parents=True)
    brew.write_text("#!/bin/sh\n")
    brew.chmod(0o755)
    monkeypatch.setattr(toolkit, "HOMEBREW_PREFIXES", (str(tmp_path / "missing"), str(tmp_path / "linuxbrew")))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    toolkit._brew_on_path()
    toolkit._brew_on_path()                                   # twice: added once
    assert toolkit.homebrew_prefix() == str(tmp_path / "linuxbrew")
    assert toolkit.os.environ["PATH"].split(":") == ["/usr/bin", "/bin", str(brew.parent)]   # after the system's
