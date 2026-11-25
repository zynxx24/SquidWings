"""Learning across chats: recall of earlier exchanges and standing preferences."""

from __future__ import annotations

from dataclasses import dataclass

from brain.memory import chat_recall


@dataclass
class _Msg:
    role: str
    content: str
    timestamp: float = 1_790_000_000.0
    failed: bool = False


class _Store:
    def __init__(self, chats: dict[str, list[_Msg]]) -> None:
        self.chats = chats

    def list_sessions(self) -> list[dict]:
        return [{"session_id": sid, "name": f"chat {sid}", "message_count": len(m)} for sid, m in self.chats.items()]

    def get_history(self, sid: str) -> list[_Msg]:
        return self.chats[sid]


def _store() -> _Store:
    return _Store({
        "a": [_Msg("user", "cara install laravel 11 di ubuntu, composer error ext-xml"),
              _Msg("assistant", "Pasang ekstensi PHP dulu: sudo apt install php8.3-xml php8.3-mbstring, lalu composer install.")],
        "b": [_Msg("user", "resep nasi goreng kampung yang enak"), _Msg("assistant", "Bawang merah, cabai, terasi...")],
        "now": [_Msg("user", "laravel composer error ext-xml lagi"), _Msg("assistant", "...")],
    })


def test_a_similar_exchange_from_another_chat_is_recalled() -> None:
    chat_recall.reset()
    text = chat_recall.recall(_store(), "composer install laravel gagal ext-xml missing", exclude_session="now")
    assert "DARI CHAT SEBELUMNYA" in text and "php8.3-xml" in text and "nasi goreng" not in text
    # The current chat is never recalled into itself.
    assert "ext-xml lagi" not in text


def test_unrelated_or_short_requests_recall_nothing() -> None:
    chat_recall.reset()
    assert chat_recall.recall(_store(), "jadwal kereta bandung jakarta besok", exclude_session="now") == ""
    assert chat_recall.recall(_store(), "halo", exclude_session="now") == ""


def test_a_broken_store_never_breaks_the_turn() -> None:
    chat_recall.reset()

    class Broken:
        def list_sessions(self) -> list[dict]:
            raise OSError("disk gone")

    assert chat_recall.recall(Broken(), "cara install laravel di ubuntu") == ""


def test_standing_preferences_are_saved_but_ordinary_sentences_are_not() -> None:
    from tools.local_memory.user_profile import _preference

    assert _preference("mulai sekarang jawab pakai bahasa santai ya") == "mulai sekarang jawab pakai bahasa santai ya"
    assert _preference("tolong jangan pakai bullet point lagi. Buat ringkasan bab 3") == "tolong jangan pakai bullet point lagi"
    for text in ("selalu error pas run laravel", "jangan dihapus filenya", "kenapa kamu selalu jawab panjang?"):
        assert _preference(text) == "", text
