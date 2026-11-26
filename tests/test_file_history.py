"""What the AI changed in a chat's folder, turn by turn: found by scanning, shown as diffs, put back on request."""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.utils.file_history import FileHistory, line_stats, scan


def write(root: Path, relative: str, text: str | bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def test_a_turn_records_what_appeared_changed_and_went(tmp_path: Path) -> None:
    write(tmp_path, "app.py", "print('a')\n")
    write(tmp_path, "old.txt", "bye\n")
    history = FileHistory(tmp_path)
    started = history.begin()
    write(tmp_path, "app.py", "print('a')\nprint('b')\n")
    write(tmp_path, "src/new.js", "export const x = 1;\n")
    (tmp_path / "old.txt").unlink()
    turn = history.end(started, "tambah fitur", "sess-1")
    assert turn is not None
    found = {c.path: (c.status, c.added, c.removed) for c in turn.changes}
    assert found == {"app.py": ("modified", 1, 0), "src/new.js": ("added", 1, 0), "old.txt": ("deleted", 0, 1)}
    assert turn.public()["added"] == 2 and turn.public()["removed"] == 1
    assert history.turns()[0].prompt == "tambah fitur"
    assert history.content(turn.changes[0].before) == b"print('a')\n"


def test_nothing_changed_means_no_turn_and_hand_edits_are_not_blamed_on_the_ai(tmp_path: Path) -> None:
    write(tmp_path, "a.txt", "1\n")
    history = FileHistory(tmp_path)
    assert history.end(history.begin()) is None
    write(tmp_path, "a.txt", "edited by hand\n")       # between turns
    started = history.begin()
    assert history.end(started) is None
    assert history.turns() == []


def test_caches_and_dependencies_are_not_the_work(tmp_path: Path) -> None:
    write(tmp_path, "node_modules/x/index.js", "x")
    write(tmp_path, ".git/HEAD", "ref")
    write(tmp_path, "__pycache__/a.pyc", b"\0\1")
    write(tmp_path, ".session-id", "sess-1")
    write(tmp_path, "pkg.egg-info/PKG-INFO", "x")
    write(tmp_path, "main.py", "x = 1\n")
    assert set(scan(tmp_path)) == {"main.py"}


def test_binary_files_are_tracked_but_not_stored(tmp_path: Path) -> None:
    history = FileHistory(tmp_path)
    started = history.begin()
    write(tmp_path, "logo.png", b"\x89PNG\0\0data")
    turn = history.end(started)
    assert turn is not None and turn.changes[0].binary and turn.changes[0].after is None


def test_a_turn_or_one_file_can_be_put_back(tmp_path: Path) -> None:
    write(tmp_path, "a.txt", "one\n")
    write(tmp_path, "b.txt", "two\n")
    history = FileHistory(tmp_path)
    started = history.begin()
    write(tmp_path, "a.txt", "ONE\n")
    write(tmp_path, "b.txt", "TWO\n")
    write(tmp_path, "c.txt", "new\n")
    turn = history.end(started, "ubah semua")
    assert turn is not None
    undo = history.restore(turn.id, "a.txt")
    assert (tmp_path / "a.txt").read_text() == "one\n" and (tmp_path / "b.txt").read_text() == "TWO\n"
    assert undo is not None and undo.by == "restore"
    history.restore(turn.id)
    assert (tmp_path / "b.txt").read_text() == "two\n" and not (tmp_path / "c.txt").exists()
    assert [t.by for t in history.turns()] == ["ai", "restore", "restore"]
    with pytest.raises(KeyError):
        history.restore("nope")


def test_a_binary_change_cannot_be_put_back(tmp_path: Path) -> None:
    write(tmp_path, "logo.png", b"\x89PNG\0old")
    history = FileHistory(tmp_path)
    started = history.begin()
    write(tmp_path, "logo.png", b"\x89PNG\0new!")
    turn = history.end(started)
    assert turn is not None
    with pytest.raises(ValueError):
        history.restore(turn.id)


def test_line_stats() -> None:
    assert line_stats(b"a\nb\n", b"a\nc\nd\n") == (2, 1)
    assert line_stats(None, b"x\n") == (1, 0)


def test_a_forged_history_line_never_reaches_a_page_unchecked(tmp_path: Path) -> None:
    history = FileHistory(tmp_path)
    history.dir.mkdir(parents=True)
    history.turns_path.write_text(
        '{"id": "x\\"><img src=x onerror=alert(1)>", "started": 1, "ended": 2, "changes": [{"path": "a", "status": "added"}]}\n'
        '{"id": "ok1", "started": 1, "ended": 2, "by": "<b>", "changes": [{"path": "a", "status": "<script>", "added": 1}, '
        '{"path": "b", "status": "modified", "added": "3", "before": "../../etc"}]}\n'
        "not json\n",
        encoding="utf-8",
    )
    [turn] = history.turns()
    assert turn.id == "ok1" and turn.by == "ai"
    assert [(c.path, c.status, c.added, c.before) for c in turn.changes] == [("b", "modified", 3, None)]
