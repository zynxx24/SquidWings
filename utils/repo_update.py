"""Keeps this checkout on the newest commit of its GitHub branch.

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

_started = threading.Lock()
_started_once = False


def _env(name: str, default: str) -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover - python-dotenv is a requirement
        pass
    return os.getenv(name, default).strip().lower()


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
        return _update(root)
    except (OSError, subprocess.SubprocessError) as exc:
        return Result("skipped", f"update check failed: {exc}")


def _update(root: Path) -> Result:
    if shutil.which("git") is None or not (root / ".git").exists():
        return Result("skipped", "not a git checkout")
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
