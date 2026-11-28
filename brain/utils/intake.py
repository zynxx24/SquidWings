"""One way in for uploaded files, the same from the web chat, the terminal (``/upload``) and Telegram.

``accept(folder, name, data)``:

* keeps the original under ``<folder>/uploads/`` (never overwriting: ``name (2).ext``);
* a ZIP is checked before anything is unpacked (zip-slip, symlinks, zip bombs), unpacked into a private staging
  folder, and every file in it is scanned (``brain/security/file_scan.py``);
* a ZIP that holds a skill (a ``SKILL.md`` at the top) is installed when the scan has no HIGH finding, and is
  usable from the next message on; any other ZIP is unpacked into ``uploads/<name>/`` under the same condition;
* a single file is scanned too, and kept either way (it is the owner's file); a HIGH finding is said loudly.

The returned note is what the agent and the user read about the upload, in Indonesian.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from brain.config import paths
from brain.security import file_scan


@dataclass
class Intake:
    saved: str                     # the original, relative to the folder
    note: str
    extracted: str = ""            # the unpacked folder, relative to the folder
    skill: str = ""                # the skill installed
    blocked: bool = False
    findings: list[str] = field(default_factory=list)


def _safe_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name.strip() or "file"
    cleaned = re.sub(r"[^\w.\- ()]+", "_", base)[:120].strip(". ") or "file"
    return cleaned


def _free(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (candidate := path.with_name(f"{path.stem} ({n}){path.suffix}")).exists():
        n += 1
    return candidate


def accept(folder: Path, name: str, data: bytes) -> Intake:
    uploads = folder / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    original = _free(uploads / _safe_name(name))
    original.write_bytes(data)
    saved = original.relative_to(folder).as_posix()

    if original.suffix.lower() != ".zip":
        report = file_scan.Report()
        file_scan.scan_file(original, saved, report)
        lines = [f"File disimpan: {saved} ({len(data):,} byte)."]
        if report.findings:
            lines.append(("PERINGATAN, jangan dijalankan sebelum dicek: " if report.blocked else "Catatan pemindaian: ")
                         + "; ".join(f.text() for f in report.findings[:6]))
        return Intake(saved, " ".join(lines), blocked=report.blocked, findings=[f.text() for f in report.findings])

    archive, report = file_scan.check_zip(data)
    if archive is None:
        return Intake(saved, f"ZIP {saved} ditolak dan tidak diekstrak. {report.summary()}", blocked=True,
                      findings=[f.text() for f in report.findings])

    staging_root = paths.data_dir() / "intake"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="zip-", dir=staging_root))
    try:
        unpacked = staging / "files"
        file_scan.extract(archive, unpacked)
        scanned = file_scan.scan_tree(unpacked)
        findings = [f.text() for f in scanned.findings]

        from skills import installer

        skill_root = installer.find_skill_root(unpacked)
        if skill_root is not None:
            if scanned.blocked:
                return Intake(saved, "Skill di ZIP ini TIDAK dipasang karena ada temuan berbahaya.\n" + scanned.summary(),
                              blocked=True, findings=findings)
            try:
                done = installer.install(skill_root)
            except installer.InstallError as exc:
                return Intake(saved, f"Skill tidak dipasang: {exc}", blocked=True, findings=findings)
            verb = "diperbarui" if done.replaced else "terpasang"
            return Intake(saved, f"Skill '{done.name}' {verb} dan bisa dipakai mulai pesan berikutnya "
                                 f"(load_skill(name=\"{done.name}\")). {scanned.summary()}",
                          skill=done.name, findings=findings)

        if scanned.blocked:
            return Intake(saved, f"ZIP {saved} disimpan tapi TIDAK diekstrak karena ada temuan berbahaya.\n"
                                 + scanned.summary(), blocked=True, findings=findings)
        target = _free(uploads / original.stem)
        shutil.move(str(unpacked), str(target))
        relative = target.relative_to(folder).as_posix()
        return Intake(saved, f"ZIP diekstrak ke {relative}/ ({scanned.files} file). {scanned.summary()}",
                      extracted=relative, findings=findings)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
