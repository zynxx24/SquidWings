"""Which edition is installed: the open harness alone, or with the Enterprise trading module.

The trading model (``trading/``), its web API and MT4/MT5 bridges, the trading panel, and the
``trading_snapshot`` tool are the Enterprise module. They are not in the public repository.
When their files are present, the server, the CLI and the tool registry switch them on; when
they are missing, everything else runs the same and the trading entry points say they belong
to the Enterprise edition instead of failing on an import.

``ENTERPRISE_TRADING=0`` turns the module off even when it is installed.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

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
    "Panel dan model trading termasuk SquidWings Enterprise, jadi tidak ada di edisi ini. "
    "Harness, chat, tim agent, toolbox, dan router tetap lengkap."
)


@lru_cache(maxsize=1)
def trading_enabled() -> bool:
    """True when the Enterprise trading module is installed and not switched off."""
    if os.environ.get("ENTERPRISE_TRADING", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return all((ROOT / name).is_file() for name in TRADING_FILES)


def edition() -> str:
    return "enterprise" if trading_enabled() else "community"


__all__ = ["trading_enabled", "edition", "ENTERPRISE_NOTE", "TRADING_FILES"]
