"""Check uploaded files and archives before anything is extracted, installed, or read by the agent.

An upload (a skill as a ZIP, a project, a document) arrives from the owner, but its content may come from anywhere.
Two passes:

* ``check_zip``: the archive itself, before a single file is written. Paths that escape the target (zip-slip),
  absolute paths, symlinks, too many files, too many bytes once unpacked, or a compression ratio no real file has
  (a zip bomb) stop it.
* ``scan_tree`` / ``scan_file``: every file, once unpacked. HIGH findings stop an install: a native executable,
  a command the shell guard refuses (``command_risk``) in a script or a fenced code block, prompt injection in text
  the agent will read (``PromptShield``), invisible or direction-flipping characters (Trojan Source). MEDIUM
  findings are reported only: what looks like an API key, a private key file, an ``.env`` file, code that runs
  decoded or fetched text.

Never raises on file content; a file that cannot be read is skipped and said so.
"""

from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_FILES = 2_000
MAX_UNPACKED = 200 * 1024 * 1024
MAX_RATIO = 120
MAX_TEXT_SCAN = 2 * 1024 * 1024
TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".htm", ".css", ".php", ".rb", ".pl", ".ps1", ".bat", ".cmd",
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".sql", ".xml", ".env", ".csv",
})
SHELL_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".command"})
_EXEC_MAGIC = (b"\x7fELF", b"MZ", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
_HIDDEN_CHARS = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]")
_FENCE = re.compile(r"```(?:bash|sh|shell|zsh|console|terminal)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_RUNS_DECODED = re.compile(
    r"(eval|exec)\s*\(\s*(base64|atob|codecs\.decode|bytes\.fromhex|zlib\.decompress|__import__\(['\"]base64)"
    r"|subprocess\.[a-z_]+\([^)]*shell\s*=\s*True[^)]*(curl|wget)"
    r"|Function\(\s*atob\(", re.IGNORECASE)
_SECRET_FILES = frozenset({"id_rsa", "id_ed25519", "id_ecdsa", ".npmrc", ".pypirc", ".netrc", "credentials.json"})


@dataclass(frozen=True)
class Finding:
    severity: str          # "HIGH" stops an install; "MEDIUM" is reported
    file: str
    reason: str
    line: int = 0

    def text(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"[{self.severity}] {where}: {self.reason}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    files: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.severity == "HIGH" for f in self.findings)

    def summary(self, limit: int = 12) -> str:
        if not self.findings:
            return f"Aman: {self.files} file diperiksa, tidak ada temuan."
        high = sum(f.severity == "HIGH" for f in self.findings)
        head = f"{self.files} file diperiksa: {high} temuan berbahaya, {len(self.findings) - high} catatan."
        lines = [f.text() for f in sorted(self.findings, key=lambda f: f.severity != "HIGH")[:limit]]
        more = len(self.findings) - limit
        return "\n".join([head, *lines, *([f"... dan {more} temuan lain"] if more > 0 else [])])


def check_zip(data: bytes) -> tuple[zipfile.ZipFile | None, Report]:
    """The opened archive when it is safe to unpack, with what was wrong otherwise."""
    report = Report()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        report.findings.append(Finding("HIGH", "(arsip)", "bukan file ZIP yang valid"))
        return None, report
    entries = archive.infolist()
    report.files = len(entries)
    if len(entries) > MAX_FILES:
        report.findings.append(Finding("HIGH", "(arsip)", f"terlalu banyak file ({len(entries)} > {MAX_FILES})"))
    total = sum(e.file_size for e in entries)
    if total > MAX_UNPACKED:
        report.findings.append(Finding("HIGH", "(arsip)", f"isi terlalu besar setelah diekstrak ({total // 2**20} MB)"))
    packed = max(1, sum(e.compress_size for e in entries))
    if total > 10 * 2**20 and total / packed > MAX_RATIO:
        report.findings.append(Finding("HIGH", "(arsip)", f"rasio kompresi {total // packed}x: pola zip bomb"))
    for entry in entries:
        name = entry.filename
        path = PurePosixPath(name.replace("\\", "/"))
        if "\x00" in name or path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", name):
            report.findings.append(Finding("HIGH", name, "path keluar dari folder tujuan (zip-slip)"))
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode):
            report.findings.append(Finding("HIGH", name, "symlink di dalam arsip"))
    return (None if report.blocked else archive), report


def extract(archive: zipfile.ZipFile, target: Path) -> list[Path]:
    """Unpack a checked archive into ``target`` (which must not exist yet). Returns the files written."""
    target.mkdir(parents=True)
    root = target.resolve()
    written = []
    for entry in archive.infolist():
        destination = (root / entry.filename.replace("\\", "/")).resolve()
        if not destination.is_relative_to(root):
            raise ValueError(f"path escapes the target: {entry.filename}")
        if entry.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(entry) as source, destination.open("wb") as out:
            remaining = MAX_UNPACKED
            while chunk := source.read(1 << 20):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("archive grew past its declared size")
                out.write(chunk)
        written.append(destination)
    return written


def scan_file(path: Path, shown_as: str, report: Report) -> None:
    from brain.security.command_risk import assess
    from brain.security.prompt_shield import PromptShield
    from brain.security.redactor import Redactor

    report.files += 1
    name = path.name.lower()
    try:
        head = path.open("rb").read(8)
    except OSError:
        report.skipped.append(shown_as)
        return
    if head.startswith(_EXEC_MAGIC):
        report.findings.append(Finding("HIGH", shown_as, "program biner yang bisa dijalankan"))
        return
    if name in _SECRET_FILES or name.endswith((".pem", ".key", ".p12", ".pfx")):
        report.findings.append(Finding("MEDIUM", shown_as, "file kunci atau kredensial"))
    if name == ".env" or name.startswith(".env."):
        report.findings.append(Finding("MEDIUM", shown_as, "file .env (bisa berisi rahasia)"))
    if path.suffix.lower() not in TEXT_SUFFIXES and name not in {".env", "dockerfile", "makefile"}:
        return
    try:
        text = path.read_bytes()[:MAX_TEXT_SCAN].decode("utf-8", errors="replace")
    except OSError:
        report.skipped.append(shown_as)
        return

    for number, line in enumerate(text.splitlines(), 1):
        if _HIDDEN_CHARS.search(line):
            report.findings.append(Finding("HIGH", shown_as, "karakter tak terlihat atau pembalik arah teks", number))
            break
    if Redactor().find(text):
        report.findings.append(Finding("MEDIUM", shown_as, "berisi teks yang mirip API key atau token"))
    if _RUNS_DECODED.search(text):
        report.findings.append(Finding("MEDIUM", shown_as, "menjalankan teks yang didekode atau diunduh"))

    commands: list[tuple[int, str]] = []
    if path.suffix.lower() in SHELL_SUFFIXES or text.startswith("#!/bin/") or text.startswith("#!/usr/bin/env bash"):
        commands += [(n, ln) for n, ln in enumerate(text.splitlines(), 1) if ln.strip() and not ln.lstrip().startswith("#")]
    if path.suffix.lower() in {".md", ".markdown"}:
        for match in _FENCE.finditer(text):
            first = text[: match.start()].count("\n") + 2
            commands += [(first + i, ln.lstrip("$ ").strip()) for i, ln in enumerate(match.group(1).splitlines()) if ln.strip()]
    for number, command in commands:
        risk = assess(command, cwd="/tmp/skill-check", workspace="/tmp/skill-check")
        if risk.blocked:
            report.findings.append(Finding("HIGH", shown_as, f"perintah berbahaya: {risk.reason}", number))

    if path.suffix.lower() in {".md", ".markdown", ".txt", ".html", ".htm"}:
        verdict = PromptShield().inspect(text)
        if verdict.should_block:
            report.findings.append(Finding("HIGH", shown_as, "berisi upaya prompt injection: " + ", ".join(verdict.matches[:3])))


def scan_tree(root: Path, report: Report | None = None) -> Report:
    """Scan every file under ``root``."""
    report = report or Report()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            report.findings.append(Finding("HIGH", path.relative_to(root).as_posix(), "symlink"))
        elif path.is_file():
            scan_file(path, path.relative_to(root).as_posix(), report)
    return report
