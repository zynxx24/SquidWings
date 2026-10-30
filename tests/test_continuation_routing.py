"""Continuation turns must resume the model that did the work.

A bare "lanjutkan..." has no intent of its own. The heuristic classifier can
only answer "activity/easy, conf≈0.30", and routing that bucket lands the turn
on an unrelated model which then answers with empty content — so the pipeline
advances the fallback chain and the user loses the thread of the truncated
work. These tests pin both halves of the fix: the detector, and the routing.
"""

import pytest

from brain.classifier.continuation import (
    continuation_hint,
    is_continuation_input,
    normalize_input,
)


@pytest.mark.parametrize(
    "text",
    [
        "lanjutkan...",
        "Lanjut!",
        "LANJUTKAN",
        "lanjut aja dong",
        "lanjutkan aja",
        "terusin",
        "sambung",
        "continue.",
        "continue",
        "go on",
        "keep going",
        "ok lanjut",
        "oke lanjut",
        "gas",
        "silakan lanjutkan",
    ],
)
def test_bare_continuation_prompts_are_detected(text: str) -> None:
    assert is_continuation_input(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "...",
        "lanjutkan implementasi modul auth dengan JWT",
        "lanjut tapi ganti model dulu",
        "tolong lanjutkan tapi pakai test dulu",
        "buatkan kode python sorting",
        "lanjut",
        "next step untuk refactor parser",
    ],
)
def test_instructions_that_merely_mention_continue_are_not_continuations(
    text: str,
) -> None:
    # "lanjut" alone is a continuation; anything carrying new content is a
    # fresh instruction and must keep normal routing.
    if text == "lanjut":
        assert is_continuation_input(text)
    else:
        assert not is_continuation_input(text)


def test_normalize_collapses_unicode_punctuation() -> None:
    assert normalize_input("Lanjut…") == "lanjut"
    assert normalize_input("  Lanjutkan!!  ") == "lanjutkan"


def test_continuation_hint_is_non_empty_instruction() -> None:
    hint = continuation_hint()
    assert hint.strip()
    assert "Lanjutkan" in hint


async def test_continuation_turn_reuses_previous_model(tmp_path) -> None:
    from agent.orchestrator import Orchestrator
    from brain.memory.conversation_store import ConversationStore
    from config.settings import Settings
    from model.registry.model_info import ModelInfo

    good = {"choices": [{"message": {"content": "lanjutan"}, "finish_reason": "stop"}], "usage": {}}
    calls: list[str] = []

    class Client:
        async def chat_completion(self, **kwargs):
            calls.append(kwargs["model"])
            return good

    orch = Orchestrator.from_settings(Settings(), api_client=Client())
    store = ConversationStore(storage_dir=tmp_path)
    orch._conversation_store = store
    orch._context_assembler._store = store
    for model_id in ("ag/routed", "ag/worked"):
        orch.registry.register(ModelInfo(id=model_id, display_name=model_id, provider="ag"))
    store.add_message("s", "user", "tulis modul")
    store.add_message("s", "assistant", "setengah jadi", model_used="ag/worked")

    response = await orch.process(user_input="lanjutkan...", session_id="s", stream=False)

    assert calls == ["ag/worked"]
    assert response.model_used == "ag/worked"
