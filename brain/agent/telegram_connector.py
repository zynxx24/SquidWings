"""Telegram connected to the harness from the moment the app starts, also from the terminal.

The web server already runs the flows (Telegram included). The terminal chat did not, so Telegram only answered
while the web page's server was up. ``start_in_background()`` runs the same flow service in a thread of its own
(the terminal's ``input()`` blocks its loop): the owner's Telegram flow is made from the "Harness lewat Telegram"
template when there is none, and its poller answers with the full harness (tools, skills, files, uploads).

Only one process polls the bot (a lock in the data folder, see ``TeamOrchestrator._telegram_loop``), so the
terminal and the web server can both run. ``TELEGRAM_CONNECTOR=off`` keeps it off.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from brain.config import credentials
from brain.utils.logger import get_logger

logger = get_logger(__name__)

_started = threading.Lock()
_thread: threading.Thread | None = None
TEAMS_DIR = Path(__file__).resolve().parents[2] / "web" / "data" / "teams"


def wanted() -> str:
    """"" when the connector should run; otherwise why not (for one line in the terminal)."""
    if os.getenv("TELEGRAM_CONNECTOR", "on").strip().lower() in {"0", "off", "false", "no"}:
        return "dimatikan (TELEGRAM_CONNECTOR=off)"
    if not credentials.get("telegram_bot_token"):
        return "bot token belum diisi (Kredensial > Telegram)"
    if not credentials.get("telegram_chat_id"):
        return "chat ID pemilik belum diisi (Kredensial > Telegram)"
    return ""


async def _serve() -> None:
    from brain.agent.orchestrator import Orchestrator
    from brain.agent.team_orchestrator import TeamOrchestrator, TeamStore
    from brain.config.settings import get_settings

    service = TeamOrchestrator(Orchestrator.from_settings(get_settings()), TeamStore(TEAMS_DIR))
    service.start_scheduler()
    while True:
        await asyncio.sleep(3600)


def _run() -> None:
    try:
        asyncio.run(_serve())
    except Exception:
        logger.exception("[telegram] connector stopped")


def start_in_background() -> str:
    """Start the connector once per process. Returns a line for the terminal."""
    global _thread
    why = wanted()
    if why:
        return f"Telegram: {why}"
    with _started:
        if _thread is None:
            _thread = threading.Thread(target=_run, name="telegram-connector", daemon=True)
            _thread.start()
    return "Telegram: tersambung ke harness (kirim pesan atau file ke bot)"
