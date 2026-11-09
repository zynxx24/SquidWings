"""A busy web port: who holds it, and which port the web command picks. Offline."""

from __future__ import annotations

import socket

import pytest

import main
from utils import ports


@pytest.fixture
def busy():
    """A port held by a plain listening socket (not SquidWings)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def test_port_free_and_busy(busy):
    assert not ports.port_free("127.0.0.1", busy)
    nxt = ports.next_free_port("127.0.0.1", busy)
    assert nxt is not None and nxt > busy and ports.port_free("127.0.0.1", nxt)


def test_holder_finds_this_process(busy):
    import os

    h = ports.holder("127.0.0.1", busy)
    assert h.pid == os.getpid()
    assert not h.squidwings
    assert str(os.getpid()) in h.describe()


def test_free_port_is_kept():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert main._choose_web_port("127.0.0.1", port) == port


def test_busy_port_moves_to_the_next_free_one(busy):
    chosen = main._choose_web_port("127.0.0.1", busy)
    assert chosen is not None and chosen != busy and ports.port_free("127.0.0.1", chosen)


def test_strict_port_refuses(busy):
    assert main._choose_web_port("127.0.0.1", busy, strict=True) is None


def test_replace_never_stops_a_foreign_program(busy, monkeypatch):
    stopped = []
    monkeypatch.setattr(ports, "stop", lambda *a, **k: stopped.append(a) or True)
    chosen = main._choose_web_port("127.0.0.1", busy, replace=True)
    assert stopped == [] and chosen not in (None, busy)


@pytest.mark.parametrize(("answer", "expected"), [("", "alt"), ("1", "alt"), ("2", "same"), ("3", None)])
def test_prompt_when_squidwings_holds_the_port(busy, monkeypatch, answer, expected):
    monkeypatch.setattr(ports, "holder", lambda host, port: ports.Holder(4242, "python main.py web", True))
    monkeypatch.setattr(ports, "stop", lambda pid, port, host: pid == 4242)
    chosen = main._choose_web_port("127.0.0.1", busy, ask=lambda _: answer)
    if expected is None:
        assert chosen is None
    elif expected == "same":
        assert chosen == busy
    else:
        assert chosen not in (None, busy)


def test_no_free_port_left(busy, monkeypatch):
    monkeypatch.setattr(ports, "next_free_port", lambda host, start, tries=20: None)
    assert main._choose_web_port("127.0.0.1", busy) is None
