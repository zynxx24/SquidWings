"""Runtime data moves out of the checkout and leaves a link, so old full paths keep working."""

from __future__ import annotations

from pathlib import Path

from brain.config import paths


def make_repo(root: Path) -> None:
    (root / "workspace" / "sessions" / "a").mkdir(parents=True)
    (root / "workspace" / "sessions" / "a" / "note.txt").write_text("hi", encoding="utf-8")
    (root / "web" / "data").mkdir(parents=True)
    (root / "web" / "data" / "credentials.json").write_text("{}", encoding="utf-8")


def test_folders_move_and_old_paths_still_work(tmp_path: Path) -> None:
    root, target = tmp_path / "repo", tmp_path / "home" / "squidwings"
    make_repo(root)
    report = paths.move_out_of_repo(root, target)
    assert any(line.startswith("workspace: moved") for line in report)
    assert (root / "workspace").is_symlink() and (target / "workspace" / "sessions" / "a" / "note.txt").is_file()
    assert (root / "workspace" / "sessions" / "a" / "note.txt").read_text(encoding="utf-8") == "hi"
    assert (root / "web" / "data").is_symlink() and (target / "web" / "data" / "credentials.json").is_file()
    assert paths.move_out_of_repo(root, target) == [], "running it again changes nothing"


def test_a_fresh_clone_gets_its_links_back(tmp_path: Path) -> None:
    root, target = tmp_path / "repo", tmp_path / "data"
    make_repo(root)
    paths.move_out_of_repo(root, target)
    fresh = tmp_path / "clone"
    fresh.mkdir()
    report = paths.move_out_of_repo(fresh, target)
    assert "workspace: linked" in report[0] or any("workspace: linked" in r for r in report)
    assert (fresh / "workspace" / "sessions" / "a" / "note.txt").read_text(encoding="utf-8") == "hi"


def test_nothing_is_overwritten_and_it_can_be_switched_off(tmp_path: Path, monkeypatch) -> None:
    root, target = tmp_path / "repo", tmp_path / "data"
    make_repo(root)
    (target / "workspace").mkdir(parents=True)
    report = paths.move_out_of_repo(root, target)
    assert any("workspace: kept in the repo" in r for r in report) and not (root / "workspace").is_symlink()
    monkeypatch.setenv("SQUIDWINGS_KEEP_DATA_IN_REPO", "1")
    other = tmp_path / "repo2"
    make_repo(other)
    assert paths.move_out_of_repo(other, tmp_path / "data2") == [] and not (other / "workspace").is_symlink()


def test_the_data_folder_follows_the_platform(monkeypatch) -> None:
    monkeypatch.setenv("SQUIDWINGS_HOME", "/srv/sw")
    assert paths.user_data_root() == Path("/srv/sw")
    monkeypatch.delenv("SQUIDWINGS_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", "/x/share")
    if paths.sys.platform.startswith("linux"):
        assert paths.user_data_root() == Path("/x/share/squidwings")


def test_every_moved_folder_and_its_link_stays_out_of_git() -> None:
    import shutil
    import subprocess

    import pytest

    if shutil.which("git") is None or not (paths.ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for rel in paths.MOVABLE:
        done = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=paths.ROOT, check=False)
        assert done.returncode == 0, f"{rel} (folder or link) must be git-ignored"
