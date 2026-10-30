"""Automatic L1 vault memory bridge: prompt → memory, handled by code.

The agent must never have to remember to call ``vault_boot``. This module
builds the L1 context once per session and returns text that is ready to
append to the system prompt.

Design rules:

* **Never raises.** A broken, missing, or unreadable vault degrades to "no
  memory", never to "no session". Every failure path returns ``""``.
* **Cached per vault mtime.** The L1 block is rebuilt only when the vault
  actually changed, so injecting it every turn costs nothing.
* **Same vault resolution as ``vault_tool``.** One source of truth for
  "where is the vault", so the prompt and the tools can never disagree.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from config.settings import get_settings
from tool.execute.execute_local_memory.vault import boot as B
from tool.execute.execute_local_memory.vault import constants as C
from utils.logger import get_logger
from utils.workspace_context import get_active_workspace

logger = get_logger(__name__)

_CACHE: dict[str, tuple[int, str]] = {}

# Each chat's own vault, inside its folder. Hidden so it is not treated as code.
SESSION_VAULT_DIR = ".vault"

HEADER = "\n\n[MEMORY — auto-loaded, do not ask the user for this]\n"
FOOTER = (
    "\n[END MEMORY]\n"
    "Use vault_search / vault_read / vault_recall for anything deeper."
)


def vault_path(project_root: str | Path | None = None) -> Path:
    """The vault for this moment: the current session's, or else the global one.

    Resolved exactly like ``vault_tool._vault``.
    """
    active = get_active_workspace()
    if project_root is None and active:
        return Path(active) / SESSION_VAULT_DIR
    return global_vault_path(project_root)


def global_vault_path(project_root: str | Path | None = None) -> Path:
    """The vault shared by every session: your profile, playbooks, and own notes."""
    try:
        settings = get_settings()
    except Exception:
        settings = None

    configured = ""
    if settings is not None:
        configured = (getattr(settings, "obsidian_vault_path", "") or "").strip()
    if configured:
        return Path(configured).expanduser()

    if project_root is not None:
        root = Path(project_root).expanduser()
    elif settings is not None:
        root = Path(settings.project_root).expanduser()
    else:
        root = Path.cwd()
    return root / "vault"


def _note_mtimes(vault: Path) -> list[int]:
    """mtime_ns of every note, pruning excluded folders while walking.

    ``os.walk`` with in-place ``dirs[:]`` pruning rather than ``rglob``: if the
    vault is ever pointed at a project root by mistake, ``rglob`` would descend
    into ``.venv`` and stat thousands of irrelevant files on every turn.
    """
    excluded = {e.lower() for e in C.DEFAULT_EXCLUDED_FOLDERS}
    stamps: list[int] = []
    try:
        for root, dirs, files in os.walk(vault):
            dirs[:] = [d for d in dirs if d.lower() not in excluded]
            for name in files:
                if not name.endswith(".md"):
                    continue
                try:
                    stamps.append((Path(root) / name).stat().st_mtime_ns)
                except OSError:
                    continue
    except OSError:
        pass
    return stamps


def _stamp(vault: Path) -> int:
    """Change signal: newest mtime_ns across the vault, its notes, and the index.

    Three sources must be watched, because each moves independently:

    * **Folders** — creating, deleting, or renaming a note bumps the parent
      folder mtime.
    * **Note files** — editing a note in place (Obsidian, an editor, a
      ``git pull``) bumps only the file's own mtime. The parent folder does
      **not** move, so watching folders alone served stale memory after every
      external edit — the single most common way a vault changes.
    * **The index** — ``vault_write`` refreshes it, the cheapest signal that
      the tool itself changed something.

    ``mtime_ns`` rather than ``st_mtime``: two writes inside the same
    filesystem timestamp tick must still be distinguishable.

    Cost is one ``stat`` per note per turn (~1-3 ms for a thousand notes),
    which is far cheaper than rebuilding the index, graph, and render.

    Never raises — an unreadable vault simply reports "unchanged".
    """
    index = C.index_dir(vault)
    candidates = [vault, index, index / C.INDEX_FILENAME]
    with contextlib.suppress(OSError):
        candidates.extend(p for p in vault.iterdir() if p.is_dir())

    newest = 0
    for path in candidates:
        try:
            newest = max(newest, path.stat().st_mtime_ns)
        except OSError:
            continue
    for stamp in _note_mtimes(vault):
        newest = max(newest, stamp)
    return newest


def build_global_context(*, token_budget: int = B.DEFAULT_TOKEN_BUDGET) -> str:
    """L1 of the shared vault, shown alongside a session's own. Never raises."""
    if not get_active_workspace():
        return ""
    return build_context(global_vault_path(), token_budget=token_budget, _vault=global_vault_path())


def build_context(
    project_root: str | Path | None = None,
    *,
    token_budget: int = B.DEFAULT_TOKEN_BUDGET,
    refresh: bool = False,
    _vault: Path | None = None,
) -> str:
    """Return the L1 block for the system prompt. Never raises."""
    # Contract: every failure path returns "". If settings cannot be read we
    # cannot prove which vault the user meant, and vault_path() would silently
    # fall back to Path.cwd() — which, when the process happens to run from a
    # project root, injects *that* project's vault into an unrelated session.
    # Guessing is worse than staying silent, so bail out here.
    if project_root is None:
        try:
            get_settings()
        except Exception as exc:  # noqa: BLE001 - degrade, never break the session
            logger.warning("[vault_context] settings unavailable, no memory: %s", exc)
            return ""

    vault = _vault or vault_path(project_root)
    key = str(vault)
    stamp = _stamp(vault)

    if not refresh:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]

    text = ""
    try:
        C.ensure_layout(vault)
        body = (B.text_for_prompt(vault, token_budget=token_budget) or "").strip()
        # An empty vault renders as "[BOOT CONTEXT]\n[/BOOT CONTEXT]" — that is
        # wrapper noise, not memory. Inject only when something real is inside.
        inner = body
        for marker in ("[BOOT CONTEXT]", "[/BOOT CONTEXT]"):
            inner = inner.replace(marker, "")
        if inner.strip():
            text = HEADER + body + FOOTER
    except Exception as exc:  # noqa: BLE001 - degrade, never break the session
        logger.warning("[vault_context] L1 unavailable: %s", exc)
        text = ""

    _CACHE[key] = (stamp, text)
    return text


def clear_cache() -> None:
    """Drop the cache. Call after a write that bypassed ``vault_tool``."""
    _CACHE.clear()


def stats() -> dict[str, Any]:
    """Diagnostics for the boot log line."""
    return {
        "cached_vaults": len(_CACHE),
        "chars": sum(len(v[1]) for v in _CACHE.values()),
    }


__all__ = ["build_context", "build_global_context", "global_vault_path", "vault_path", "clear_cache", "stats"]
