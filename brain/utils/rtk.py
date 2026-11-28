"""rtk (Rust Token Killer, github.com/rtk-ai/rtk): shorter command output for every AI on this machine.

rtk compresses what a command prints before a model reads it (tests show only the failures, ``git status`` a
few lines, ``ls`` a tree with counts), up to 90 % less, and never hides a failure: an exit code passes through
and a cut says where the full output is (``rtk bypass <command>`` runs it unfiltered).

* ``install()`` fetches the Apache-2.0 edition (``rtk-oss``, no BUSL engine) from the official GitHub release
  into ``~/.local/bin``, and refuses it unless its SHA-256 matches the release's ``checksums.txt``.
* ``connect()`` plugs it into the coding agents found here with rtk's own ``init`` (Claude Code and Codex get
  its hook; Antigravity its rules file).
* ``rewrite(command)`` is what SquidWings itself uses: ``execute_command`` runs ``rtk git status`` where the
  model asked for ``git status``, after the safety checks judged the original. ``RTK=off`` turns that off.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import shlex
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from brain.utils.logger import get_logger

logger = get_logger(__name__)

REPO = "rtk-ai/rtk"
EDITION = "rtk-oss"                    # Apache-2.0 only: the hook core and every command filter
REWRITE_SECONDS = 3.0
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
_VERSION = re.compile(r"/releases/tag/(v?\d+\.\d+\.\d+[\w.-]*)")
_COMMAND_RTK = re.compile(r"(^|[;&|(]\s*)rtk(?=\s|$)")


class RtkError(RuntimeError):
    """What went wrong, in words the person can act on."""


def install_dir() -> Path:
    return Path(os.getenv("RTK_INSTALL_DIR", "~/.local/bin")).expanduser()


def binary() -> str | None:
    """The rtk binary: on PATH, or where ``install()`` puts it."""
    found = shutil.which("rtk")
    if found:
        return found
    local = install_dir() / ("rtk.exe" if os.name == "nt" else "rtk")
    return str(local) if local.is_file() and os.access(local, os.X_OK) else None


def enabled() -> bool:
    return os.getenv("RTK", "auto").strip().lower() not in {"0", "off", "false", "no"}


def version() -> str:
    exe = binary()
    if not exe:
        return ""
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


# ── SquidWings: the command an agent asked for, through rtk ───────────────────────
def rewrite(command: str) -> str:
    """``command`` as rtk would run it (``git status`` -> ``rtk git status``), or unchanged when rtk has no
    filter for it, is not installed, is switched off, or does not answer in time."""
    exe = binary() if enabled() else None
    if not exe or not command.strip():
        return command
    # RTK_REWRITE_HOST relaxes rtk's own approval gate: an inherited value must not reach it (as in its hooks).
    env = {k: v for k, v in os.environ.items() if k != "RTK_REWRITE_HOST"}
    try:
        done = subprocess.run([exe, "rewrite", command], capture_output=True, text=True, timeout=REWRITE_SECONDS,
                              check=False, env=env)
    except (OSError, subprocess.SubprocessError):
        return command
    rewritten = done.stdout.strip()
    # 0: rewritten and allowed; 3: rewritten, the agent's own approval applies (ours already ran). Else unchanged.
    if done.returncode not in (0, 3) or not rewritten:
        return command
    # rtk writes a bare "rtk": point at this binary, so the command runs where ~/.local/bin is not on PATH.
    word = command_word(exe)
    return _COMMAND_RTK.sub(lambda m: m.group(1) + word, rewritten)


def command_word(exe: str) -> str:
    """The binary as the first word of a command in the shell that runs it (brain/security/sandbox.shell_argv):
    sh on Linux and macOS; on Windows Git Bash when installed, else PowerShell, which runs a quoted path only
    through its call operator."""
    if os.name != "nt":
        return shlex.quote(exe)
    if shutil.which("bash"):
        return shlex.quote(exe.replace("\\", "/"))
    return "& '" + exe.replace("'", "''") + "'"


# ── install ──────────────────────────────────────────────────────────────────────
def target() -> tuple[str, str]:
    """(release target, archive extension) for this machine."""
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = platform.system()
    if system == "Linux" and arch in {"x86_64", "aarch64"}:
        return f"{arch}-unknown-linux-musl", "tar.gz"
    if system == "Darwin" and arch in {"x86_64", "aarch64"}:
        return f"{arch}-apple-darwin", "tar.gz"
    if system == "Windows" and arch == "x86_64":
        return "x86_64-pc-windows-msvc", "zip"
    raise RtkError(f"rtk has no ready-made build for {system} {machine}. Build it with: cargo install --git https://github.com/{REPO}")


def latest_version() -> str:
    import httpx

    try:
        answer = httpx.head(f"https://github.com/{REPO}/releases/latest", follow_redirects=False, timeout=20)
    except httpx.HTTPError as exc:
        raise RtkError(f"GitHub tidak bisa dihubungi ({type(exc).__name__})") from None
    found = _VERSION.search(answer.headers.get("location", ""))
    if not found:
        raise RtkError("Versi terbaru rtk tidak terbaca dari GitHub.")
    return found.group(1)


def _get(url: str) -> bytes:
    import httpx

    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as answer:
            if answer.status_code != 200:
                raise RtkError(f"GitHub menolak {url.rsplit('/', 1)[-1]} ({answer.status_code})")
            data = bytearray()
            for chunk in answer.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_ARCHIVE_BYTES:
                    raise RtkError("Arsip rtk lebih besar dari yang wajar; dibatalkan.")
            return bytes(data)
    except httpx.HTTPError as exc:
        raise RtkError(f"Unduhan gagal ({type(exc).__name__})") from None


def _member(archive: bytes, extension: str, name: str) -> bytes:
    """The one file named ``name`` from the archive, wherever it sits in it; nothing else is written."""
    if extension == "zip":
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            for info in zipped.infolist():
                if Path(info.filename).name == name and not info.is_dir():
                    return zipped.read(info)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tarred:
            for member in tarred.getmembers():
                if Path(member.name).name == name and member.isfile():
                    handle = tarred.extractfile(member)
                    if handle is not None:
                        return handle.read()
    raise RtkError(f"{name} tidak ada di arsip rilis.")


def install(release: str = "") -> dict[str, Any]:
    """Install the Apache-2.0 rtk from its GitHub release, checked against the release's SHA-256 list."""
    tag = release or latest_version()
    triple, extension = target()
    asset = f"{EDITION}-{triple}.{extension}"
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    sums = _get(f"{base}/checksums.txt").decode("utf-8", errors="replace")
    expected = next((line.split()[0].lower() for line in sums.splitlines()
                     if len(line.split()) >= 2 and line.split()[-1].lstrip("*") == asset), "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RtkError(f"{asset} tidak ada di checksums.txt {tag}; tidak dipasang.")
    archive = _get(f"{base}/{asset}")
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise RtkError(f"Checksum {asset} tidak cocok (harusnya {expected[:12]}..., dapat {actual[:12]}...); tidak dipasang.")
    name = "rtk.exe" if extension == "zip" else "rtk"
    data = _member(archive, extension, name)
    folder = install_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{name}.new"
    temporary.write_bytes(data)
    temporary.chmod(0o755)
    os.replace(temporary, folder / name)
    return {"ok": True, "version": tag, "edition": EDITION, "path": str(folder / name), "sha256": actual}


# ── the coding agents on this machine ───────────────────────────────────────────
AGENTS: tuple[tuple[str, list[str], str], ...] = (
    # (name, rtk init arguments, what tells us it is installed)
    ("Claude Code", ["init", "-g", "--auto-patch"], "claude"),
    ("Codex", ["init", "-g", "--codex"], "codex"),                     # Codex takes no --auto-patch
    ("Antigravity", ["init", "-g", "--agent", "antigravity", "--auto-patch"], "~/.gemini/antigravity"),
)


def _present(marker: str) -> bool:
    return Path(marker).expanduser().exists() if marker.startswith("~") else shutil.which(marker) is not None


def connect(dry_run: bool = False) -> list[dict[str, Any]]:
    """Run rtk's own ``init`` for every agent found here. Returns what happened per agent."""
    exe = binary()
    if not exe:
        raise RtkError("rtk belum terpasang: python main.py rtk install")
    out = []
    for name, args, marker in AGENTS:
        if not _present(marker):
            out.append({"agent": name, "action": "skipped", "detail": "not installed here"})
            continue
        argv = [exe, *args, *(["--dry-run"] if dry_run else [])]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            out.append({"agent": name, "action": "failed", "detail": str(exc)[:300]})
            continue
        text = (done.stdout + done.stderr).strip()
        out.append({"agent": name, "action": "connected" if done.returncode == 0 else "failed",
                    "detail": text[-600:], "command": " ".join(["rtk", *args])})
    return out


def gain() -> str:
    exe = binary()
    if not exe:
        return ""
    try:
        done = subprocess.run([exe, "gain"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()
