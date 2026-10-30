"""What the agent knows about you, across every project and session.

Project memory ends at the workspace folder, and chat history ends with the
session. Preferences and your name should not. They live in one ordinary note,
``vault/90-meta/user-profile.md``, which you can read and edit in Obsidian.

Only things you stated are saved: an explicit "ingat: ..." or "nama saya ...", or
a tool call by the model. Nothing is inferred, so nothing is saved by surprise.
Text that looks like a credential is refused.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from security.redactor import Redactor
from tool.execute.execute_local_memory import vault_context
from tool.execute.execute_local_memory.vault import constants as C
from utils.logger import get_logger

logger = get_logger(__name__)

_FILE_NAME = "user-profile.md"
_MAX_LINES = 40
_MAX_PROMPT_CHARS = 1_500
_MAX_ENTRY_CHARS = 300
_DEDUP_SIMILARITY = 0.8

_redactor = Redactor()
_lock = threading.Lock()

_REMEMBER_RE = re.compile(
    r"^\s*(?:tolong\s+|please\s+)?(?:ingat(?:lah)?|catat(?:lah)?|remember)(?:\s+ya)?"
    r"(?:\s+bahwa|\s+that|\s*:|\s)\s*(?P<text>.{3,300})$",
    re.IGNORECASE | re.DOTALL,
)
# Questions about memory start the same way: "ingat gak nama saya?"
_NOT_A_STATEMENT_RE = re.compile(r"^(?:gak|ga|nggak|enggak|tidak|kah|apa|what|when|who)\b", re.IGNORECASE)
# The name must be capitalised, or "nama saya dan file apa" would save "dan".
_NAME_RE = re.compile(
    r"(?<!\w)(?:[Nn]ama saya|[Nn]amaku|[Pp]anggil saya|[Mm]y name is|[Cc]all me)\s+(?P<name>[A-Z][\w'-]{1,30})"
)


def _path() -> Path:
    return vault_context.global_vault_path() / C.FOLDER_META / _FILE_NAME


def entries() -> list[str]:
    """Everything remembered about the user, oldest first. Never raises."""
    try:
        text = _path().read_text(encoding="utf-8")
    except OSError:
        return []
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]


def _save(lines: list[str]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- {line}" for line in lines[-_MAX_LINES:])
    today = time.strftime("%Y-%m-%d")
    content = (
        f"---\ntype: {C.TYPE_META}\nstatus: {C.STATUS_ACTIVE}\nconfidence: {C.CONFIDENCE_HIGH}\n"
        f"updated: {today}\ntags: [profile]\n---\n\n# User profile\n\n{body}\n"
    )
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    vault_context.clear_cache()


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w{3,}", text.lower()))


def _similar(a: str, b: str, threshold: float = _DEDUP_SIMILARITY) -> bool:
    left, right = _words(a), _words(b)
    return bool(left and right) and len(left & right) / len(left | right) >= threshold


def remember(text: str) -> bool:
    """Save one line. Returns False for a duplicate or anything like a credential."""
    line = " ".join(text.split())[:_MAX_ENTRY_CHARS]
    if not line or _redactor.contains_secret(line):
        return False
    with _lock:
        lines = entries()
        if any(_similar(line, existing) for existing in lines):
            return False
        # A new name replaces the old one rather than leaving two.
        if line.startswith("Nama: "):
            lines = [existing for existing in lines if not existing.startswith("Nama: ")]
        _save([*lines, line])
    return True


def forget(text: str) -> int:
    """Remove lines that contain or closely match the text. Returns how many."""
    needle = text.strip().lower()
    if not needle:
        return 0
    with _lock:
        lines = entries()
        kept = [
            line for line in lines
            if needle not in line.lower() and not _similar(text, line, 0.5)
        ]
        if len(kept) != len(lines):
            _save(kept)
    return len(lines) - len(kept)


def capture_from_message(message: str) -> list[str]:
    """Save what the user explicitly asked to be remembered. Never raises."""
    saved: list[str] = []
    try:
        match = _REMEMBER_RE.match(message)
        if match and not message.rstrip().endswith("?"):
            statement = match.group("text").strip()
            if not _NOT_A_STATEMENT_RE.match(statement) and remember(statement):
                saved.append(statement)
        name = _NAME_RE.search(message)
        if name and not message.rstrip().endswith("?") and remember(f"Nama: {name.group('name')}"):
            saved.append(f"Nama: {name.group('name')}")
    except Exception as exc:  # noqa: BLE001 - memory must never break a turn
        logger.warning(f"[user_profile] capture failed: {exc}")
    return saved


def prompt_block() -> str:
    """The profile as a system prompt section, or "" when empty. Never raises."""
    lines = entries()
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)[:_MAX_PROMPT_CHARS]
    return f"\n\n[ABOUT THE USER: stated by them earlier. Use it, and do not ask again.]\n{body}"
