"""
Integration tests for main.py terminal CLI markdown support and code minimization.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from main import _display_help, _handle_ide_command, process_input
from model.registry.model_registry import ModelRegistry
from utils.cli_formatter import SESSION_CODE_TRACKER, TerminalFormatConfig


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


@pytest.mark.asyncio
async def test_process_input_non_stream_markdown(tmp_path: Path):
    cfg = TerminalFormatConfig(code_display="minimized", min_lines_threshold=2)

    # Mock API Client
    api_client = MagicMock()
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": "Here is the python code:\n\n```python\nx = 1\ny = 2\nz = 3\nprint(x + y + z)\n```"
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 25},
    }
    api_client.chat_completion = AsyncMock(return_value=mock_response)

    # Mock Classifier & Router
    classifier = MagicMock()
    classification = MagicMock()
    classification.task_type.value = "code"
    from brain.classifier.intent_classifier import TaskType
    classification.task_type = TaskType.CODE
    classification.confidence = 0.95
    classifier.classify_with_difficulty.return_value = (classification, "easy")

    model_mock = MagicMock()
    model_mock.id = "test-model"
    model_mock.provider = "ag"
    model_mock.display_name = "Test Model"
    model_mock.supports_tools = False

    router = MagicMock()
    router.route.return_value = model_mock

    context_assembler = MagicMock()
    ctx_mock = MagicMock()
    ctx_mock.messages = [{"role": "user", "content": "test"}]
    context_assembler.assemble.return_value = ctx_mock

    conversation_store = MagicMock()
    health_checker = MagicMock()
    fallback = MagicMock()

    SESSION_CODE_TRACKER.clear()

    res = await process_input(
        user_input="test code",
        session_id="test-session",
        api_client=api_client,
        classifier=classifier,
        router=router,
        context_assembler=context_assembler,
        conversation_store=conversation_store,
        health_checker=health_checker,
        fallback=fallback,
        stream=False,
        terminal_config=cfg,
    )

    assert "Here is the python code" in res
    assert SESSION_CODE_TRACKER.count() >= 1
    block = SESSION_CODE_TRACKER.get_block(1)
    assert block is not None
    assert block.language == "python"
    assert "print(x + y + z)" in block.code


@pytest.mark.asyncio
async def test_process_input_autonomous_tool_loop(tmp_path: Path):
    """Test that process_input automatically detects and executes DSML/tool calls and loops to final response."""
    cfg = TerminalFormatConfig()

    # Step 1: Model responds with DSML invoke
    dsml_output = (
        '｜DSML｜｜ invoke name="execute_command">\n'
        '<｜｜DSML｜｜ parameter name="command" string="true">echo "hello from workspace"</｜｜DSML｜｜ parameter>\n'
        '</｜｜DSML｜｜ invoke>\n'
        '</｜｜DSML｜｜ calls>'
    )
    # Step 2: After tool execution, model responds with final answer
    final_output = "Hasil eksekusi: workspace berjalan lancar."

    api_client = MagicMock()
    api_client.chat_completion = AsyncMock(
        side_effect=[
            {
                "choices": [{"message": {"content": dsml_output}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 30},
            },
            {
                "choices": [{"message": {"content": final_output}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        ]
    )

    classifier = MagicMock()
    from brain.classifier.intent_classifier import TaskType
    classification = MagicMock()
    classification.task_type = TaskType.ACTIVITY
    classification.confidence = 0.95
    classifier.classify_with_difficulty.return_value = (classification, "easy")

    model_mock = MagicMock()
    model_mock.id = "kg/deepseek-4-1-flash"
    model_mock.provider = "kg"
    model_mock.display_name = "DeepSeek 4.1 Flash"
    model_mock.supports_tools = True

    router = MagicMock()
    router.route.return_value = model_mock

    context_assembler = MagicMock()
    ctx_mock = MagicMock()
    ctx_mock.messages = [{"role": "user", "content": "cek workspace"}]
    context_assembler.assemble.return_value = ctx_mock

    conversation_store = MagicMock()
    health_checker = MagicMock()
    fallback = MagicMock()

    tool_registry = MagicMock()
    tool_registry.get_all_schemas.return_value = [{"type": "function", "function": {"name": "execute_command"}}]
    tool_registry.execute = AsyncMock(return_value={"stdout": "hello from workspace\n", "exit_code": 0})

    res = await process_input(
        user_input="cek workspace",
        session_id="test-session-tool",
        api_client=api_client,
        classifier=classifier,
        router=router,
        context_assembler=context_assembler,
        conversation_store=conversation_store,
        health_checker=health_checker,
        fallback=fallback,
        stream=False,
        terminal_config=cfg,
        tool_registry=tool_registry,
        active_workspace=tmp_path,
    )

    # Verify tool was called automatically with cwd set to active_workspace
    tool_registry.execute.assert_called_once()
    called_name, called_args = tool_registry.execute.call_args[0]
    assert called_name == "execute_command"
    assert called_args["command"] == 'echo "hello from workspace"'
    assert called_args["cwd"] == str(tmp_path)

    # Verify API was called twice (round 1: invoke tool, round 2: answer with result)
    assert api_client.chat_completion.call_count == 2

    # Verify final response is clean without DSML tags
    assert "Hasil eksekusi: workspace berjalan lancar." in res
    assert "DSML" not in res

