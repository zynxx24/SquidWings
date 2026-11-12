"""Video and audio: download (yt-dlp), transcribe (faster-whisper), and make videos in code (HyperFrames, Remotion).

* ``media_info`` / ``media_download``: what a link holds, and the video, audio or subtitles saved in the
  workspace. Only public http(s) addresses, one item (never a whole playlist), with a size cap.
* ``transcribe``: speech to text on this machine, as .txt, .srt, .vtt (and word times as .json).
* ``video_new`` / ``video_check`` / ``video_render``: a HyperFrames project (HTML compositions) or a
  Remotion project (React), checked and rendered to MP4, then shown in the chat; ``video_docs`` is the
  official HyperFrames reference, read from the CLI that renders.
* ``media_tools_status``: which of these programs are installed and how to install the rest.

Every program runs in its own process group with a time limit, inside the session workspace.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tool.execute.execute_local_ops.path_guard import resolve_inside
from utils import toolkit, whisper_stt

from . import _files
from .catalog import group, param, spec
from .office_tools import deliver

group("media", "Video and audio: download (yt-dlp), transcribe (faster-whisper), make and render videos (HyperFrames, Remotion)",
      "youtube, yt, video youtube, unduh video, download video, download audio, unduh lagu, mp3, mp4, subtitle, "
      "subtitel, takarir, caption, yt-dlp, ytdlp, tiktok video, instagram reel, transkrip, transkripsi, transcribe, "
      "transcript, speech to text, suara ke teks, whisper, hyperframes, remotion, render video, bikin video, "
      "buat video, video dari html, video dari kode, motion graphics, intro video, explainer video")

MAX_DOWNLOAD = os.getenv("MEDIA_MAX_DOWNLOAD", "").strip() or "2G"
MAX_TRANSCRIBE_BYTES = 4 * 1024 ** 3
TRANSCRIPT_RETURN_CHARS = 15_000
_TEMPORARY = (".part", ".ytdl", ".temp", ".tmp")
REMOTION_TEMPLATES = ("blank", "hello-world", "tiktok", "audiogram", "still", "music-visualization", "three",
                      "skia", "overlay", "code-hike", "stargazer")
_COMPOSITION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
HF_RESOLUTIONS = ("landscape", "portrait", "square", "landscape-4k", "portrait-4k", "square-4k")


class MediaError(RuntimeError):
    pass


# ── running programs ─────────────────────────────────────────────────────────

async def _run(argv: list[str], cwd: Path, timeout_s: float, env: dict[str, str] | None = None) -> tuple[int, str, bool]:
    """(exit code, last part of the output, stopped at the time limit). The whole process group is stopped."""
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True, env={**os.environ, **(env or {})},
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        timed_out = False
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)       # npx starts Chrome and ffmpeg below it
        output, _ = await process.communicate()
        timed_out = True
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        raise
    return process.returncode or 0, (output or b"").decode("utf-8", "replace"), timed_out


def _tail(text: str, limit: int = 1500) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith(("@hf-progress", "[INFO]"))]
    return "\n".join(lines)[-limit:]


def _missing(key: str) -> dict[str, Any]:
    program = toolkit.BY_KEY[key]
    return {"ok": False, "error": f"{program.label} belum terpasang di mesin ini", "install": toolkit.install_hint(key)}


# ── links ────────────────────────────────────────────────────────────────────

def _public_url(url: str) -> str:
    """A public http(s) address, never this machine or its network (the router, a metadata endpoint)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MediaError("hanya link http atau https")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise MediaError(f"alamat {parsed.hostname} tidak ditemukan") from None
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]))
        if not address.is_global or address.is_multicast:
            raise MediaError(f"{parsed.hostname} bukan alamat publik")
    return url.strip()


def _ytdlp(*extra: str) -> list[str]:
    command = toolkit.ytdlp_command()
    if command is None:
        raise MediaError("yt-dlp belum terpasang. " + toolkit.install_hint("yt-dlp"))
    args = [*command, "--no-playlist", "--no-warnings", "--ignore-config"]
    if shutil.which("deno") is None and shutil.which("node"):
        args += ["--js-runtimes", "node"]          # YouTube needs a JavaScript runtime; deno is the default
    ffmpeg = toolkit.ffmpeg_exe()
    if ffmpeg:
        args += ["--ffmpeg-location", ffmpeg]
    return [*args, *extra]


@spec("media_info", "What a video or audio link holds, without downloading it: title, channel, length, upload date, "
      "the qualities on offer, and which subtitle languages exist (made by people or auto-generated). Works for "
      "YouTube and about 1800 other sites through yt-dlp. Needs internet.",
      "media", [param("url", description="A link to one video or track (http or https).")],
      keywords="info video, judul video, durasi video, video ini tentang apa, cek video, ada subtitle", lead=True,
      network=True)
async def media_info(url: str) -> dict[str, Any]:
    link = await asyncio.to_thread(_public_url, url)
    code, output, timed_out = await _run(_ytdlp("-J", "--skip-download", link), _files.workspace(None), 90)
    if code != 0 or timed_out:
        raise MediaError(f"yt-dlp tidak bisa membaca link ini: {_tail(output, 600)}")
    data = json.loads(output[output.find("{"):])
    heights = sorted({f["height"] for f in data.get("formats") or [] if isinstance(f.get("height"), int)})
    description = str(data.get("description") or "")
    return {
        "title": data.get("title"), "channel": data.get("channel") or data.get("uploader"),
        "duration_s": data.get("duration"), "upload_date": data.get("upload_date"), "views": data.get("view_count"),
        "is_live": bool(data.get("is_live")), "url": data.get("webpage_url") or link, "site": data.get("extractor_key"),
        "video_heights": heights, "has_audio": any(f.get("acodec") not in (None, "none") for f in data.get("formats") or []),
        "subtitles": sorted(data.get("subtitles") or {})[:30],
        "auto_subtitles": sorted(data.get("automatic_captions") or {})[:30],
        "chapters": [c.get("title") for c in data.get("chapters") or []][:30],
        "description": description[:600] + ("…" if len(description) > 600 else ""),
    }


def _new_files(folder: Path, before: set[Path]) -> list[Path]:
    found = [p for p in folder.rglob("*") if p.is_file() and p not in before and not p.name.endswith(_TEMPORARY)]
    return sorted(found, key=lambda p: p.stat().st_mtime)


@spec("media_download", "Download one video, its audio, or only its subtitles from a link (YouTube and about 1800 other "
      "sites, through yt-dlp) into the workspace. kind='video' saves an MP4 (max_height caps the quality), 'audio' an "
      "MP3, 'subtitles' only the subtitle files as .srt (cheapest way to get what is said). Never a whole playlist. "
      "Only for media the user may download: their own, licensed, public domain, or allowed by the site. Needs internet.",
      "media", [param("url", description="A link to one video or track."),
                param("kind", description="video, audio, or subtitles.", required=False, default="video",
                      enum=["video", "audio", "subtitles"]),
                param("max_height", "int", "Highest video height, e.g. 720 or 1080.", required=False, default=1080),
                param("subtitles", "bool", "Also save subtitles next to the video or audio.", required=False, default=False),
                param("sub_langs", description="Subtitle languages, e.g. 'id,en'.", required=False, default="id,en"),
                param("folder", description="Folder in the workspace.", required=False, default="downloads"),
                param("share", "bool", "Show the file in the chat.", required=False, default=False)],
      keywords="download youtube, unduh youtube, download video, simpan video, ambil audio, download mp3, ambil "
               "subtitle, download subtitle, rip audio", lead=True, workspace=True, network=True, read_only=False)
async def media_download(url: str, kind: str = "video", max_height: int = 1080, subtitles: bool = False,
                         sub_langs: str = "id,en", folder: str = "downloads", share: bool = False,
                         workspace_path: str | None = None) -> dict[str, Any]:
    link = await asyncio.to_thread(_public_url, url)
    root = _files.workspace(workspace_path)
    target = _files.destination(f"{folder.strip('/') or 'downloads'}/.keep", workspace_path).parent
    langs = ",".join(f"{lang.strip()}.*" for lang in sub_langs.split(",") if lang.strip()) or "id.*,en.*"
    subs = ["--write-subs", "--write-auto-subs", "--sub-langs", langs, "--convert-subs", "srt"]
    height = max(144, min(int(max_height), 4320))
    if kind == "video":
        extra = ["-f", f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b", "--merge-output-format", "mp4"]
    elif kind == "audio":
        extra = ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
    elif kind == "subtitles":
        extra = ["--skip-download", *subs]
    else:
        raise MediaError("kind harus video, audio, atau subtitles")
    if subtitles and kind != "subtitles":
        extra += subs
    before = {p for p in target.rglob("*") if p.is_file()}
    argv = _ytdlp(*extra, "--restrict-filenames", "--no-mtime", "--newline", "--no-progress",
                  "--max-filesize", MAX_DOWNLOAD, "-P", str(target), "-o", "%(title).80B [%(id)s].%(ext)s", link)
    code, output, timed_out = await _run(argv, target, 30 * 60)
    files = _new_files(target, before)
    if not files:
        reason = "waktu 30 menit habis" if timed_out else _tail(output, 900)
        if "max-filesize" in output.lower() or "larger than max-filesize" in output.lower():
            reason = f"file lebih besar dari batas {MAX_DOWNLOAD} (MEDIA_MAX_DOWNLOAD)"
        return {"ok": False, "error": f"tidak ada yang terunduh: {reason}"}
    saved = [{"path": _files.relative(p, workspace_path), "size_mb": round(p.stat().st_size / 1e6, 1)} for p in files]
    out: dict[str, Any] = {"ok": code == 0, "files": saved, "folder": _files.relative(target, workspace_path)}
    if code != 0:
        out["warning"] = _tail(output, 600)
    main = next((p for p in reversed(files) if p.suffix.lower() in {".mp4", ".mp3", ".webm", ".m4a", ".mkv"}), None)
    if main is not None:
        out["main"] = _files.relative(main, workspace_path)
        if share:
            out["share"] = deliver(main, str(root), True, main.stem)
    return out


# ── speech to text ───────────────────────────────────────────────────────────

def _transcribe_sync(source: Path, language: str, formats: list[str], words: bool, model: str) -> dict[str, Any]:
    result = whisper_stt.transcribe(source, language, words=words, model=model)
    folder = source.parent
    saved = []
    writers = {"txt": lambda: result.text + "\n", "srt": lambda: whisper_stt.to_srt(result.segments),
               "vtt": lambda: whisper_stt.to_vtt(result.segments)}
    for fmt in formats:
        if fmt in writers:
            path = folder / f"{source.stem}.{fmt}"
            path.write_text(writers[fmt](), encoding="utf-8")
            saved.append(path)
    if words:
        path = folder / f"{source.stem}.words.json"
        path.write_text(json.dumps([{"start": w.start, "end": w.end, "word": w.text}
                                    for s in result.segments for w in s.words], ensure_ascii=False), encoding="utf-8")
        saved.append(path)
    return {"result": result, "saved": saved}


@spec("transcribe", "Turn the speech in an audio or video file in the workspace into text, on this machine with "
      "faster-whisper (nothing is uploaded). Saves .txt and .srt next to the file (also .vtt, and word times as "
      ".words.json when words=true) and returns the text, the language heard, and the length. Long files take a "
      "while on a CPU; say so first. For a link, media_download(kind='subtitles') is faster when the video has them.",
      "media", [param("path", description="Audio or video file in the workspace."),
                param("language", description="Language code such as 'id' or 'en'; empty detects it.", required=False, default=""),
                param("formats", "list", "Files to write: txt, srt, vtt.", required=False, default=["txt", "srt"], items="str"),
                param("words", "bool", "Also time every word (for captions that follow the voice).", required=False, default=False),
                param("model", description="Whisper model: tiny, base, small, medium, large-v3, turbo. Empty uses "
                      "VOICE_STT_MODEL (default base).", required=False, default="")],
      keywords="transkrip, transkripsi, transcribe, transcript, speech to text, suara ke teks, rekaman ke teks, "
               "subtitle dari video, bikin subtitle, ubah audio jadi teks, notulen rekaman", lead=True, workspace=True,
      read_only=False)
async def transcribe(path: str, language: str = "", formats: list[str] | None = None, words: bool = False,
                     model: str = "", workspace_path: str | None = None) -> dict[str, Any]:
    if not whisper_stt.available():
        return _missing("faster-whisper")
    root = _files.workspace(workspace_path)
    source = resolve_inside(root, path, must_exist=True)
    if not source.is_file():
        raise MediaError(f"{path} bukan file")
    if source.stat().st_size > MAX_TRANSCRIBE_BYTES:
        raise MediaError(f"{path} lebih besar dari 4 GB")
    wanted = [f.strip().lower().lstrip(".") for f in (formats or ["txt", "srt"])]
    try:
        done = await asyncio.to_thread(_transcribe_sync, source, language, wanted, words, model)
    except whisper_stt.STTError as exc:
        return {"ok": False, "error": str(exc)}
    result = done["result"]
    text = result.text
    return {
        "ok": True, "language": result.language, "language_probability": result.language_probability,
        "duration_s": result.duration, "segments": len(result.segments), "model": whisper_stt.model_name(model),
        "saved": [_files.relative(p, workspace_path) for p in done["saved"]],
        "text": text[:TRANSCRIPT_RETURN_CHARS], "truncated": len(text) > TRANSCRIPT_RETURN_CHARS,
    }


# ── videos in code ───────────────────────────────────────────────────────────

def _hyperframes() -> list[str]:
    if shutil.which("hyperframes"):
        return ["hyperframes"]
    if toolkit.node_major() < toolkit.NODE_MINIMUM or not shutil.which("npx"):
        raise MediaError(f"HyperFrames butuh Node.js {toolkit.NODE_MINIMUM}+. " + toolkit.install_hint("node"))
    return ["npx", "--yes", f"{toolkit.HYPERFRAMES_PACKAGE}@latest"]


# The CLI installs AI skills into the user's home on init; this app brings its own instructions.
_HF_ENV = {"HYPERFRAMES_SKIP_SKILLS": "1", "CI": "1"}


def _engine(project: Path) -> str:
    if (project / "hyperframes.json").is_file():
        return "hyperframes"
    package = project / "package.json"
    if package.is_file():
        with contextlib.suppress(ValueError, OSError):
            data = json.loads(package.read_text(encoding="utf-8"))
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            if "remotion" in deps or "@remotion/cli" in deps:
                return "remotion"
    raise MediaError(f"{project.name} bukan proyek HyperFrames (hyperframes.json) atau Remotion (package.json dengan remotion)")


def _project(path: str, workspace_path: str | None) -> Path:
    folder = resolve_inside(_files.workspace(workspace_path), path, must_exist=True)
    if not folder.is_dir():
        raise MediaError(f"{path} bukan folder proyek")
    return folder


@spec("video_new", "Start a video project in code in the workspace. engine='hyperframes' (HeyGen HyperFrames): the "
      "video is HTML + CSS + GSAP-style animation in index.html, previewed in a browser and rendered to MP4; best "
      "for titles, explainers, captions, charts, social clips. engine='remotion': a React project (src/), for apps "
      "that already use React or need components and data-driven scenes. Both need Node.js; packages are fetched the "
      "first time. Then edit the files, call video_check, and video_render.",
      "media", [param("name", description="Folder for the project, e.g. 'videos/intro'."),
                param("engine", description="hyperframes or remotion.", required=False, default="hyperframes",
                      enum=["hyperframes", "remotion"]),
                param("example", description="HyperFrames example (e.g. blank, warm-grain, swiss-grid) or Remotion "
                      "template (blank, hello-world, tiktok, audiogram, still, music-visualization, three, skia, "
                      "overlay, code-hike).", required=False, default=""),
                param("resolution", description="HyperFrames canvas: landscape, portrait, square (and -4k).",
                      required=False, default=""),
                param("audio", description="HyperFrames: an audio file in the workspace to build the video around "
                      "(it is transcribed for captions).", required=False, default="")],
      keywords="proyek video, project video, mulai video, hyperframes init, remotion project, create video, bikin "
               "video dari html, video react", lead=True, workspace=True, network=True, read_only=False)
async def video_new(name: str, engine: str = "hyperframes", example: str = "", resolution: str = "", audio: str = "",
                    workspace_path: str | None = None) -> dict[str, Any]:
    root = _files.workspace(workspace_path)
    folder = _files.destination(f"{name.strip('/')}/.keep", workspace_path).parent
    if any(folder.iterdir()):
        raise MediaError(f"{name} sudah berisi file; pilih nama folder baru")
    folder.rmdir()      # the CLIs make the folder themselves
    if engine == "hyperframes":
        argv = [*_hyperframes(), "init", folder.name, "--non-interactive", "--skip-skills"]
        if example:
            argv.append(f"--example={example}")
        if resolution:
            if resolution not in HF_RESOLUTIONS:
                raise MediaError(f"resolution harus salah satu dari {', '.join(HF_RESOLUTIONS)}")
            argv.append(f"--resolution={resolution}")
        if audio:
            argv.append(f"--audio={resolve_inside(root, audio, must_exist=True)}")
        code, output, timed_out = await _run(argv, folder.parent, 10 * 60, _HF_ENV)
    elif engine == "remotion":
        if toolkit.node_major() < 18 or not shutil.which("npx"):
            return _missing("node")
        template = example or "blank"
        if template not in REMOTION_TEMPLATES:
            raise MediaError(f"template Remotion harus salah satu dari {', '.join(REMOTION_TEMPLATES)}")
        argv = ["npx", "--yes", "create-video@latest", "--yes", f"--{template}", folder.name]
        code, output, timed_out = await _run(argv, folder.parent, 15 * 60, {"CI": "1"})
        if code == 0 and folder.is_dir() and not (folder / "node_modules").exists():
            code, more, timed_out = await _run(["npm", "install", "--no-fund", "--no-audit"], folder, 15 * 60)
            output += more
    else:
        raise MediaError("engine harus hyperframes atau remotion")
    if code != 0 or timed_out or not folder.is_dir():
        return {"ok": False, "error": "proyek tidak terbentuk" + (" (waktu habis)" if timed_out else ""),
                "log_tail": _tail(output)}
    files = sorted(_files.relative(p, workspace_path) for p in folder.rglob("*")
                   if p.is_file() and "node_modules" not in p.parts and ".git" not in p.parts)[:40]
    if engine == "hyperframes":
        step = "baca video_docs('data-attributes') dan AGENTS.md proyek, edit index.html"
    else:
        step = "edit src/Root.tsx (daftar komposisi) dan komponennya di src/"
    return {"ok": True, "engine": engine, "project": _files.relative(folder, workspace_path), "files": files,
            "next": f"{step}, lalu video_check(project) dan video_render(project)"}


async def _remotion_compositions(project: Path) -> list[str]:
    code, output, _ = await _run(["npx", "--yes", "remotion", "compositions", "--quiet"], project, 5 * 60)
    if code != 0:
        raise MediaError(f"Remotion tidak bisa membaca komposisinya: {_tail(output, 800)}")
    # --quiet prints only the ids, space-separated, on the last line; the first run logs a Chrome download above it.
    last = next((line for line in reversed(output.splitlines()) if line.strip()), "")
    return [word for word in last.split() if _COMPOSITION_ID.fullmatch(word)]


@spec("video_check", "Check a video project before rendering. HyperFrames: lints the compositions (errors, "
      "warnings, unmounted compositions) and returns the findings. Remotion: lists the composition ids that can be "
      "rendered. Fix every error before video_render.",
      "media", [param("project", description="The project folder in the workspace.")],
      keywords="cek video, lint video, periksa video, hyperframes lint, remotion compositions", workspace=True)
async def video_check(project: str, workspace_path: str | None = None) -> dict[str, Any]:
    folder = _project(project, workspace_path)
    engine = _engine(folder)
    if engine == "remotion":
        return {"ok": True, "engine": engine, "compositions": await _remotion_compositions(folder)}
    code, output, _ = await _run([*_hyperframes(), "lint", ".", "--json"], folder, 5 * 60, _HF_ENV)
    try:
        report = json.loads(output[output.find("{"):])
    except ValueError:
        return {"ok": False, "engine": engine, "error": "lint tidak memberi hasil JSON", "log_tail": _tail(output)}
    return {"ok": bool(report.get("ok")) and code == 0, "engine": engine, "errors": report.get("errorCount", 0),
            "warnings": report.get("warningCount", 0), "findings": (report.get("findings") or [])[:25],
            "unmounted": report.get("unmountedCompositions") or []}


@spec("video_render", "Render a HyperFrames or Remotion project in the workspace to an MP4 and show it in the chat. "
      "The engine is read from the project. composition: HyperFrames composition file (default index.html) or the "
      "Remotion composition id (required when the project has more than one; video_check lists them). Rendering takes from seconds to many minutes; say so first.",
      "media", [param("project", description="The project folder in the workspace."),
                param("output", description="MP4 path in the workspace; default renders/<project>.mp4.", required=False, default=""),
                param("composition", required=False, default=""),
                param("share", "bool", "Show the video in the chat.", required=False, default=True),
                param("max_minutes", "int", "Stop after this long (1-120).", required=False, default=30)],
      keywords="render video, render mp4, export video, jadikan mp4, hyperframes render, remotion render", lead=True,
      workspace=True, read_only=False)
async def video_render(project: str, output: str = "", composition: str = "", share: bool = True, max_minutes: int = 30,
                       workspace_path: str | None = None) -> dict[str, Any]:
    folder = _project(project, workspace_path)
    engine = _engine(folder)
    dest = _files.destination(output or f"renders/{folder.name}.mp4", workspace_path)
    if dest.suffix.lower() not in {".mp4", ".webm", ".mov"}:
        raise MediaError("output harus .mp4, .webm, atau .mov")
    limit = max(1, min(int(max_minutes), 120)) * 60
    if engine == "hyperframes":
        argv = [*_hyperframes(), "render", "-o", str(dest)]
        if composition:
            argv += ["-c", str(resolve_inside(folder, composition, must_exist=True))]
        code, log, timed_out = await _run(argv, folder, limit, _HF_ENV)
    else:
        target = composition
        if not target:
            ids = await _remotion_compositions(folder)
            if not ids:
                raise MediaError("proyek Remotion ini tidak punya komposisi")
            if len(ids) > 1:
                return {"ok": False, "engine": engine, "compositions": ids,
                        "error": "proyek ini punya beberapa komposisi; panggil lagi dengan composition=<salah satu id>"}
            target = ids[0]
        code, log, timed_out = await _run(["npx", "--yes", "remotion", "render", target, str(dest)], folder, limit)
    if code != 0 or timed_out or not dest.is_file():
        return {"ok": False, "engine": engine, "error": "render berhenti karena waktu habis" if timed_out else "render gagal",
                "log_tail": _tail(log)}
    root = _files.workspace(workspace_path)
    return {**deliver(dest, str(root), share, dest.stem), "engine": engine}


@spec("video_docs", "The official HyperFrames reference for one topic, from the installed CLI (always the version "
      "that renders): data-attributes, compositions, gsap, audio, transcribe, rendering, examples, motion-blur, "
      "troubleshooting. Read data-attributes and compositions before writing a composition. Empty topic lists them. "
      "For Remotion, read https://www.remotion.dev/docs with web_read instead.",
      "media", [param("topic", required=False, default="")],
      keywords="dokumentasi hyperframes, hyperframes docs, cara pakai hyperframes, data-composition-id, gsap timeline")
async def video_docs(topic: str = "") -> dict[str, Any]:
    name = topic.strip().lower()
    if name and not re.fullmatch(r"[a-z][a-z-]{1,40}", name):
        raise MediaError("topic hanya huruf dan tanda minus, mis. data-attributes")
    code, output, timed_out = await _run([*_hyperframes(), "docs", *([name] if name else [])],
                                         _files.workspace(None), 120, _HF_ENV)
    if code != 0 or timed_out:
        return {"ok": False, "error": _tail(output, 600) or "docs tidak bisa dibaca"}
    return {"ok": True, "topic": name or "(daftar topik)", "text": output.strip()[:12_000]}


@spec("media_tools_status", "Which media programs are installed on this machine (ffmpeg, yt-dlp, faster-whisper, "
      "Node.js, Deno, HyperFrames, Remotion, Homebrew), their versions, and the exact command to install what is "
      "missing. Call it before telling the user something cannot be done.",
      "media", [], keywords="cek tools media, apa yang terpasang, install yt-dlp, install ffmpeg, install whisper, "
                            "homebrew, brew")
async def media_tools_status() -> dict[str, Any]:
    entries = await asyncio.to_thread(toolkit.status)
    return {"ok": True, "programs": entries,
            "missing": [e["key"] for e in entries if not e["ready"] and not e["optional"]]}
