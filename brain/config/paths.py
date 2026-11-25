"""Where SquidWings keeps what it writes while running, so the project root stays code only.

Everything here is private and out of git: ``data/`` holds the logs, the audit chain, and
reports. ``SQUIDWINGS_DATA_DIR`` moves it. The chat folders (``workspace/``) and the memory
vault (``vault/``) keep their place, because saved chats point at them by full path.

``tidy_root()`` runs at start-up and moves what older versions left in the root (``*.log``,
``audit.jsonl``, ``logs/``, ``reports/``) into ``data/``. It never overwrites: when the name is
taken (an older server wrote again before it was restarted), the file gets a number,
``audit.1.jsonl``, so nothing is lost and the root stays clean.
"""

from __future__ import annotations

import os
import shutil
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
