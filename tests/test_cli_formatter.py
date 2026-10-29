"""
Unit tests for utils.cli_formatter
"""

from pathlib import Path

from rich.console import Console

from utils.cli_formatter import (
    CodeBlockInfo,
    CodeSessionTracker,
    TerminalFormatConfig,
    TerminalStreamRenderer,
    handle_code_command,
    handle_format_command,
    parse_tag,
    render_code_panel,
    render_terminal_markdown,
)


def test_parse_tag():
    lang, b_type, t_path = parse_tag("python")
    assert lang == "python"
    assert b_type == "snippet"
    assert t_path is None

    lang, b_type, t_path = parse_tag("file:src/app.py")
    assert lang == "python"
    assert b_type == "file"
    assert t_path == "src/app.py"

    lang, b_type, t_path = parse_tag("replace:config/settings.json")
    assert lang == "json"
    assert b_type == "replace"
    assert t_path == "config/settings.json"


def test_code_session_tracker():
    tracker = CodeSessionTracker()
    assert tracker.count() == 0

    block = CodeBlockInfo(
        index=1,
        language="python",
        code="print('hello')",
        line_count=1,
    )
    tracker.add_block(block)
    assert tracker.count() == 1
    assert tracker.get_block(1) == block
    assert tracker.get_block(2) is None


def test_render_code_panel():
    config = TerminalFormatConfig(
        code_display="minimized",
        min_lines_threshold=2,
        preview_lines=2,
    )
    block = CodeBlockInfo(
        index=1,
        language="python",
        code="line 1\nline 2\nline 3\nline 4\nline 5",
        line_count=5,
    )
    panel = render_code_panel(block, config)
    assert panel is not None

    full_panel = render_code_panel(block, config, force_full=True)
    assert full_panel is not None


def test_render_terminal_markdown():
    console = Console(record=True, width=80)
    config = TerminalFormatConfig()
    tracker = CodeSessionTracker()

    sample_md = (
        "# Test Header\n\n"
        "This is markdown text with **bold**.\n\n"
        "```python\n"
        "x = 1\n"
        "y = 2\n"
        "z = 3\n"
        "print(x + y + z)\n"
        "```\n"
    )
    render_terminal_markdown(sample_md, console, config, tracker)
    output = console.export_text()
    assert "Test Header" in output
    assert tracker.count() >= 1


def test_commands(tmp_path: Path):
    cfg_file = tmp_path / "terminal_format.json"
    console = Console(record=True, width=80)
    config = TerminalFormatConfig()
    tracker = CodeSessionTracker()

    block = CodeBlockInfo(
        index=1,
        language="python",
        code="def foo():\n    return 42",
        line_count=2,
    )
    tracker.add_block(block)

    # Test /code list
    res = handle_code_command("list", console, config, tracker, cfg_file)
    assert res is True

    # Test /code 1
    res = handle_code_command("1", console, config, tracker, cfg_file)
    assert res is True

    # Test /code full
    res = handle_code_command("full", console, config, tracker, cfg_file)
    assert res is True

    # Test /format
    res = handle_format_command("", console, config, cfg_file)
    assert res is True

    # Test /format theme dracula
    res = handle_format_command("theme dracula", console, config, cfg_file)
    assert res is True
    assert config.syntax_theme == "dracula"


def test_stream_renderer():
    console = Console(record=True, width=80)
    config = TerminalFormatConfig()
    tracker = CodeSessionTracker()
    renderer = TerminalStreamRenderer(console, config, tracker)

    # Stream chunks
    renderer.on_content_chunk("Hello! Here is code:\n\n```python\n")
    renderer.on_content_chunk("a = 10\nb = 20\n")
    renderer.on_content_chunk("c = a + b\n```\n")
    renderer.on_content_chunk("Done!")
    renderer.finish()

    assert tracker.count() == 1
    block = tracker.get_block(1)
    assert block is not None
    assert block.language == "python"
    assert "c = a + b" in block.code
