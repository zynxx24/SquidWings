"""Is this machine online, and how often should the self-updates run?

The repository (``brain/utils/repo_update.py``), the GitHub skills (``skills/sources.py``) and the local tools
(``tools/code_intel/tool_updates.py``) update themselves while the terminal chat or the web server runs. They only
try when the internet answers: offline, a git fetch or a download would just wait for its timeout and slow the start.
An offline check is retried soon (``RETRY_OFFLINE_S``), so the update happens as soon as the network is back.

``UPDATE_EVERY_HOURS`` sets the rhythm of all three (default 12). Each also has its own setting, which wins:
``AUTO_UPDATE_EVERY_MIN`` (repository), ``SKILL_SOURCES_UPDATE_HOURS`` (skills), ``TOOLS_UPDATE_HOURS`` (tools).
"""

from __future__ import annotations

import os
import socket
import time

DEFAULT_HOURS = 12.0
RETRY_OFFLINE_S = 600
_PROBES = (("github.com", 443), ("1.1.1.1", 443))
_TIMEOUT = 3.0
_REMEMBER_S = 60.0
_last: dict[str, float] = {"at": 0.0, "up": 0.0}


def is_online() -> bool:
    """True when GitHub (or, failing DNS, a public resolver) answers a TCP connect. Remembered for a minute."""
    now = time.monotonic()
    if now - _last["at"] < _REMEMBER_S and _last["at"]:
        return bool(_last["up"])
    up = False
    for host, port in _PROBES:
        try:
            with socket.create_connection((host, port), timeout=_TIMEOUT):
                up = True
                break
        except OSError:
            continue
    _last.update(at=now, up=1.0 if up else 0.0)
    return up


def every_hours(own_setting: str = "", default: float = DEFAULT_HOURS) -> float:
    """Hours between updates: the component's own setting, else UPDATE_EVERY_HOURS, else 12. 0 turns it off."""
    for name in (own_setting, "UPDATE_EVERY_HOURS"):
        value = os.getenv(name, "").strip() if name else ""
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                continue
    return default
