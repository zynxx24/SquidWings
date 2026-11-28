"""The shared agent settings: Claude Code's are the source, Codex's are generated from them and must not drift."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sync_agent_config", ROOT / "scripts" / "sync_agent_config.py")
assert _spec and _spec.loader
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def test_codex_copy_is_in_sync_with_claude_settings() -> None:
    assert sync.differences() == [], "run: .venv/bin/python scripts/sync_agent_config.py"


def test_every_rule_claude_imports_exists_and_codex_gets_its_text() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    imports = re.findall(r"^@(\S+\.md)\s*$", claude, re.MULTILINE)
    assert {"plan-first.md", "quality-bar.md", "video-script-first.md", "lessons.md"} <= {Path(i).name for i in imports}
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for rel in imports:
        heading = (ROOT / rel).read_text(encoding="utf-8").splitlines()[0]
        assert heading in agents
    assert not re.search(r"^@\S+\.md\s*$", agents, re.MULTILINE)


def test_skills_have_a_name_matching_their_folder_and_a_description() -> None:
    folders = sorted((ROOT / ".claude" / "skills").iterdir())
    assert {f.name for f in folders} >= {"super-plan", "quality-gate", "video-script"}
    for folder in folders:
        head = (folder / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        assert re.search(rf"^name: {re.escape(folder.name)}$", head, re.MULTILINE)
        assert re.search(r"^description: .{40,}$", head, re.MULTILINE)


def test_shared_settings_keep_secrets_unreadable() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    denied = settings["permissions"]["deny"]
    assert "Read(./.env)" in denied and "Read(./web/data/credentials.json)" in denied


def test_expand_refuses_a_missing_import(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(SystemExit, match="not found"):
        sync.expand("@.claude/rules/does-not-exist.md", ROOT)


def test_the_harness_prompt_carries_every_rule_once() -> None:
    from brain.config.constants import TaskType
    from brain.system_prompt.base_system_prompt import SystemPromptManager

    prompt = SystemPromptManager().get_system_prompt(TaskType.CODE)
    for marker in ("Plan first:", "must meet all six", "Video: write the final script first", "Lessons:"):
        assert prompt.count(marker) == 1, marker


def test_the_harness_quality_skill_is_the_quality_gate_text() -> None:
    harness = (ROOT / "skills" / "builtin" / "bar-kualitas.md").read_text(encoding="utf-8")
    gate = (ROOT / ".claude" / "skills" / "quality-gate" / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    assert gate.strip() in harness and "name: bar-kualitas" in harness


def test_long_guides_arrive_as_an_outline_and_open_by_section() -> None:
    from skills import sections

    body = "Intro line.\n\n## Setup\nInstall it.\n\n## Usage\nRun it.\n" + "x" * 20_000
    outline = sections.outline("big", body)
    assert len(outline) < 2_000 and 'load_skill(name="big", section="Usage")' in outline
    assert sections.section(body, "usage").startswith("## Usage")
    assert sections.section(body, "missing") is None
    assert sections.outline("small", "## A\nshort") == "## A\nshort"
