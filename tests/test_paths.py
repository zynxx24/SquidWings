"""Runtime files live in data/, and leftovers from older versions are moved there without overwriting."""

from __future__ import annotations

from pathlib import Path

from brain.config import paths


def test_tidy_moves_root_leftovers_into_data(tmp_path: Path):
    (tmp_path / "audit.jsonl").write_text("chain")
    (tmp_path / "tunnel-trading.log").write_text("t")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "web-server.out").write_text("w")
    (tmp_path / "reports" / "trading").mkdir(parents=True)
    (tmp_path / "reports" / "trading" / "r.md").write_text("r")
    moved = paths.tidy_root(tmp_path)
    data = tmp_path / "data"
    assert (data / "audit.jsonl").read_text() == "chain"
    assert (data / "logs" / "tunnel-trading.log").exists() and (data / "logs" / "web-server.out").exists()
    assert (data / "reports" / "trading" / "r.md").exists()
    assert not (tmp_path / "logs").exists() and not (tmp_path / "audit.jsonl").exists()
    assert moved


def test_tidy_never_overwrites(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "audit.jsonl").write_text("new chain")
    (tmp_path / "audit.jsonl").write_text("old chain")
    paths.tidy_root(tmp_path)
    assert (tmp_path / "data" / "audit.jsonl").read_text() == "new chain"
    assert (tmp_path / "data" / "audit.1.jsonl").read_text() == "old chain", "kept under a number, not lost"
    assert not (tmp_path / "audit.jsonl").exists()


def test_the_data_folder_can_move(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SQUIDWINGS_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.audit_log() == tmp_path / "elsewhere" / "audit.jsonl"
    assert paths.log_file("x.log").parent.is_dir()
