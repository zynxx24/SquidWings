"""Which data an agent may touch while it runs, and how.

Normal chat is the owner at the keyboard, so nothing is restricted there. A team agent only reaches the sources its
node was granted, with the rights it was granted. An agent that answers customers on Telegram (``public``) is held
to a stricter rule than the node says: it can read and add rows but never change or delete them, and text it writes
that would act as a formula in a spreadsheet is made harmless.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .base import DataError

RIGHTS = {
    "read": frozenset({"read"}),
    "append": frozenset({"append"}),
    "read+append": frozenset({"read", "append"}),
    "write": frozenset({"read", "append", "write"}),
}
RIGHT_LABELS = {
    "read": "Baca saja",
    "append": "Tambah baris saja",
    "read+append": "Baca dan tambah baris",
    "write": "Baca dan ubah semua",
}
MAX_NOTIFICATIONS = 3
_FORMULA = re.compile(r"^[=@]|^[+-](?!\d|\s*$)")


@dataclass
class Access:
    grants: dict[str, str] = field(default_factory=dict)
    public: bool = False
    notified: int = 0
    # Columns an agent must not see or write, per source. For a customer-facing agent a column whose name
    # starts with "_" is always hidden too, so the owner can keep a cost price in the same sheet.
    hidden: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Who is talking, added to what the agent tells the owner ("Budi, @budi, chat 123").
    context: str = ""

    def rights(self, source: str) -> frozenset[str]:
        rights = RIGHTS.get(self.grants.get(source, ""), frozenset())
        return rights - {"write"} if self.public else rights

    def hides(self, source: str, column: str) -> bool:
        if column.lower() in (h.lower() for h in self.hidden.get(source, ())):
            return True
        return self.public and column.startswith("_")


_current: ContextVar[Access | None] = ContextVar("datastore_access", default=None)


def current() -> Access | None:
    return _current.get()


@contextmanager
def restrict(access: Access | None) -> Iterator[None]:
    token = _current.set(access)
    try:
        yield
    finally:
        _current.reset(token)


def require(source: str, need: str) -> None:
    """Raise a plain error when the running agent lacks a right. ``source`` is "" for the agent's own workspace file."""
    access = current()
    if access is None or not source:
        return
    if need in access.rights(source):
        return
    if source not in access.grants:
        raise DataError(f"Agent ini tidak diberi akses ke sumber data '{source}'. Beri aksesnya di node agent > Data.")
    wanted = {"read": "membaca", "append": "menambah baris", "write": "mengubah atau menghapus"}[need]
    raise DataError(f"Agent ini tidak boleh {wanted} di '{source}'.")


def allows(source: str, need: str) -> bool:
    """Whether the running agent holds a right on a source, without raising."""
    access = current()
    return access is None or not source or need in access.rights(source)


def require_any(source: str, needs: tuple[str, ...]) -> None:
    """Like ``require`` but any one of the rights is enough, e.g. to learn the columns before adding a row."""
    if not any(allows(source, need) for need in needs):
        require(source, needs[0])


def defuse(value: object) -> object:
    """Text from a customer that a spreadsheet would run as a formula is stored as plain text instead."""
    access = current()
    if access is not None and access.public and isinstance(value, str) and _FORMULA.search(value.strip()):
        return "'" + value
    return value


def hidden_columns(source: str, columns: list[str]) -> set[str]:
    """The columns of a source the running agent must not see."""
    access = current()
    if access is None or not source:
        return set()
    return {c for c in columns if access.hides(source, c)}
