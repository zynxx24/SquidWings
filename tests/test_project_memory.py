"""Project memory: concurrent writers, prompt content, and resuming cut-off output."""

from __future__ import annotations

from pathlib import Path

from brain.memory.project_memory import ProjectMemory


def test_empty_project_leaves_the_message_untouched(tmp_path: Path) -> None:
    assert ProjectMemory(tmp_path).build_input("halo", False) == "halo"


def test_two_instances_do_not_erase_each_others_facts(tmp_path: Path) -> None:
    """Regression: a tool saving mid-turn was overwritten by the turn's stale copy."""
    turn = ProjectMemory(tmp_path)
    assert turn.state["facts"] == []
    assert ProjectMemory(tmp_path).remember("fact", "database memakai postgres")
    turn.finish_turn("selesai", "m", [], truncated=False)
    assert [f["fact"] for f in ProjectMemory(tmp_path).state["facts"]] == ["database memakai postgres"]


def test_new_requests_are_not_told_to_continue_the_last_answer(tmp_path: Path) -> None:
    """Regression: every turn carried an order to continue the previous output."""
    (tmp_path / "app.py").write_text("x = 1\n")
    memory = ProjectMemory(tmp_path)
    memory.finish_turn("JAWABAN_LAMA panjang", "m", [], truncated=False)
    text = memory.build_input("pertanyaan baru", is_continue=False)
    assert "JAWABAN_LAMA" not in text
    assert "pertanyaan baru" in text


def test_continue_uses_the_last_answer_when_nothing_was_cut_off(tmp_path: Path) -> None:
    memory = ProjectMemory(tmp_path)
    memory.finish_turn("JAWABAN_LAMA", "m", [], truncated=False)
    assert "JAWABAN_LAMA" in memory.build_input("lanjut", is_continue=True)


def test_cut_off_code_resumes_inside_the_block(tmp_path: Path) -> None:
    memory = ProjectMemory(tmp_path)
    memory.finish_turn("### File: app.py\n```python\nclass Demo:\n    def run(self):\n", "m", [], truncated=True)
    assert memory.has_cutoff()
    text = memory.build_input("lanjut", is_continue=True)
    assert "File: app.py" in text and "def run" in text and "Do not open a new fence" in text
    memory.finish_turn("selesai", "m", [], truncated=False)
    assert not memory.has_cutoff()


def test_cut_off_prose_resumes_from_the_tail(tmp_path: Path) -> None:
    memory = ProjectMemory(tmp_path)
    memory.finish_turn("kalimat yang terpotong di teng", "m", [], truncated=True)
    text = memory.build_input("lanjut", is_continue=True)
    assert "terpotong di teng" in text and "code block" not in text


def test_short_file_names_are_not_matched_by_ordinary_letters(tmp_path: Path) -> None:
    assert not ProjectMemory._mentioned("a.py", "buat apa ini")
    assert ProjectMemory._mentioned("a.py", "baca a.py dong")
    assert ProjectMemory._mentioned("auth/login.py", "perbaiki login")
    assert not ProjectMemory._mentioned("auth/login.py", "perbaiki loginnya")


def test_near_duplicates_refresh_instead_of_piling_up(tmp_path: Path) -> None:
    memory = ProjectMemory(tmp_path)
    assert memory.remember("decision", "pakai postgres untuk database utama") is True
    assert memory.remember("decision", "pakai postgres untuk database utama") is False
    assert len(memory.state["decisions"]) == 1
    assert memory.remember("nonsense", "x") is None


def test_answers_can_record_decisions_and_edits(tmp_path: Path) -> None:
    memory = ProjectMemory(tmp_path)
    memory.finish_turn("Keputusan: pakai Redis\nFakta: port 8080", "m", [tmp_path / "x.py"], truncated=False)
    state = memory.state
    assert state["decisions"][0]["what"] == "pakai Redis"
    assert state["facts"][0]["fact"] == "port 8080"
    assert "x.py" in state["files"]
    assert "pakai Redis" in memory.summary()


def test_changed_files_are_shown_once_then_only_when_named(tmp_path: Path) -> None:
    (tmp_path / "util.py").write_text("ISI_FILE = 1\n")
    memory = ProjectMemory(tmp_path)
    assert "ISI_FILE" in memory.build_input("apa kabar", False)
    assert "ISI_FILE" not in memory.build_input("apa kabar", False)
    assert "ISI_FILE" in memory.build_input("jelaskan util.py", False)


def test_existing_state_files_keep_working(tmp_path: Path) -> None:
    directory = tmp_path / ".agent_memory"
    directory.mkdir()
    (directory / "state.json").write_text(
        '{"progress":[],"decisions":[{"what":"lama","why":"","ts":1}],"files":{},'
        '"facts":[],"model_history":[],"errors":[],"turn_count":3}'
    )
    memory = ProjectMemory(tmp_path)
    assert "lama" in memory.summary()
    memory.build_input("halo", False)
    assert memory.state["turn_count"] == 4
