"""Auto-update: fast-forward only, never loses local work. Uses real throwaway git repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from utils import repo_update

pytestmark = pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
                                reason="git not installed")


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return done.stdout.strip()


def commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", f"edit {name}")


@pytest.fixture()
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """(local checkout, another clone that pushes to the same origin)."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    local = tmp_path / "local"
    git(tmp_path, "clone", "-q", str(origin), str(local))
    commit(local, "app.py", "v1\n")
    git(local, "push", "-q", "-u", "origin", "HEAD:main")
    git(local, "branch", "-q", "--set-upstream-to=origin/main")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    return local, other


def test_nothing_new_is_current(repos):
    local, _ = repos
    assert repo_update.update(local).status == "current"


def test_new_commits_are_pulled(repos):
    local, other = repos
    commit(other, "app.py", "v2\n")
    git(other, "push", "-q")
    result = repo_update.update(local)
    assert result.updated and result.changed == ["app.py"]
    assert (local / "app.py").read_text() == "v2\n"


def test_uncommitted_edits_elsewhere_stay(repos):
    local, other = repos
    commit(local, "notes.txt", "a\n")
    git(local, "push", "-q")
    git(other, "pull", "-q")
    (local / "notes.txt").write_text("my unsaved edit\n")
    commit(other, "app.py", "v2\n")
    git(other, "push", "-q")
    assert repo_update.update(local).updated
    assert (local / "notes.txt").read_text() == "my unsaved edit\n"


def test_a_clashing_edit_stops_the_pull_and_is_kept(repos):
    local, other = repos
    (local / "app.py").write_text("my unsaved edit\n")
    commit(other, "app.py", "v2\n")
    git(other, "push", "-q")
    result = repo_update.update(local)
    assert result.status == "skipped"
    assert (local / "app.py").read_text() == "my unsaved edit\n"


def test_unpushed_local_commits_stop_the_pull(repos):
    local, other = repos
    commit(local, "mine.py", "x\n")
    commit(other, "app.py", "v2\n")
    git(other, "push", "-q")
    result = repo_update.update(local)
    assert result.status == "skipped" and "unpushed" in result.message
    assert (local / "app.py").read_text() == "v1\n"


def test_not_a_checkout_is_skipped(tmp_path):
    assert repo_update.update(tmp_path).status == "skipped"
    assert not (tmp_path / ".git").exists()      # the failed link leaves the folder as it was


# ── a folder downloaded as a ZIP, with no .git ────────────────────────────────


def zip_of(repo: Path, ref: str, into: Path) -> Path:
    """What GitHub's "Download ZIP" gives: the files of one commit, no history."""
    into.mkdir()
    archive = subprocess.run(["git", "archive", ref], cwd=repo, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x"], cwd=into, input=archive, check=True)
    return into


@pytest.fixture()
def downloaded(repos, tmp_path, monkeypatch):
    """(a ZIP of the first release, the clone that keeps publishing). Two more releases follow it."""
    local, other = repos
    git(other, "pull", "-q")
    commit(other, "notes.txt", "n1\n")
    git(other, "push", "-q")
    folder = zip_of(other, "HEAD", tmp_path / "SquidWings-main")
    commit(other, "app.py", "v2\n")
    commit(other, "new_tool.py", "t\n")
    git(other, "push", "-q")
    monkeypatch.setenv("UPDATE_REPO", str(tmp_path / "origin.git"))
    monkeypatch.setenv("UPDATE_BRANCH", "main")
    return folder, other


def test_a_downloaded_zip_updates_without_downloading_again(downloaded):
    folder, other = downloaded
    (folder / "notes.txt").write_text("my own note\n")       # the user's edit to a file nobody else changed
    (folder / "data.csv").write_text("mine\n")               # and a file of their own
    result = repo_update.update(folder)
    assert result.updated and set(result.changed) == {"app.py", "new_tool.py"}
    assert (folder / "app.py").read_text() == "v2\n" and (folder / "new_tool.py").exists()
    assert (folder / "notes.txt").read_text() == "my own note\n"
    assert (folder / "data.csv").read_text() == "mine\n"
    assert git(folder, "rev-parse", "HEAD") == git(other, "rev-parse", "HEAD")
    commit(other, "app.py", "v3\n")                           # from now on it updates like a clone
    git(other, "push", "-q")
    assert repo_update.update(folder).updated and (folder / "app.py").read_text() == "v3\n"


def test_a_downloaded_zip_keeps_an_edit_that_clashes(downloaded):
    folder, _ = downloaded
    (folder / "app.py").write_text("my edit\n")
    result = repo_update.update(folder)
    assert result.status == "skipped"
    assert (folder / "app.py").read_text() == "my edit\n"


def test_an_unrelated_folder_is_not_linked(tmp_path, repos, monkeypatch):
    monkeypatch.setenv("UPDATE_REPO", str(tmp_path / "origin.git"))
    folder = tmp_path / "something-else"
    folder.mkdir()
    (folder / "app.py").write_text("not this project\n")
    result = repo_update.update(folder)
    assert result.status == "skipped" and "does not look like" in result.message
    assert not (folder / ".git").exists()


def test_status_remembers_a_pulled_update_until_restart(repos):
    local, other = repos
    repo_update._last.clear()
    repo_update.update(local)
    assert repo_update.status()["restart_needed"] is False
    commit(other, "app.py", "v2\n")
    git(other, "push", "-q")
    repo_update.update(local)
    repo_update.update(local)                                 # a later "nothing new" does not hide it
    assert repo_update.status()["restart_needed"] is True


def test_off_in_tests_so_start_never_restarts(monkeypatch):
    called = []
    monkeypatch.setattr(repo_update, "update", lambda root=None: called.append(1))
    repo_update.update_at_start(lambda _m: None)
    assert not repo_update.enabled() and called == []
