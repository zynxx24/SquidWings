"""What reaches the model: history sized to the model's tier, prompt files that apply when edited."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from brain.config.constants import TaskType
from input.context import context_assembler
from input.prompt_must_think.base_think_prompt import ThinkPromptManager


def test_history_has_no_cap_of_our_own(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HISTORY_MAX_TOKENS", raising=False)
    assert context_assembler.history_cap("x/gemini-flash") == 0, "the model's window decides, up to 1M"
    monkeypatch.setenv("HISTORY_MAX_TOKENS", "3000")
    assert context_assembler.history_cap("x/claude-opus-9") == 3000, "an explicit setting still applies"


def test_an_unknown_window_is_one_million():
    from brain.model.registry.model_info import UNKNOWN_CONTEXT_WINDOW, ModelInfo

    assert UNKNOWN_CONTEXT_WINDOW == 1_000_000
    assert ModelInfo.from_api_response({"id": "x/new-model"}).context_window == 1_000_000


def test_an_edited_think_prompt_applies_without_a_restart(tmp_path: Path):
    prompt = tmp_path / "for_code.txt"
    prompt.write_text("first version")
    manager = ThinkPromptManager(tmp_path)
    manager._watch.interval = 0.0
    assert manager.get_think_prompt(TaskType.CODE) == "first version"
    prompt.write_text("second version, longer")
    later = time.time() + 5
    os.utime(prompt, (later, later))
    assert manager.get_think_prompt(TaskType.CODE) == "second version, longer"
