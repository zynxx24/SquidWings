"""Keeps the local tools current: graphify, aislop, yt-dlp and (when installed globally) HyperFrames.

Once a day, in a background thread, this compares the installed version of each program with
the newest release and upgrades it when there is a newer one. It never blocks a request.

* graphify is a Python tool: upgraded with ``uv tool upgrade`` (package ``graphifyy``).
* aislop is an npm package: upgraded with ``npm install -g aislop@latest``.
* yt-dlp lives in this app's own Python and is upgraded there: sites like YouTube change often,
  and a yt-dlp a few months old stops downloading from them.
* HyperFrames, only when installed with ``npm install -g hyperframes`` (``npx`` always fetches the newest).

Set ``TOOLS_AUTO_UPDATE=false`` to turn it off. Tests turn it off too, because they must not
touch the network or the global installs. The last check and its result go to
``.agent_memory/tool_updates.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

CHECK_EVERY_S = 24 * 3600
_NETWORK_TIMEOUT = 10.0
_UPGRADE_TIMEOUT = 600

GRAPHIFY_PACKAGE = "graphifyy"
AISLOP_PACKAGE = "aislop"
YTDLP_PACKAGE = "yt-dlp"
HYPERFRAMES_PACKAGE = "hyperframes"

_started = threading.Lock()
_started_once = False


def enabled() -> bool:
    return os.getenv("TOOLS_AUTO_UPDATE", "true").strip().lower() != "false"


def _numbers(version: str | None) -> tuple[int, ...] | None:
    """'0.19.1' -> (0, 19, 1). None when there is no version to compare."""
    if not version:
        return None
    found = re.findall(r"\d+", version)
    return tuple(int(x) for x in found) if found else None


def is_newer(latest: str | None, installed: str | None) -> bool:
    """True only when both versions are known and the latest is greater."""
    a, b = _numbers(latest), _numbers(installed)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def installed_version(command: str) -> str | None:
    """The version a program reports, e.g. 'graphify 0.9.91' or '0.19.1'. None if it is not installed."""
    if shutil.which(command) is None:
        return None
    try:
        done = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+(?:\.\d+)+", (done.stdout or "") + (done.stderr or ""))
    return match.group(0) if match else None


def latest_pypi(package: str) -> str | None:
    try:
        resp = httpx.get(f"https://pypi.org/pypi/{package}/json", timeout=_NETWORK_TIMEOUT)
        resp.raise_for_status()
        return str(resp.json()["info"]["version"])
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def latest_npm(package: str) -> str | None:
    try:
        resp = httpx.get(f"https://registry.npmjs.org/{package}/latest", timeout=_NETWORK_TIMEOUT)
        resp.raise_for_status()
        return str(resp.json()["version"])
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _run_upgrade(argv: list[str]) -> tuple[bool, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=_UPGRADE_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    tail = ((done.stderr or "") + (done.stdout or "")).strip()[-300:]
    return done.returncode == 0, tail


def _ytdlp_version() -> str | None:
    """yt-dlp in this app's Python; one on PATH is the system's and is left alone."""
    import importlib.metadata

    try:
        return importlib.metadata.version(YTDLP_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None


def _update_one(name: str, command: str | Callable[[], str | None], latest: str | None,
                upgrade: list[str]) -> dict[str, Any]:
    installed = command() if callable(command) else installed_version(command)
    result: dict[str, Any] = {"tool": name, "installed": installed, "latest": latest, "action": "none"}
    if installed is None:
        result["action"] = "not installed"
        return result
    if not is_newer(latest, installed):
        return result
    if shutil.which(upgrade[0]) is None:
        result["action"] = f"newer version available, but {upgrade[0]} is not installed"
        return result
    ok, tail = _run_upgrade(upgrade)
    after = command() if callable(command) else installed_version(command)
    result.update(action="upgraded" if ok else "upgrade failed", installed_after=after, output=tail if not ok else "")
    return result


def _pip_upgrade(requirement: str) -> list[str]:
    import importlib.util
    import sys

    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", "-q", "-U", requirement]
    return ["uv", "pip", "install", "-q", "--python", sys.executable, "-U", requirement]


def run_check(state_path: Path, *, force: bool = False, now: float | None = None) -> dict[str, Any]:
    """One check-and-upgrade pass. Skipped when the last one was less than a day ago unless forced."""
    now = time.time() if now is None else now
    state: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        state = json.loads(state_path.read_text(encoding="utf-8"))
    if not force and now - float(state.get("checked_at", 0)) < CHECK_EVERY_S:
        return {"skipped": True, "reason": "checked recently", **state}

    results = [
        _update_one("graphify", "graphify", latest_pypi(GRAPHIFY_PACKAGE),
                    ["uv", "tool", "upgrade", GRAPHIFY_PACKAGE]),
        _update_one("aislop", "aislop", latest_npm(AISLOP_PACKAGE),
                    ["npm", "install", "-g", f"{AISLOP_PACKAGE}@latest"]),
        _update_one("yt-dlp", _ytdlp_version, latest_pypi(YTDLP_PACKAGE), _pip_upgrade("yt-dlp[default]")),
        _update_one("hyperframes", "hyperframes", latest_npm(HYPERFRAMES_PACKAGE),
                    ["npm", "install", "-g", f"{HYPERFRAMES_PACKAGE}@latest"]),
    ]
    out = {"checked_at": now, "results": results}
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    except OSError as exc:
        logger.debug(f"[tools] Could not save update state: {exc}")
    for item in results:
        if item["action"] in {"upgraded", "upgrade failed"}:
            logger.info(f"[tools] {item['tool']}: {item['action']} ({item.get('installed')} -> {item.get('installed_after', item.get('latest'))})")
    return out


def start_background(state_path: Path) -> bool:
    """Start the daily check in a daemon thread. Returns False when disabled or already started."""
    global _started_once
    if not enabled():
        return False
    with _started:
        if _started_once:
            return False
        _started_once = True

    def _worker() -> None:
        try:
            run_check(state_path)
        except Exception as exc:  # noqa: BLE001 - a failed update must never stop the server
            logger.warning(f"[tools] auto-update check failed: {exc}")

    threading.Thread(target=_worker, name="tool-auto-update", daemon=True).start()
    return True


__all__ = ["enabled", "is_newer", "run_check", "start_background", "installed_version"]
