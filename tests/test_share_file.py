"""Handing files to the user: share_file, the per-session manifest, and the web route."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.harness import tool_select
from agent.harness.tiers import _SCAFFOLDS, Tier
from config.constants import TaskType
from tool.execute.execute_command_prompt import run_command_tool
from tool.execute.execute_share_file import share_tool
from utils import session_settings, shared_files
from utils.workspace_context import set_active_session, set_active_workspace
from web import server


@pytest.fixture
def session(tmp_path: Path) -> Any:
    folder = tmp_path / "chat-a"
    (folder / "pieces/demo/renders").mkdir(parents=True)
    (folder / "pieces/demo/renders/share.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42video")
    (folder / "pieces/demo/index.html").write_text("<script>alert(1)</script>")
    (folder / "secret.txt").write_text("rahasia")
    set_active_workspace(folder)
    set_active_session("chat-a")
    yield folder
    set_active_workspace("")
    set_active_session("")


async def test_sharing_a_video_lists_it_for_this_session(session: Path) -> None:
    result = await share_tool.share_file("pieces/demo/renders/share.mp4", caption="Sejarah internet")
    assert result["ok"] and result["kind"] == "video"
    assert result["url"] == "/api/session/chat-a/files/pieces/demo/renders/share.mp4"
    assert result["markdown"] == f"![Sejarah internet]({result['url']})"
    assert shared_files.shared(session) == ["pieces/demo/renders/share.mp4"]


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", ".agent_memory/shared.json", "tidak-ada.mp4"])
async def test_files_outside_the_session_or_its_memory_are_refused(session: Path, path: str) -> None:
    (session / ".agent_memory").mkdir(exist_ok=True)
    (session / ".agent_memory/shared.json").write_text("[]")
    (session.parent / "outside.txt").write_text("x")
    assert (await share_tool.share_file(path))["ok"] is False


def test_the_route_serves_only_what_was_shared(session: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "session_workspace", lambda _id, create=True: session)
    shared_files.add(session, "pieces/demo/renders/share.mp4")
    shared_files.add(session, "pieces/demo/index.html")
    client = TestClient(server.app)
    video = client.get("/api/session/chat-a/files/pieces/demo/renders/share.mp4")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert video.headers["content-disposition"].startswith("inline")
    # Seeking in a video needs byte ranges.
    assert client.get("/api/session/chat-a/files/pieces/demo/renders/share.mp4", headers={"Range": "bytes=0-3"}).status_code == 206
    # Shared HTML is downloaded, never rendered on this origin.
    page = client.get("/api/session/chat-a/files/pieces/demo/index.html")
    assert page.headers["content-disposition"].startswith("attachment")
    assert client.get("/api/session/chat-a/files/secret.txt").status_code == 404
    assert client.get("/api/session/chat-a/files/..%2Fsecret.txt").status_code == 404


def test_command_timeouts_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    class Executor:
        def __init__(self, default_timeout: float) -> None:
            seen.append(default_timeout)

        async def execute(self, **_kwargs: Any) -> Any:
            raise RuntimeError("berhenti di sini")

    monkeypatch.setattr(run_command_tool, "CommandExecutor", Executor)
    import asyncio

    asyncio.run(run_command_tool.execute_command("echo hai", timeout=99_999))
    asyncio.run(run_command_tool.execute_command("echo hai", timeout=600))
    assert seen == [run_command_tool.MAX_COMMAND_SECONDS, 600]


def test_an_awake_skill_brings_its_tools_on_a_small_model(session: Path) -> None:
    names = [f"t{i}" for i in range(40)] + ["share_file", "execute_command", "load_skill", "read_file"]
    schemas = [{"type": "function", "function": {"name": n, "parameters": {"properties": {}}}} for n in names]
    lite = _SCAFFOLDS[Tier.LITE]

    def offered(text: str) -> list[str]:
        return [s["function"]["name"] for s in tool_select.select_tools(schemas, TaskType.CODE, text, lite)]

    assert "share_file" not in offered("lanjutkan panel 3")
    session_settings.activate_skills(session, ["animate"])
    assert "share_file" in offered("lanjutkan panel 3")


async def test_the_loop_puts_shared_files_under_the_answer(session: Path) -> None:
    from agent.agent_loop import AgentLoop
    from model.connection.response_handler import ParsedResponse
    from tool.registry.tool_registry import ToolRegistry
    from tool.registry.tool_schema import ToolSchema

    tools = ToolRegistry()
    tools.reset()
    tools.register(ToolSchema(name="share_file", description="share"), share_tool.share_file)
    loop = AgentLoop(object(), tools, max_iterations=3)  # type: ignore[arg-type]
    replies = [
        ParsedResponse(content="", tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "share_file", "arguments": '{"path": "pieces/demo/renders/share.mp4", "caption": "Demo"}'},
        }], finish_reason="tool_calls"),
        ParsedResponse(content="Videonya sudah jadi.", finish_reason="stop"),
    ]

    async def call_model(**_kwargs: Any) -> ParsedResponse:
        return replies.pop(0)

    loop._call_model = call_model  # type: ignore[method-assign]
    result = await loop.run("mock", [{"role": "user", "content": "kirim videonya"}], stream=False)
    assert result.shared == ["![Demo](/api/session/chat-a/files/pieces/demo/renders/share.mp4)"]
