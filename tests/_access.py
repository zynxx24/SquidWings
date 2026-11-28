"""Log a test client in from elsewhere: past the access PIN, as the owner's phone would be."""

from __future__ import annotations


def logged_in() -> dict[str, str]:
    from brain.security import access_gate

    if not access_gate.has_pin():
        access_gate.set_pin("246810")
    return {access_gate.COOKIE: access_gate.issue()}
