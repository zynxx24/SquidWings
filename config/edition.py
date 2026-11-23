"""Which optional modules are installed: trading and the video Studio. Both are free, in the repository.

The trading model (``trading/``), its web API and MT4/MT5 bridges, the trading panel, and the
``trading_snapshot`` tool form the trading module. When their files are present, the server,
the CLI and the tool registry switch them on; when some are missing, everything else runs the
same and the trading entry points say the module is not installed instead of failing on an import.

``ENTERPRISE_TRADING=0`` turns the module off even when it is installed (the name is kept for
existing .env files).

The video Studio (editor, its API, the studio tools and skill, the Studio harness) works the same
way; ``ENTERPRISE_STUDIO=0`` turns it off.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Every file the trading features need. A copy with only some of them is treated as absent.
TRADING_FILES = (
    "trading/journal.py",
    "trading/quant/__init__.py",
    "web/quant_api.py",
    "web/mt4_bridge.py",
    "web/mt5_api.py",
    "web/trading_server.py",
    "web/static/trading/index.html",
    "tool/execute/execute_trading/snapshot_tool.py",
)

ENTERPRISE_NOTE = (
    "Modul trading tidak terpasang: file di trading/ tidak lengkap, atau ENTERPRISE_TRADING=0. "
    "Harness, chat, tim agent, toolbox, dan router tetap lengkap."
)


# Every file the video Studio needs. Missing any of them, the Studio is not offered anywhere.
STUDIO_FILES = (
    "utils/studio_doc.py",
    "utils/studio_context.py",
    "web/studio_api.py",
    "web/static/studio/index.html",
    "web/static/studio/studio.js",
    "tool/execute/execute_toolbox/studio_tools.py",
    "brain/skills/studio/SKILL.md",
)

STUDIO_NOTE = (
    "Studio video (editor motion dengan keyframe, catatan, dan chat revisi) tidak terpasang: filenya tidak lengkap, "
    "atau ENTERPRISE_STUDIO=0. Video tetap bisa dibuat dengan skill animate dan alat media."
)


@lru_cache(maxsize=1)
def studio_enabled() -> bool:
    """True when the Enterprise video Studio is installed and not switched off."""
    if os.environ.get("ENTERPRISE_STUDIO", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return all((ROOT / name).is_file() for name in STUDIO_FILES)


def studio_module(name: str) -> Any:
    """An Enterprise Studio module (``utils.studio_context``...), or None in the community edition."""
    if not studio_enabled():
        return None
    import importlib

    return importlib.import_module(name)


@lru_cache(maxsize=1)
def trading_enabled() -> bool:
    """True when the Enterprise trading module is installed and not switched off."""
    if os.environ.get("ENTERPRISE_TRADING", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return all((ROOT / name).is_file() for name in TRADING_FILES)


def edition() -> str:
    return "enterprise" if trading_enabled() else "community"


__all__ = ["trading_enabled", "edition", "ENTERPRISE_NOTE", "TRADING_FILES", "studio_enabled", "studio_module",
           "STUDIO_NOTE", "STUDIO_FILES"]
