"""Customer chats: the per-minute allowance, the daily cap, and the short memory per chat."""

from __future__ import annotations

import json
import time
from pathlib import Path

from agent.team_chats import KEEP_SECONDS, MAX_CHATS, MAX_KEPT_TURNS, ChatLog, Limiter


def test_a_chat_gets_a_few_messages_a_minute_and_one_warning() -> None:
    limiter = Limiter(per_minute=3)
    assert [limiter.check("a", 100.0 + i) for i in range(3)] == ["ok", "ok", "ok"]
    assert limiter.check("a", 103.0) == "warn"
    assert limiter.check("a", 104.0) == "drop"                  # warned once already
    assert limiter.check("b", 104.0) == "ok"                    # another chat is not affected
    assert limiter.check("a", 161.0) == "ok"                    # a minute later the allowance is back
    assert [limiter.check("a", 170.0 + i / 10) for i in range(3)] == ["ok", "ok", "warn"]    # over again: warned again


def test_the_daily_count_is_per_team_and_starts_over_each_day() -> None:
    limiter = Limiter()
    assert limiter.day_count("t", "2026-11-12") == 0
    assert [limiter.count_day("t", "2026-11-12") for _ in range(3)] == [1, 2, 3]
    assert limiter.count_day("other", "2026-11-12") == 1
    assert limiter.count_day("t", "2026-11-13") == 1 and limiter.day_count("t", "2026-11-12") == 0


def test_the_chat_keeps_the_last_turns_and_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "chats.json"
    log = ChatLog(path)
    log.add("t", "1", "Pelanggan", "ada kaos?")
    log.add("t", "1", "Toko", "ada, M dan L")
    log.add("t", "2", "Pelanggan", "halo")
    assert log.render("t", "1", 5) == "Pelanggan: ada kaos?\nToko: ada, M dan L"
    assert log.render("t", "1", 1) == "Pelanggan: ada kaos?\nToko: ada, M dan L"      # one exchange is two lines
    assert log.render("t", "1", 0) == "" and log.render("t", "9", 3) == ""
    assert ChatLog(path).render("t", "2", 3) == "Pelanggan: halo"                      # a fresh object reads the file
    assert path.stat().st_mode & 0o777 == 0o600                                         # customers' words stay private


def test_long_texts_and_old_turns_are_cut(tmp_path: Path) -> None:
    log = ChatLog(tmp_path / "chats.json")
    log.add("t", "1", "Pelanggan", "x" * 5000)
    assert len(log.render("t", "1", 1)) < 1300
    log.add("t", "1", "Pelanggan", "   ")                                              # nothing to remember
    for i in range(MAX_KEPT_TURNS * 3):
        log.add("t", "1", "Pelanggan", f"pesan {i}")
    lines = log.render("t", "1", 100).splitlines()
    assert len(lines) == MAX_KEPT_TURNS * 2 and lines[-1] == f"Pelanggan: pesan {MAX_KEPT_TURNS * 3 - 1}"


def test_a_week_old_chat_is_forgotten_and_the_file_stays_small(tmp_path: Path) -> None:
    path = tmp_path / "chats.json"
    path.write_text(json.dumps({"t:old": {"turns": [{"who": "Pelanggan", "text": "dulu"}], "at": time.time() - KEEP_SECONDS - 10}}))
    log = ChatLog(path)
    log.add("t", "new", "Pelanggan", "baru")
    assert log.render("t", "old", 3) == "" and "t:old" not in json.loads(path.read_text())
    for i in range(MAX_CHATS + 20):
        log.add("t", f"c{i}", "Pelanggan", "hai")
    assert len(json.loads(path.read_text())) == MAX_CHATS


def test_forgetting_a_team_leaves_the_others(tmp_path: Path) -> None:
    log = ChatLog(tmp_path / "chats.json")
    log.add("a", "1", "Pelanggan", "satu")
    log.add("b", "1", "Pelanggan", "dua")
    log.forget("a")
    assert log.render("a", "1", 3) == "" and log.render("b", "1", 3) == "Pelanggan: dua"


def test_a_broken_file_is_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "chats.json"
    path.write_text("{ rusak")
    log = ChatLog(path)
    assert log.render("t", "1", 3) == ""
    log.add("t", "1", "Pelanggan", "halo")
    assert log.render("t", "1", 3) == "Pelanggan: halo"
