"""Install, switch on, and switch off add-ons (``python main.py addon ...``).

``install`` does what can be done safely from here: the add-on's Python packages go into this
interpreter with pip, missing programs are installed through ``brain.utils.toolkit`` when it knows a
way that needs no password, and the add-on is switched on. Steps that need the user (a broker's
MetaTrader installer, sudo, a browser) are printed from the add-on's ``addon.json`` for this OS.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from brain.config import edition

ROOT = Path(__file__).resolve().parents[1]
_PIP_TIMEOUT = 1800


def os_key() -> str:
    system = platform.system().lower()
    return {"darwin": "macos", "windows": "windows"}.get(system, "linux")


def _requirements(spec: dict[str, Any]) -> Path | None:
    rel = spec.get("requirements")
    path = ROOT / rel if rel else None
    if path is None or not path.is_file():
        return None
    lines = [line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return path if any(lines) else None


def missing_programs(spec: dict[str, Any]) -> list[str]:
    return [name for name in spec.get("programs") or [] if shutil.which(name) is None]


def status() -> list[dict[str, Any]]:
    out = []
    for name in edition.names():
        spec = edition.manifest(name)
        out.append({
            "name": name,
            "title": spec.get("title", name),
            "description": spec.get("description", ""),
            "on": edition.addon_enabled(name),
            "switched_on": edition.switched_on(name),
            "files_complete": edition.files_complete(name),
            "missing_programs": missing_programs(spec),
        })
    return out


def _pip_install(requirements: Path) -> tuple[bool, str]:
    argv = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=_PIP_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return done.returncode == 0, (done.stdout + done.stderr)[-2000:]


def _install_program(name: str) -> tuple[bool, str]:
    try:
        from brain.utils import toolkit

        result = toolkit.install(name)
    except Exception as exc:  # noqa: BLE001 - a program we cannot install is reported, not fatal
        return False, str(exc)
    ok = bool(result.get("ok")) or shutil.which(name) is not None
    return ok, str(result.get("manual") or result.get("message") or result.get("error") or "")


def install(name: str, deps: bool = True) -> dict[str, Any]:
    """Install what the add-on needs and switch it on. Returns a report; never raises for a missing piece."""
    spec = edition.manifest(name)
    if not spec:
        return {"ok": False, "error": f"add-on '{name}' tidak ada. Pilihan: {', '.join(edition.names())}"}
    if not edition.files_complete(name):
        return {"ok": False, "error": f"file add-on '{name}' tidak lengkap; cek addons/{name}/addon.json"}
    report: dict[str, Any] = {"ok": True, "name": name, "steps": [], "manual": spec.get("manual", {}).get(os_key(), [])}
    if deps:
        requirements = _requirements(spec)
        if requirements is not None:
            ok, log = _pip_install(requirements)
            report["steps"].append({"step": f"pip install -r {requirements.relative_to(ROOT)}", "ok": ok, "log": log})
            report["ok"] = report["ok"] and ok
        for program in missing_programs(spec):
            ok, note = _install_program(program)
            report["steps"].append({"step": f"pasang {program}", "ok": ok, "log": note})
    report["missing_programs"] = missing_programs(spec)
    if report["ok"]:
        edition.write_state(name, True)
    report["on"] = edition.addon_enabled(name)
    return report


def remove(name: str) -> dict[str, Any]:
    """Switch the add-on off. Its files and data stay where they are."""
    if not edition.manifest(name):
        return {"ok": False, "error": f"add-on '{name}' tidak ada. Pilihan: {', '.join(edition.names())}"}
    edition.write_state(name, False)
    return {"ok": True, "name": name, "on": edition.addon_enabled(name)}


__all__ = ["install", "remove", "status", "missing_programs", "os_key"]
