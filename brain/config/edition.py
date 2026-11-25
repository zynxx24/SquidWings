"""Which add-ons are switched on: trading and the video Studio. Both are free and in the repository.

Each add-on lives in ``addons/<name>/`` with an ``addon.json`` that names its files, the Python
packages and programs it needs, and the manual steps per OS. Add-ons are **off by default**:
``python main.py addon install <name>`` installs what it needs and switches it on, and
``python main.py addon remove <name>`` switches it off again (files and data stay).

The switch is kept in ``addons/enabled.json`` (private, not in git). ``ADDON_<NAME>=1`` or ``=0``
in the environment wins over that file, which is handy for tests and servers. The old
``ENTERPRISE_TRADING=0`` / ``ENTERPRISE_STUDIO=0`` still turn an add-on off.

An add-on whose files are incomplete counts as absent, switched on or not: the server, the CLI
and the tool registry then say it is not installed instead of failing on an import.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ADDONS_DIR = ROOT / "addons"
STATE_FILE = ADDONS_DIR / "enabled.json"

_ON = {"1", "true", "yes", "on"}
_OFF = {"0", "false", "no", "off"}
_LEGACY_ENV = {"trading": "ENTERPRISE_TRADING", "studio": "ENTERPRISE_STUDIO"}


def manifest(name: str) -> dict[str, Any]:
    """The add-on's addon.json, or {} when there is no such add-on."""
    path = ADDONS_DIR / name / "addon.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def names() -> list[str]:
    """Every add-on in the repository, whether it is on or not."""
    return sorted(p.parent.name for p in ADDONS_DIR.glob("*/addon.json"))


def files_complete(name: str) -> bool:
    files = manifest(name).get("files") or []
    return bool(files) and all((ROOT / f).is_file() for f in files)


def read_state() -> dict[str, bool]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}


def write_state(name: str, on: bool) -> None:
    state = read_state()
    state[name] = on
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    clear_cache()


def switched_on(name: str) -> bool:
    """The switch alone, without looking at the files: env first, then addons/enabled.json, default off."""
    value = os.environ.get(f"ADDON_{name.upper()}", "").strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    legacy = os.environ.get(_LEGACY_ENV.get(name, ""), "").strip().lower() if name in _LEGACY_ENV else ""
    if legacy in _OFF:
        return False
    return read_state().get(name, False)


@lru_cache(maxsize=16)
def addon_enabled(name: str) -> bool:
    """True when the add-on is switched on and its files are complete."""
    return switched_on(name) and files_complete(name)


def _files(name: str) -> tuple[str, ...]:
    return tuple(manifest(name).get("files") or ())


TRADING_FILES = _files("trading")
STUDIO_FILES = _files("studio")

ENTERPRISE_NOTE = (
    "Add-on trading belum aktif. Pasang dan nyalakan dengan: python main.py addon install trading "
    "(lihat addons/trading/README.md). Harness, chat, tim agent, toolbox, dan router tetap lengkap."
)
STUDIO_NOTE = (
    "Add-on Studio video belum aktif. Pasang dan nyalakan dengan: python main.py addon install studio "
    "(lihat addons/studio/README.md). Video tetap bisa dibuat dengan skill animate dan alat media."
)


def trading_enabled() -> bool:
    return addon_enabled("trading")


def studio_enabled() -> bool:
    return addon_enabled("studio")


def clear_cache() -> None:
    addon_enabled.cache_clear()


# Kept for callers that clear these caches directly.
trading_enabled.cache_clear = clear_cache  # type: ignore[attr-defined]
studio_enabled.cache_clear = clear_cache  # type: ignore[attr-defined]


def studio_module(name: str) -> Any:
    """A Studio module (``addons.studio.studio_context``...), or None while the add-on is off."""
    if not studio_enabled():
        return None
    import importlib

    return importlib.import_module(name)


def edition() -> str:
    return "enterprise" if trading_enabled() else "community"


__all__ = ["addon_enabled", "trading_enabled", "edition", "ENTERPRISE_NOTE", "TRADING_FILES", "studio_enabled",
           "studio_module", "STUDIO_NOTE", "STUDIO_FILES", "names", "manifest", "switched_on", "write_state",
           "files_complete", "clear_cache"]
