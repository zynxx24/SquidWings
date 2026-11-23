"""terminal_run: type a command into the user's Terminal panel (a real shell on this machine).

``execute_command`` runs in the sandbox, which is right for almost everything. This tool is
for what should stay visible to the user: ``sudo`` (answered with the saved password, else typed by the user), system
services, package managers, and servers the user opens in the browser. See
``utils.web_terminal`` for how it is wrapped.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from utils import web_terminal
from utils.logger import get_logger
from utils.workspace_context import get_active_session, get_active_workspace

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 120
_MAX_TIMEOUT = 900


def _audit(command: str, result: dict[str, Any]) -> None:
    try:
        from security.audit_chain import audit

        audit("terminal_run", actor="agent", session=get_active_session(), command=command[:500],
              exit_code=result.get("exit_code"), finished=result.get("finished"))
    except Exception as exc:  # noqa: BLE001 - the audit log must not stop the command
        logger.debug(f"[terminal] audit failed: {exc}")


async def terminal_run(command: str = "", timeout: int = _DEFAULT_TIMEOUT, interrupt: bool = False,
                       answer: str = "") -> dict[str, Any]:
    """Run a command in this chat's Terminal panel and wait for it to finish (or for the timeout)."""
    if not web_terminal.enabled():
        return {
            "ok": False,
            "error": "There is no Terminal panel here (this is not the web app).",
            "note": "Ask the user to run the command themselves. In the CLI they can type it after '!'.",
        }
    session = get_active_session() or "default"
    workspace = get_active_workspace() or os.getenv("SQUIDWINGS_WORKSPACE", "").strip() or os.getcwd()
    timeout = max(1, min(int(timeout or _DEFAULT_TIMEOUT), _MAX_TIMEOUT))
    command = (command or "").strip()
    if command:
        reason = web_terminal.forbidden(command)
        if reason:
            return {"ok": False, "blocked": True, "error": f"Refused: {reason}. The user can type it themselves if they really mean it."}
    term = await web_terminal.open_terminal(session, Path(workspace))
    if answer:
        # A question from the running command ("Continue? [Y/n]", a config wizard). Never a password:
        # that is the user's to type in the panel.
        tail = web_terminal.clean(term.output_since(max(0, term.total - 400))).rstrip()
        if web_terminal.waiting_for_password(tail):
            return {"ok": False, "error": "The terminal is asking for a password. Only the user types that, in the Terminal panel."}
        if not term.agent_command or term.at_prompt:
            return {"ok": False, "error": "Nothing is waiting for an answer; run the command with terminal_run(command=...)."}
        term.write(answer + "\r")
        result = await web_terminal.wait_more(term, timeout)
        _audit(f"(answer) {answer}", result)
        return {**result, "answered": answer}
    if interrupt:
        term.interrupt()
        result = await web_terminal.wait_more(term, 5)
        _audit("^C", result)
        return {**result, "interrupted": True}
    result = await (web_terminal.run(term, command, timeout) if command else web_terminal.wait_more(term, timeout))
    if command:
        _audit(command, result)
    return {"command": command, **result, "shown_in": "Terminal panel"}



async def preview_url(port: int, public: bool | None = None) -> dict[str, Any]:
    """The link to a dev server on this machine; a public https link when the user works from afar."""
    from security.web_access import is_public_mode
    from utils import preview

    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "error": "port must be a number, e.g. 8000"}
    if not preview.listening(port):
        return {
            "ok": False, "port": port,
            "error": f"Nothing is listening on port {port} yet.",
            "note": "Start the server in the Terminal panel first with terminal_run (it keeps running), e.g. "
                    "'php artisan serve --host=127.0.0.1 --port=8000', 'python3 -m http.server 8090', "
                    "'npm run dev -- --host 127.0.0.1', then call preview_url again.",
        }
    result: dict[str, Any] = {"ok": True, "port": port, "local": preview.local_url(port)}
    if public is None:
        public = is_public_mode()
    if public:
        try:
            result["public"] = await preview.open_public(port)
            if wait := preview.ready_in(port):
                result["ready_in_seconds"] = wait
        except (RuntimeError, ValueError, OSError) as exc:
            result["public_error"] = str(exc)
    result["note"] = (
        "Give the user the public link: it opens from any device. A new link works about half a minute after "
        "it is made; if the browser says the site cannot be found, wait a moment and reload." if result.get("public")
        else "Give the user the local link; it opens in a browser on this machine."
    )
    return result


__all__ = ["preview_url", "terminal_run"]
