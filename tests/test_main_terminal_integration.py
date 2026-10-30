"""
Integration tests for main.py terminal CLI markdown support and code minimization.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from rich.console import Console

from main import _display_help, _handle_ide_command, process_input
from model.registry.model_registry import ModelRegistry
from utils.cli_formatter import TerminalFormatConfig


def test_display_help_contains_new_commands():

    # Capture help output
    test_console = Console(record=True, width=100)
    import main
    orig_console = main.console
    main.console = test_console

    try:
        _display_help()
        output = test_console.export_text()
        assert "/code" in output
        assert "/format" in output
        assert "/theme" in output
        assert "/md" in output
    finally:
        main.console = orig_console


def test_handle_ide_command_code_and_format(tmp_path: Path):
    registry = MagicMock(spec=ModelRegistry)
    project_root = tmp_path
    workspace_dir = tmp_path / "workspace"
    cfg_file = tmp_path / "config" / "terminal_format.json"
    cfg = TerminalFormatConfig.load(cfg_file)

    # 1. Test /format theme dracula
    handled, new_model, new_ws = _handle_ide_command(
        "format theme dracula",
        registry,
        project_root,
        workspace_dir,
        current_model=None,
        active_workspace=None,
        terminal_config=cfg,
        terminal_cfg_path=cfg_file,
    )
    assert handled is True
    assert cfg.syntax_theme == "dracula"
    assert cfg_file.exists()

    # 2. Test /code toggle
    prev_mode = cfg.code_display
    handled, _, _ = _handle_ide_command(
        "code toggle",
        registry,
        project_root,
        workspace_dir,
        current_model=None,
        active_workspace=None,
        terminal_config=cfg,
        terminal_cfg_path=cfg_file,
    )
    assert handled is True
    assert cfg.code_display != prev_mode

    # 3. Test /theme nord
    handled, _, _ = _handle_ide_command(
        "theme nord",
        registry,
        project_root,
        workspace_dir,
        current_model=None,
        active_workspace=None,
        terminal_config=cfg,
        terminal_cfg_path=cfg_file,
    )
    assert handled is True
    assert cfg.syntax_theme == "nord"


def real_orchestrator(tmp_path: Path, *replies: dict) -> Any:
    from agent.orchestrator import Orchestrator
    from brain.memory.conversation_store import ConversationStore
    from config.settings import Settings
    from model.registry.model_info import ModelCapabilities, ModelInfo

    queue = list(replies)

    class Client:
        async def chat_completion(self, **_kwargs):
            return queue.pop(0)

    orch = Orchestrator.from_settings(Settings(), api_client=Client())
    store = ConversationStore(storage_dir=tmp_path / "chat")
    orch._conversation_store = store
    orch._context_assembler._store = store
    orch.registry.register(
        ModelInfo(id="ag/m", display_name="M", provider="ag", capabilities=ModelCapabilities(tools=True))
    )
    return orch


def reply(content: str = "", calls: list | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)
        ]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}], "usage": {}}


async def test_process_input_returns_and_shows_the_answer(tmp_path: Path, capsys: Any) -> None:
    orch = real_orchestrator(tmp_path, reply("Halo **dunia**"))
    result = await process_input(
        user_input="halo", session_id="s", orchestrator=orch, stream=False, override_model="ag/m",
    )
    assert result == "Halo **dunia**"
    assert "dunia" in capsys.readouterr().out


async def test_process_input_runs_tools_through_the_orchestrator(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    orch = real_orchestrator(
        tmp_path,
        reply("", [("file_write", {"path": "a.txt", "content": "isi"})]),
        reply("Selesai."),
    )
    result = await process_input(
        user_input="tulis file a.txt", session_id="s", orchestrator=orch, stream=False,
        override_model="ag/m", active_workspace=workspace,
    )
    assert result == "Selesai."
    assert (workspace / "a.txt").read_text() == "isi"


async def test_process_input_reports_failure_without_raising(tmp_path: Path) -> None:
    orch = real_orchestrator(tmp_path)
    orch._api_client.chat_completion = None  # any call now fails
    result = await process_input(
        user_input="halo", session_id="s", orchestrator=orch, stream=False, override_model="ag/m",
    )
    assert isinstance(result, str) and result


def test_cli_session_starts_inside_a_new_project(tmp_path: Path) -> None:
    from main import _start_project

    folder, auto = _start_project(tmp_path, "sess-abcdef123456")
    assert folder.parent == tmp_path and folder.is_dir()
    assert folder.name.endswith("-abcdef")
    assert auto is True


def test_cli_session_can_open_an_existing_project(tmp_path: Path) -> None:
    from main import _start_project

    (tmp_path / "toko").mkdir()
    folder, auto = _start_project(tmp_path, "sess-1", "toko")
    assert folder == (tmp_path / "toko").resolve()
    assert auto is False


def test_project_name_cannot_leave_the_projects_folder(tmp_path: Path) -> None:
    from main import _project_path, _start_project

    root = tmp_path / "projects"
    root.mkdir()
    assert _project_path(root, "../../etc") is None
    assert _project_path(root, "/etc") == (root / "etc").resolve()  # a name, never the real /etc
    assert _project_path(root, "..") is None
    assert _project_path(root, "workspace/projects/toko") == (root / "toko").resolve()
    folder, _ = _start_project(root, "sess-abcdef", "../luar")
    assert folder.parent == root
    assert not (tmp_path / "luar").exists()


def test_unused_project_is_removed_but_used_ones_stay(tmp_path: Path) -> None:
    from brain.memory.project_memory import ProjectMemory
    from main import _remove_if_unused

    unused = tmp_path / "kosong"
    unused.mkdir()
    (unused / ".agent_memory").mkdir()
    _remove_if_unused(unused)
    assert not unused.exists()

    with_file = tmp_path / "ada-file"
    with_file.mkdir()
    (with_file / "app.py").write_text("x = 1\n")
    _remove_if_unused(with_file)
    assert with_file.exists()

    chatted = tmp_path / "pernah-chat"
    chatted.mkdir()
    ProjectMemory(chatted).build_input("halo", False)
    _remove_if_unused(chatted)
    assert chatted.exists()
