"""Keeps this checkout on the newest commit of its GitHub branch.

A folder downloaded as a ZIP (GitHub's "Download ZIP") has no git history. On its first start it is
turned into a checkout of ``UPDATE_REPO`` (``UPDATE_BRANCH``): the commit the ZIP was made from is
found by comparing the files, so anything the user changed since counts as their own edit and is
kept, exactly as in a clone. From then on it updates like a clone, and nobody downloads again.

At start, ``main.py`` fetches the branch's upstream and, when GitHub has newer commits, moves
to them with a fast-forward only, then restarts itself so the new code runs. While the web
server or the chat runs, a background thread checks again every ``AUTO_UPDATE_EVERY_MIN``
minutes and pulls what is new; that code is used from the next start.

It never loses work:

* only a fast-forward: with local commits that are not on GitHub, nothing is pulled;
* uncommitted edits stay; when an incoming commit touches the same file, git refuses and
  nothing changes;
* ignored files (trading, .env, workspace) are never touched.

When ``requirements.txt`` changed, the new packages are installed into the running Python.

``AUTO_UPDATE=false`` (or ``python main.py --no-update ...``) turns it off. Tests turn it off.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RESTARTED_ENV = "SQUIDWINGS_JUST_UPDATED"
_FETCH_TIMEOUT = 25
_PIP_TIMEOUT = 900
_DEP_FILES = ("requirements.txt", "requirements.lock")
DEFAULT_REPO = "https://github.com/zynxx24/SquidWings.git"
DEFAULT_BRANCH = "main"
# How far back to look for the commit a ZIP was made from, and how much of it must match.
_ADOPT_SEARCH = 300
_ADOPT_MIN_MATCH = 0.5
_ADOPT_FETCH_TIMEOUT = 300

_started = threading.Lock()
_started_once = False
# The newest result, for the web page: it shows that an update was pulled and needs a restart.
_last: dict[str, object] = {}
_last_lock = threading.Lock()


def _env(name: str, default: str, *, lower: bool = True) -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover - python-dotenv is a requirement
        pass
    value = os.getenv(name, default).strip() or default
    return value.lower() if lower else value


def enabled() -> bool:
    return _env("AUTO_UPDATE", "true") not in ("false", "0", "off", "no")


def interval_s() -> int:
    try:
        return max(0, int(float(_env("AUTO_UPDATE_EVERY_MIN", "30")) * 60))
    except ValueError:
        return 30 * 60


@dataclass
class Result:
    status: str  # updated | current | skipped
    message: str
    old: str = ""
    new: str = ""
    changed: list[str] = field(default_factory=list)

    @property
    def updated(self) -> bool:
        return self.status == "updated"


def _git(root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout, check=False, env=env,
    )


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.lower().startswith("hint:"):
            return line[:200]
    return "unknown error"


def _install_deps(root: Path) -> str:
    req = root / "requirements.txt"
    if not req.exists() or _env("AUTO_UPDATE_DEPS", "true") in ("false", "0", "off", "no"):
        return ""
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
            cwd=root, capture_output=True, text=True, timeout=_PIP_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"; packages not installed ({exc})"
    if done.returncode != 0:
        return f"; packages not installed ({_first_line(done.stderr)})"
    return "; packages installed"


def update(root: Path = ROOT) -> Result:
    """Fetch and fast-forward to the upstream branch. Never raises."""
    try:
        result = _update(root)
    except (OSError, subprocess.SubprocessError) as exc:
        result = Result("skipped", f"update check failed: {exc}")
    _remember(result)
    return result


def _remember(result: Result) -> None:
    with _last_lock:
        restart = bool(_last.get("restart_needed")) or result.updated
        _last.update(status=result.status, message=result.message, at=time.time(), restart_needed=restart)
        if result.updated:
            _last["new"] = result.new


def status() -> dict[str, object]:
    """The newest check, for the web page. ``restart_needed`` stays on until the process restarts."""
    with _last_lock:
        return {"enabled": enabled(), **_last}


def _blob_sha(path: Path) -> str | None:
    """The id git gives a file's content, so a file can be matched to a commit without git reading it."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _tree(root: Path, commit: str) -> dict[str, str]:
    """path -> blob id of the files in a commit."""
    listed = _git(root, "ls-tree", "-r", commit)
    files: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            files[path] = parts[2]
    return files


def _base_commit(root: Path, ref: str) -> tuple[str, float]:
    """The commit whose files this folder matches best, and the share of them that match."""
    commits = _git(root, "rev-list", f"--max-count={_ADOPT_SEARCH}", ref).stdout.split()
    hashes: dict[str, str | None] = {}
    best, best_score, best_share = "", -1, 0.0
    for commit in commits:     # newest first: on a tie the newer commit wins
        tree = _tree(root, commit)
        if not tree:
            continue
        score = 0
        for path, blob in tree.items():
            if path not in hashes:
                hashes[path] = _blob_sha(root / path)
            score += hashes[path] == blob
        if score > best_score:
            best, best_score, best_share = commit, score, score / len(tree)
    return best, best_share


def _adopt(root: Path) -> Result | None:
    """Make a downloaded ZIP folder a git checkout of the GitHub branch. None when that worked.

    The folder's files are left exactly as they are; only a ``.git`` folder is added. On any failure it
    is removed again, so the folder is as it was.
    """
    if shutil.which("git") is None:
        return Result("skipped", "git is not installed; install git to get updates without downloading again")
    repo = _env("UPDATE_REPO", DEFAULT_REPO, lower=False)
    branch = _env("UPDATE_BRANCH", DEFAULT_BRANCH, lower=False)
    remote_ref = f"origin/{branch}"
    try:
        steps = (
            ("init", "-q"),
            ("config", "core.fileMode", "false"),     # a ZIP does not keep the executable bit
            ("remote", "add", "origin", repo),
        )
        for step in steps:
            done = _git(root, *step)
            if done.returncode != 0:
                raise _AdoptError(_first_line(done.stderr))
        fetched = _git(root, "fetch", "--quiet", "origin", branch, timeout=_ADOPT_FETCH_TIMEOUT)
        if fetched.returncode != 0:
            raise _AdoptError(f"could not reach GitHub ({_first_line(fetched.stderr)})")
        _git(root, "update-ref", f"refs/remotes/{remote_ref}", "FETCH_HEAD")
        base, share = _base_commit(root, remote_ref)
        if not base or share < _ADOPT_MIN_MATCH:
            raise _AdoptError(f"this folder does not look like a download of {repo}")
        for step in (
            ("symbolic-ref", "HEAD", f"refs/heads/{branch}"),
            ("reset", "-q", "--mixed", base),          # history and index move; the files do not
            ("branch", "-q", f"--set-upstream-to={remote_ref}"),
        ):
            done = _git(root, *step)
            if done.returncode != 0:
                raise _AdoptError(_first_line(done.stderr))
    except (_AdoptError, OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(root / ".git", ignore_errors=True)
        return Result("skipped", f"downloaded folder not linked to GitHub: {exc}")
    logger.info(f"[update] downloaded folder linked to {repo} ({branch}) at {base[:7]}")
    return None


class _AdoptError(Exception):
    pass


def _update(root: Path) -> Result:
    if not (root / ".git").exists():
        adopted = _adopt(root)
        if adopted is not None:
            return adopted
    if shutil.which("git") is None:
        return Result("skipped", "git is not installed")
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        return Result("skipped", "this branch has no upstream on GitHub")
    up = upstream.stdout.strip()
    fetched = _git(root, "fetch", "--quiet", timeout=_FETCH_TIMEOUT)
    if fetched.returncode != 0:
        return Result("skipped", f"could not reach GitHub ({_first_line(fetched.stderr)})")
    counts = _git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if counts.returncode != 0:
        return Result("skipped", _first_line(counts.stderr))
    behind, ahead = (int(x) for x in counts.stdout.split())
    if behind == 0:
        return Result("current", f"up to date with {up}")
    if ahead > 0:
        return Result(
            "skipped",
            f"{up} has {behind} new commit(s) but this checkout has {ahead} unpushed; pull by hand",
        )
    old = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    merged = _git(root, "merge", "--ff-only", "--quiet", "@{u}", timeout=120)
    if merged.returncode != 0:
        return Result("skipped", f"{behind} new commit(s) not pulled: {_first_line(merged.stderr or merged.stdout)}")
    new = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    changed = [f for f in _git(root, "diff", "--name-only", old, new).stdout.splitlines() if f]
    note = _install_deps(root) if any(f in _DEP_FILES for f in changed) else ""
    return Result("updated", f"updated {old} → {new} ({behind} commit(s), {len(changed)} file(s)){note}",
                  old, new, changed)


def update_at_start(notify: Callable[[str], None], root: Path = ROOT) -> None:
    """Pull at start and restart this process on the new code. Runs once per launch."""
    if not enabled() or os.environ.get(RESTARTED_ENV):
        return
    result = update(root)
    logger.info(f"[update] {result.message}")
    if result.status == "skipped":
        notify(f"[dim]Auto-update: {result.message}[/dim]")
    if not result.updated:
        return
    notify(f"[bold green]⟳ Auto-update: {result.message}. Restarting on the new code…[/bold green]")
    os.environ[RESTARTED_ENV] = "1"
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])


def start_background(notify: Callable[[str], None], root: Path = ROOT) -> bool:
    """Check again every AUTO_UPDATE_EVERY_MIN minutes while the app runs. Once per process."""
    global _started_once
    every = interval_s()
    if not enabled() or every <= 0:
        return False
    with _started:
        if _started_once:
            return False
        _started_once = True

    def loop() -> None:
        while True:
            time.sleep(every)
            result = update(root)
            if result.updated:
                logger.warning(f"[update] {result.message}; restart to use it")
                notify(f"[bold green]⟳ Update pulled: {result.message}. Restart to use it.[/bold green]")
            elif result.status == "skipped":
                logger.info(f"[update] {result.message}")

    threading.Thread(target=loop, name="repo-auto-update", daemon=True).start()
    return True
