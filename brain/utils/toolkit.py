"""The external programs the media tools use, how to tell they are there, and how to install them.

``python main.py tools`` shows this list, ``python main.py tools install <name|all>`` installs what
is missing. Installs never use sudo: Python packages go into this app's own environment, npm
packages into the user's global npm, and system programs through Homebrew when it is installed.
Without Homebrew the exact command to run by hand is shown instead.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_PROBE_TIMEOUT = 20
_INSTALL_TIMEOUT = 1200
NODE_MINIMUM = 22          # HyperFrames needs Node 22+; Remotion runs on it too
HYPERFRAMES_PACKAGE = "hyperframes"
HOMEBREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
# Where Homebrew puts itself: Linux (shared, then per user), Apple silicon, Intel macOS.
HOMEBREW_PREFIXES = ("/home/linuxbrew/.linuxbrew", "~/.linuxbrew", "/opt/homebrew", "/usr/local")


def homebrew_prefix() -> str | None:
    """Homebrew's folder, found even when the shell that started this app never ran ``brew shellenv``."""
    for prefix in HOMEBREW_PREFIXES:
        folder = os.path.expanduser(prefix)
        if os.access(os.path.join(folder, "bin", "brew"), os.X_OK):
            return folder
    return None


def _brew_on_path() -> None:
    """Put Homebrew's bin after everything already on PATH, so programs installed with it are found
    and nothing the system provides is shadowed."""
    prefix = homebrew_prefix()
    if prefix is None:
        return
    parts = os.environ.get("PATH", "").split(os.pathsep)
    extra = [d for d in (os.path.join(prefix, "bin"), os.path.join(prefix, "sbin")) if d not in parts and os.path.isdir(d)]
    if extra:
        os.environ["PATH"] = os.pathsep.join([*parts, *extra])


_brew_on_path()


@dataclass(frozen=True)
class Found:
    version: str
    source: str            # where it comes from: "PATH", "this app's Python", "bundled", "npx"...


@dataclass(frozen=True)
class Program:
    key: str
    label: str
    purpose: str
    probe: Callable[[], Found | None]
    installs: tuple[tuple[str, tuple[str, ...]], ...]   # (manager, argv), tried in order
    manual: str
    minimum: str = ""
    optional: bool = False


def _run(argv: list[str], timeout: float = _PROBE_TIMEOUT) -> str | None:
    if shutil.which(argv[0]) is None and argv[0] != sys.executable:
        return None
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout or "") + (done.stderr or "") if done.returncode == 0 else None


def _version_in(text: str | None) -> str | None:
    match = re.search(r"\d+(?:\.\d+)+", text or "")
    return match.group(0) if match else None


def _package(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


# ── where each program is found ──────────────────────────────────────────────

def ffmpeg_exe() -> str | None:
    """ffmpeg on PATH, else the copy bundled with imageio-ffmpeg (a requirement), else None."""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    if importlib.util.find_spec("imageio_ffmpeg") is not None:
        try:
            import imageio_ffmpeg

            return str(imageio_ffmpeg.get_ffmpeg_exe())
        except (RuntimeError, OSError):
            return None
    return None


def _probe_ffmpeg() -> Found | None:
    on_path = shutil.which("ffmpeg")
    if on_path:
        version = _version_in(_run([on_path, "-version"]))
        return Found(version or "?", "PATH")
    bundled = ffmpeg_exe()
    return Found(_version_in(_run([bundled, "-version"])) or "?", "bundled (imageio-ffmpeg)") if bundled else None


def ytdlp_command() -> list[str] | None:
    """How to run yt-dlp: this app's own (kept current), else one on PATH."""
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    on_path = shutil.which("yt-dlp")
    return [on_path] if on_path else None


def _probe_ytdlp() -> Found | None:
    version = _package("yt-dlp")
    if version:
        return Found(version, "this app's Python")
    on_path = shutil.which("yt-dlp")
    return Found(_version_in(_run([on_path, "--version"])) or "?", f"PATH ({on_path})") if on_path else None


def _probe_package(name: str) -> Callable[[], Found | None]:
    def probe() -> Found | None:
        version = _package(name)
        return Found(version, "this app's Python") if version else None
    return probe


def _probe_command(command: str, *args: str) -> Callable[[], Found | None]:
    def probe() -> Found | None:
        text = _run([command, *args])
        return Found(_version_in(text) or "?", "PATH") if text is not None else None
    return probe


def node_major() -> int:
    version = _version_in(_run(["node", "--version"]))
    return int(version.split(".")[0]) if version else 0


def _probe_hyperframes() -> Found | None:
    installed = _version_in(_run(["hyperframes", "--version"]))
    if installed:
        return Found(installed, "global npm")
    return Found("latest", "npx (diunduh saat pertama dipakai)") if node_major() >= NODE_MINIMUM and shutil.which("npx") else None


def _probe_remotion() -> Found | None:
    return Found("per proyek", "npx + package.json proyeknya") if node_major() >= 18 and shutil.which("npx") else None


def _pip() -> tuple[str, ...]:
    """pip of this app's environment. A venv made by uv has no pip, so uv installs into it then."""
    if importlib.util.find_spec("pip") is not None:
        return (sys.executable, "-m", "pip", "install", "-U")
    return ("uv", "pip", "install", "--python", sys.executable, "-U")


PROGRAMS: tuple[Program, ...] = (
    Program("ffmpeg", "ffmpeg", "potong, ubah, dan gabung audio/video; dipakai yt-dlp dan render video",
            _probe_ffmpeg, (("brew", ("brew", "install", "ffmpeg")),), "sudo apt install ffmpeg"),
    Program("yt-dlp", "yt-dlp", "unduh video, audio, dan subtitle dari YouTube dan ribuan situs lain",
            _probe_ytdlp, (("pip", (*_pip(), "yt-dlp[default]")),), f"{' '.join(_pip())} 'yt-dlp[default]'",
            minimum="2026.1"),
    Program("faster-whisper", "faster-whisper", "transkripsi suara ke teks secara lokal (rekaman, video, upload)",
            _probe_package("faster-whisper"), (("pip", (*_pip(), "faster-whisper")),),
            f"{' '.join(_pip())} faster-whisper"),
    Program("node", "Node.js", f"menjalankan HyperFrames (Node {NODE_MINIMUM}+) dan Remotion",
            _probe_command("node", "--version"), (("brew", ("brew", "install", "node")),),
            "pasang lewat nvm: https://github.com/nvm-sh/nvm, lalu: nvm install --lts", minimum=str(NODE_MINIMUM)),
    Program("deno", "Deno", "runtime JavaScript yang paling disukai yt-dlp untuk YouTube (Node juga bisa)",
            _probe_command("deno", "--version"), (("brew", ("brew", "install", "deno")),),
            "curl -fsSL https://deno.land/install.sh | sh", optional=True),
    Program("hyperframes", "HyperFrames", "video dari HTML (HeyGen): tulis komposisi HTML, render ke MP4",
            _probe_hyperframes, (("npm", ("npm", "install", "-g", f"{HYPERFRAMES_PACKAGE}@latest")),),
            f"npm install -g {HYPERFRAMES_PACKAGE}", optional=True),
    Program("remotion", "Remotion", "video dari React; paketnya dipasang per proyek oleh video_new",
            _probe_remotion, (), "pasang Node.js dulu; proyek dibuat dengan video_new(engine='remotion')",
            optional=True),
    Program("homebrew", "Homebrew", "pemasang program tanpa sudo untuk ffmpeg, node, deno (Linux dan macOS)",
            _probe_command("brew", "--version"), (), HOMEBREW_INSTALL, optional=True),
)
BY_KEY = {p.key: p for p in PROGRAMS}


def _meets(version: str, minimum: str) -> bool:
    if not minimum:
        return True
    have = [int(x) for x in re.findall(r"\d+", version)]
    need = [int(x) for x in re.findall(r"\d+", minimum)]
    if not have:
        return True     # "per proyek", "latest": nothing to compare
    width = max(len(have), len(need))
    return have + [0] * (width - len(have)) >= need + [0] * (width - len(need))


def status() -> list[dict[str, Any]]:
    """Every program: found or not, its version, and what to do when it is missing or too old."""
    out = []
    for program in PROGRAMS:
        found = program.probe()
        ready = found is not None and _meets(found.version, program.minimum)
        entry: dict[str, Any] = {
            "key": program.key, "label": program.label, "purpose": program.purpose, "optional": program.optional,
            "installed": found is not None, "ready": ready,
            "version": found.version if found else "", "source": found.source if found else "",
        }
        if not ready:
            entry["install"] = install_hint(program.key)
            if found is not None:
                entry["note"] = f"versi {found.version} terlalu lama; perlu {program.minimum}+"
        out.append(entry)
    return out


def _manager_ready(manager: str) -> bool:
    command = {"pip": _pip()[0], "brew": "brew", "npm": "npm", "uv": "uv"}[manager]
    return command == sys.executable or shutil.which(command) is not None


def install_hint(key: str) -> str:
    program = BY_KEY[key]
    for manager, argv in program.installs:
        if _manager_ready(manager):
            return f"python main.py tools install {key}   (menjalankan: {' '.join(argv)})"
    return program.manual


def install(key: str) -> dict[str, Any]:
    """Install one program with the first manager that is present. Never uses sudo."""
    program = BY_KEY.get(key)
    if program is None:
        return {"ok": False, "key": key, "error": f"tidak dikenal; pilih salah satu: {', '.join(BY_KEY)}"}
    for manager, argv in program.installs:
        if not _manager_ready(manager):
            continue
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True, timeout=_INSTALL_TIMEOUT, check=False,
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "key": key, "command": " ".join(argv), "error": str(exc)}
        tail = ((done.stderr or "") + (done.stdout or "")).strip()[-600:]
        found = program.probe()
        return {"ok": done.returncode == 0 and found is not None, "key": key, "command": " ".join(argv),
                "version": found.version if found else "", "output": "" if done.returncode == 0 else tail}
    return {"ok": False, "key": key, "manual": program.manual,
            "error": "tidak ada pemasang otomatis di mesin ini; jalankan perintah manual di atas"}


__all__ = ["BY_KEY", "PROGRAMS", "ffmpeg_exe", "install", "install_hint", "node_major", "status", "ytdlp_command"]
