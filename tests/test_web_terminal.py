"""The Terminal panel's shell: the agent's commands, sudo hand-off, and who may open it."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import web_terminal as wt


def test_forbidden_commands_are_refused_and_ordinary_ones_pass() -> None:
    for command in ("rm -rf /", "sudo rm -rf ~", "mkfs.ext4 /dev/sda1", "dd if=x of=/dev/nvme0n1", "sudo reboot",
                    ":(){ :|:& };:"):
        assert wt.forbidden(command), command
    for command in ("rm -rf ./build", "sudo apt install php-xml", "dd if=a of=b.img", "echo reboot later",
                    "php artisan serve", "chmod -R 775 storage"):
        assert not wt.forbidden(command), command


def test_clean_drops_colours_and_progress_redraws() -> None:
    assert wt.clean(b"\x1b[32mok\x1b[0m\r\n10%\r50%\r100%\n\x1b]0;title\x07x") == "ok\n100%\nx"


async def test_run_waits_for_the_prompt_and_reports_the_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_API_KEY", "secret-value")
    term = await wt.open_terminal("pytest-term", tmp_path)
    try:
        ok = await wt.run(term, "echo hello; pwd; echo ${SOME_API_KEY:-hidden}", 10)
        assert ok["finished"] and ok["exit_code"] == 0
        assert ok["output"].splitlines() == ["hello", str(tmp_path), "hidden"]
        failed = await wt.run(term, "false", 10)
        assert failed["exit_code"] == 1 and failed["ok"] is False
        slow = await wt.run(term, "sleep 2; echo late", 0.5)
        assert slow["finished"] is False and slow["still_running"]
        assert (await wt.run(term, "echo other", 1))["busy"]
        rest = await wt.wait_more(term, 5)
        assert rest["finished"] and "late" in rest["output"]
    finally:
        wt.close("pytest-term")


async def test_terminal_run_says_so_outside_the_web_app() -> None:
    from tool.execute.execute_command_prompt.terminal_tool import terminal_run

    wt.enable(False)
    result = await terminal_run("sudo apt install php-xml")
    assert result["ok"] is False and "!" in result["note"]


async def test_execute_command_hands_sudo_to_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_command_prompt import run_command_tool

    seen: list[str] = []

    async def fake(command: str, timeout: int = 120, interrupt: bool = False) -> dict:
        seen.append(command)
        return {"ok": True, "finished": True, "exit_code": 0, "output": "done"}

    monkeypatch.setattr(run_command_tool, "terminal_run", fake)
    wt.enable(True)
    try:
        result = await run_command_tool.execute_command("sudo apt install -y php-xml")
    finally:
        wt.enable(False)
    assert seen == ["sudo apt install -y php-xml"] and result["output"] == "done"
    wt.enable(False)
    refused = await run_command_tool.execute_command("sudo systemctl start mysql")
    assert refused["blocked"] and "password" in refused["error"]


def test_the_panel_opens_only_on_this_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from web.server import app

    monkeypatch.setenv("WEB_TERMINAL_PUBLIC", "false")
    with TestClient(app) as client:
        assert client.get("/api/terminal/status").json()["allowed"] is False
        with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect("/ws/terminal?session=sess-abcdef123456") as ws:
            ws.receive_json()
        assert refused.value.code == 4403


def test_the_panel_streams_the_shell_when_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from web.server import app

    monkeypatch.setenv("WEB_TERMINAL_PUBLIC", "true")
    monkeypatch.setenv("SQUIDWINGS_WORKSPACE", str(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/ws/terminal?session=sess-abcdef123456") as ws:
        ws.send_json({"type": "input", "data": "echo panel-$((40+2))\r"})
        seen = ""
        for _ in range(200):
            seen += ws.receive_json().get("data", "")
            if "panel-42" in seen:
                break
        assert "panel-42" in seen
    wt.close("sess-abcdef123456")


async def test_preview_needs_a_running_server_and_gives_the_local_link(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_command_prompt.terminal_tool import preview_url
    from utils import preview

    monkeypatch.setattr(preview, "listening", lambda port, host="127.0.0.1": port == 8000)
    missing = await preview_url(5173, public=False)
    assert missing["ok"] is False and "terminal_run" in missing["note"]
    found = await preview_url(8000, public=False)
    assert found["ok"] and found["local"] == "http://127.0.0.1:8000" and "public" not in found
