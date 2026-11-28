"""Install a skill the owner uploaded (a ZIP with a SKILL.md), after it passed the security scan.

Uploaded skills live in the user's data folder (``<data>/skills/<name>/``), outside the checkout, so an update
never touches them and they survive a fresh clone. The loader reads that folder like ``builtin/``. A skill whose
header has no ``scope`` joins as ``contextual`` (woken by its triggers), never as a guide in every prompt.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from brain.config import paths

_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_SCOPE = re.compile(r"^scope:\s*\S+", re.MULTILINE)


class InstallError(ValueError):
    """Why a skill cannot be installed. The message is for the user."""


@dataclass(frozen=True)
class Installed:
    name: str
    path: Path
    replaced: bool


def user_skills_dir() -> Path:
    return paths.user_data_root() / "skills"


def find_skill_root(folder: Path) -> Path | None:
    """The folder holding SKILL.md: the top, or the single folder the archive wraps everything in."""
    if (folder / "SKILL.md").is_file():
        return folder
    children = [p for p in folder.iterdir() if not p.name.startswith((".", "__MACOSX"))]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "SKILL.md").is_file():
        return children[0]
    return None


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    if not slug:
        raise InstallError("SKILL.md tidak punya nama yang bisa dipakai (baris name: di bagian atas).")
    return slug


def install(skill_root: Path) -> Installed:
    """Copy a scanned skill into the user's skills folder. Replaces an earlier upload of the same name."""
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---") or text.find("\n---", 3) == -1:
        raise InstallError("SKILL.md harus diawali header (--- name: ... description: ... ---).")
    found = _NAME.search(text.split("\n---", 1)[0])
    if not found:
        raise InstallError("Header SKILL.md tidak punya baris name:.")
    name = _slug(found.group(1).strip().strip("'\""))

    target = user_skills_dir() / name
    from skills import load_all

    existing = load_all().get(name)
    if existing is not None and not target.exists():
        raise InstallError(f"Sudah ada skill bernama '{name}' (bawaan atau dari GitHub). Ganti nama di SKILL.md.")

    replaced = target.exists()
    staging = target.with_name(f".{name}.incoming")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(skill_root, staging, symlinks=False)
    header, rest = text.split("\n---", 1)
    # The loader names a skill by its header, so the header carries the same name as the folder.
    header = _NAME.sub(f"name: {name}", header, count=1)
    if not _SCOPE.search(header):
        header += "\nscope: contextual"
    (staging / "SKILL.md").write_text(f"{header}\n---{rest}", encoding="utf-8")
    if replaced:
        shutil.rmtree(target)
    staging.rename(target)
    load_all.cache_clear()  # type: ignore[attr-defined]
    return Installed(name, target, replaced)
