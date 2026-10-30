"""Each chat owns its folder, vault, graph, score, and Ponytail mode."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from agent import session_stack
from brain.system_prompt.base_system_prompt import SystemPromptManager
from config.constants import TaskType
from tool.execute.execute_code_intel import quality_tools
from tool.execute.execute_local_memory import vault_context, vault_tool
from utils.workspace_context import (
    get_active_ponytail,
    get_active_workspace,
    set_active_ponytail,
    set_active_workspace,
)


@pytest.fixture(autouse=True)
def leave_no_session() -> Any:
    yield
    set_active_workspace("")
    set_active_ponytail("")


def test_each_session_gets_its_own_folder() -> None:
    first = session_stack.enter("sess-a").workspace
    second = session_stack.enter("sess-b").workspace
    assert first != second
    assert get_active_workspace() == str(second)


def test_an_open_project_is_used_instead(tmp_path: Path) -> None:
    assert session_stack.enter("sess-a", tmp_path).workspace == tmp_path


async def test_vault_notes_stay_in_their_own_session(tmp_path: Path) -> None:
    session_stack.enter("sess-a", tmp_path / "a")
    await vault_tool.vault_write(path="20-concepts/rahasia-a.md", body="# Rahasia A\nkopi", note_type="concept")
    assert (tmp_path / "a" / ".vault" / "20-concepts" / "rahasia-a.md").exists()
    assert (await vault_tool.vault_search(query="rahasia"))["ok"]

    session_stack.enter("sess-b", tmp_path / "b")
    found = await vault_tool.vault_search(query="rahasia")
    assert not found.get("notes") and not found.get("results")
    assert not (tmp_path / "b" / ".vault" / "20-concepts" / "rahasia-a.md").exists()


def test_profile_and_playbooks_stay_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_stack.enter("sess-a", tmp_path / "a")
    assert vault_context.vault_path() == tmp_path / "a" / ".vault"
    set_active_workspace("")
    assert vault_context.vault_path() == vault_context.global_vault_path()


def test_settings_are_per_session(tmp_path: Path) -> None:
    first = session_stack.enter("sess-a", tmp_path / "a")
    first.update(ponytail="ultra", graphify=False)
    second = session_stack.enter("sess-b", tmp_path / "b")
    assert second.settings.ponytail == ""
    assert second.settings.graphify is True
    session_stack.enter("sess-a", tmp_path / "a")
    assert get_active_ponytail() == "ultra"


def test_invalid_settings_are_ignored(tmp_path: Path) -> None:
    stack = session_stack.enter("sess-a", tmp_path)
    stack.update(ponytail="turbo", graphify="yes", unknown=1)
    assert stack.settings.ponytail == ""
    assert stack.settings.graphify is True


def test_ponytail_mode_changes_the_prompt_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PONYTAIL_TASKS", "code")
    manager = SystemPromptManager()
    set_active_ponytail("off")
    without = manager.get_system_prompt(TaskType.CODE)
    set_active_ponytail("ultra")
    with_ponytail = manager.get_system_prompt(TaskType.CODE)
    assert "Ponytail" not in without
    assert "Ponytail" in with_ponytail


def test_status_reports_everything_the_session_owns(tmp_path: Path) -> None:
    info = session_stack.enter("sess-a", tmp_path).status()
    for key in ("ponytail", "graphify", "aislop", "vault", "memory", "profile", "sandbox"):
        assert key in info
    assert info["folder"] == str(tmp_path)
    assert info["vault"]["notes"] == 0


def test_contexts_do_not_mix_between_concurrent_sessions(tmp_path: Path) -> None:
    async def turn(name: str) -> str:
        session_stack.enter(name, tmp_path / name)
        await asyncio.sleep(0)
        return get_active_workspace()

    async def main() -> list[str]:
        return await asyncio.gather(*(asyncio.create_task(turn(name)) for name in ("x", "y", "z")))

    assert asyncio.run(main()) == [str(tmp_path / name) for name in ("x", "y", "z")]


REPORT = {
    "score": 72,
    "label": "Needs work",
    "summary": {"errors": 1, "warnings": 2},
    "engines": {"lint": {"missingTools": ["ruff"]}},
    "diagnostics": [
        {"filePath": "app.py", "line": 3, "rule": "ai-slop/swallowed-exception", "severity": "error", "message": "empty except", "help": "handle it"},
        {"filePath": "app.py", "line": 9, "rule": "format/ruff", "severity": "warning", "message": "format"},
        {"filePath": "other.py", "line": 1, "rule": "ai-slop/trivial-comment", "severity": "warning", "message": "comment"},
    ],
}


def fake_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command: Any, *_args: Any, **_kwargs: Any) -> tuple[CompletedProcess[str], Any]:
        return CompletedProcess(command, 1, stdout=json.dumps(REPORT), stderr=""), None

    monkeypatch.setattr(quality_tools.sandbox, "run_blocking", run)
    monkeypatch.setattr(quality_tools, "has_aislop", lambda: True)


async def test_scan_reports_score_and_remembers_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_scan(monkeypatch)
    result = await quality_tools.aislop_scan(workspace_path=str(tmp_path))
    assert result["score"] == 72
    assert "app.py:3 ai-slop/swallowed-exception" in result["findings"][0]
    assert result["missing_tools"] == ["ruff"]
    assert quality_tools.last_score(tmp_path)["score"] == 72


def test_gate_only_reports_real_problems_in_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_scan(monkeypatch)
    monkeypatch.setenv("AISLOP_AUTO", "true")
    (tmp_path / "app.py").write_text("x = 1\n")
    found = quality_tools.findings_for_changes(tmp_path, [tmp_path / "app.py"])
    assert len(found) == 1 and "swallowed-exception" in found[0]


def test_gate_respects_the_session_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_scan(monkeypatch)
    monkeypatch.setenv("AISLOP_AUTO", "true")
    session_stack.enter("sess-a", tmp_path).update(aislop=False)
    assert quality_tools.findings_for_changes(tmp_path, [tmp_path / "app.py"]) == []


async def test_missing_programs_are_reported_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quality_tools.shutil, "which", lambda _name: None)
    assert "not installed" in (await quality_tools.aislop_scan(workspace_path=str(tmp_path)))["error"]
    assert "not installed" in (await quality_tools.code_graph(workspace_path=str(tmp_path)))["error"]
    assert quality_tools.refresh_graph(tmp_path) is False
    assert quality_tools.graph_brief("x", tmp_path) == ""


def test_graph_stats_read_the_session_graph(tmp_path: Path) -> None:
    assert quality_tools.graph_stats(tmp_path) == {"built": False}
    (tmp_path / "graphify-out").mkdir()
    (tmp_path / "graphify-out" / "graph.json").write_text('{"nodes":[1,2],"links":[1]}')
    stats = quality_tools.graph_stats(tmp_path)
    assert stats["built"] and stats["nodes"] == 2 and stats["edges"] == 1
