"""Where SquidWings keeps what it writes while running, so the project root stays code only.

Everything here is private and out of git: ``data/`` holds the logs, the audit chain, and
reports. ``SQUIDWINGS_DATA_DIR`` moves it. At start-up ``move_out_of_repo()`` moves every runtime folder
(``MOVABLE``: data, keys, chats, memory, trading history) to the user's data folder and leaves a link in
its old place, so saved chats that point at ``workspace/`` by full path keep working.

``tidy_root()`` runs at start-up and moves what older versions left in the root (``*.log``,
``audit.jsonl``, ``logs/``, ``reports/``) into ``data/``. It never overwrites: when the name is
taken (an older server wrote again before it was restarted), the file gets a number,
``audit.1.jsonl``, so nothing is lost and the root stays clean.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.getenv("SQUIDWINGS_DATA_DIR", "").strip() or ROOT / "data")


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file(name: str) -> Path:
    return log_dir() / name


def report_dir(*parts: str) -> Path:
    return data_dir() / "reports" / Path(*parts) if parts else data_dir() / "reports"


def audit_log() -> Path:
    return data_dir() / "audit.jsonl"


# What older versions wrote into the root, and where it lives now (relative to data/).
_LEGACY_FILES = {"audit.jsonl": "audit.jsonl", "trading-server.log": "logs/trading-server.log",
                 "candle_lm.log": "logs/candle_lm.log", "tunnel.log": "logs/tunnel.log"}
_LEGACY_DIRS = {"logs": "logs", "reports": "reports", "audit": "audit", "tool.orphan": "archive/tool.orphan"}


def _free(path: Path) -> Path:
    """``path``, or the first ``name.N.ext`` beside it that does not exist yet."""
    if not path.exists():
        return path
    n = 1
    while (candidate := path.with_name(f"{path.stem}.{n}{path.suffix}")).exists():
        n += 1
    return candidate


def tidy_root(root: Path | None = None) -> list[str]:
    """Move leftovers from the root into data/. Returns what moved, as 'old -> new'."""
    root = root or ROOT
    target_root = data_dir() if root == ROOT else root / "data"
    moved: list[str] = []
    pairs = [(root / old, target_root / new) for old, new in _LEGACY_FILES.items()]
    pairs += [(log, target_root / "logs" / log.name) for log in sorted(root.glob("tunnel-*.log"))]
    for src, dst in pairs:
        if src.is_file():
            dst = _free(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(f"{src.name} -> {dst.relative_to(root)}")
    for old, new in _LEGACY_DIRS.items():
        src, dst = root / old, target_root / new
        if not src.is_dir() or src.is_symlink():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            shutil.move(str(child), str(_free(dst / child.name)))
        if not any(src.iterdir()):
            src.rmdir()
            moved.append(f"{old}/ -> {dst.relative_to(root)}/")
    return moved


__all__ = ["ROOT", "audit_log", "data_dir", "log_dir", "log_file", "report_dir", "tidy_root"]


def secret_stores() -> list[Path]:
    """SquidWings' own secret files and folders: no command the agent runs may read or send them."""
    from brain.config import credentials
    from brain.model.router.store import store_dir
    from brain.utils.datastore.registry import DATA_DIR

    return [ROOT / ".env", credentials.get_store().path, store_dir(), DATA_DIR,
            ROOT / "addons" / "trading" / "data" / "bridge_secret"]


# What the app writes while it runs, relative to the project root. They move to the user's data folder and the
# old place becomes a link, so a `git clean -xdf`, a fresh clone or a deleted checkout never takes the owner's
# chats, memory, keys or trading history with it, and commands run inside the repo do not walk into them.
MOVABLE = ("data", "web/data", "vault", "workspace", "brain/memory/chat_store", "brain/memory/store", ".internal",
           "addons/trading/data")


def user_data_root() -> Path:
    """Where runtime data lives: SQUIDWINGS_HOME, else the platform's per-user data folder."""
    custom = os.getenv("SQUIDWINGS_HOME", "").strip()
    if custom:
        return Path(custom).expanduser()
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", "").strip() or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", "").strip() or Path.home() / ".local" / "share")
    return base / "squidwings"


def move_out_of_repo(root: Path | None = None, target: Path | None = None) -> list[str]:
    """Move each runtime folder out of the checkout and leave a link behind. Idempotent; never overwrites.

    A folder already moved (a link) is left alone. A fresh clone gets its links back to data moved earlier.
    Where links are not allowed (Windows without developer mode), the folder is moved back and kept in place.
    """
    root = root or ROOT
    target = target or user_data_root()
    report: list[str] = []
    if os.getenv("SQUIDWINGS_KEEP_DATA_IN_REPO", "").strip().lower() in {"1", "true", "yes"}:
        return report
    for rel in MOVABLE:
        here, there = root / rel, target / rel
        if here.is_symlink():
            continue
        if here.is_dir():
            if there.exists():
                report.append(f"{rel}: kept in the repo ({there} already exists; merge them by hand)")
                continue
            there.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(here), str(there))
            try:
                here.symlink_to(there, target_is_directory=True)
            except OSError as exc:
                shutil.move(str(there), str(here))
                report.append(f"{rel}: kept in the repo (links not allowed here: {exc})")
                continue
            report.append(f"{rel}: moved to {there}")
        elif not here.exists() and there.is_dir():
            here.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                here.symlink_to(there, target_is_directory=True)
                report.append(f"{rel}: linked to {there}")
    return report
