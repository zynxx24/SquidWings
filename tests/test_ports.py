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


@pytest.mark.enterprise
def test_web_starts_the_trading_server_when_port_80_is_allowed(monkeypatch):
    import subprocess

    from web import trading_server

    started = []
    monkeypatch.setattr(trading_server, "port_problem", lambda host, port: None)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append(cmd) or "proc")
    proc, line = main._start_trading_backend()
    assert proc == "proc" and started[0][-1] == "trading" and "http://127.0.0.1/trading" in line


@pytest.mark.enterprise
def test_web_explains_a_forbidden_port_80(monkeypatch):
    from web import trading_server

    monkeypatch.setattr(trading_server, "port_problem", lambda host, port: trading_server.PORT_HINT.format(port=80))
    proc, line = main._start_trading_backend()
    assert proc is None and main.SYSCTL_PORT_80 in line


@pytest.mark.enterprise
def test_web_reuses_a_running_trading_server(monkeypatch):
    from web import server, trading_server

    async def running() -> bool:
        return True

    monkeypatch.setattr(trading_server, "port_problem", lambda host, port: "Port 80 is already in use. Stop it.")
    monkeypatch.setattr(server, "trading_server_running", running)
    monkeypatch.setattr(trading_server, "code_is_stale", lambda port=80, host="127.0.0.1": False)
    proc, line = main._start_trading_backend()
    assert proc is None and "sudah jalan" in line


@pytest.mark.enterprise
def test_web_replaces_a_trading_server_running_old_code(monkeypatch):
    import subprocess

    from web import server, trading_server

    async def running() -> bool:
        return True

    asked, started = [], []
    monkeypatch.setattr(trading_server, "port_problem", lambda host, port: "Port 80 is already in use. Stop it.")
    monkeypatch.setattr(server, "trading_server_running", running)
    monkeypatch.setattr(trading_server, "code_is_stale", lambda port=80, host="127.0.0.1": True)
    monkeypatch.setattr(trading_server, "prepare_port", lambda host, port, **kw: asked.append(kw) or None)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append(cmd) or "proc")
    proc, line = main._start_trading_backend()
    assert proc == "proc" and "server lama diganti" in line and asked[0]["interactive"] is False


@pytest.mark.enterprise
def test_trading_replaces_its_own_older_copy_but_not_another_program(monkeypatch):
    from utils import ports
    from web import trading_server

    stopped = []
    monkeypatch.setattr(trading_server, "port_problem", lambda host, port: "Port 80 is already in use. Stop whatever is listening there first.")
    monkeypatch.setattr(ports, "holder", lambda host, port: ports.Holder(4242, "/x/.venv/bin/python /x/main.py trading", False))
    monkeypatch.setattr(ports, "stop", lambda pid, port, host: stopped.append(pid) or True)
    assert trading_server.prepare_port("127.0.0.1", 80, say=lambda text: None) is None and stopped == [4242]
    monkeypatch.setattr(ports, "holder", lambda host, port: ports.Holder(77, "/usr/sbin/nginx -g daemon off;", False))
    problem = trading_server.prepare_port("127.0.0.1", 80, say=lambda text: None)
    assert problem and "nginx" in problem and "kill 77" in problem and stopped == [4242]
    assert not trading_server.is_our_trading_server("python main.py web")


@pytest.mark.enterprise
def test_a_forbidden_port_80_is_opened_once_with_sudo_in_the_terminal(monkeypatch):
    import subprocess

    from web import trading_server

    allowed = []
    monkeypatch.setattr(trading_server, "port_problem",
                        lambda host, port: None if allowed else trading_server.PORT_HINT.format(port=80))
    monkeypatch.setattr(trading_server.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(trading_server.shutil, "which", lambda name: "/usr/bin/sudo")

    class Done:
        returncode = 0

    def run(cmd, **kw):
        allowed.append(cmd)
        return Done()

    monkeypatch.setattr(subprocess, "run", run)
    assert trading_server.prepare_port("127.0.0.1", 80, say=lambda text: None, interactive=True) is None
    assert allowed[0][:3] == ["sudo", "sh", "-c"] and "ip_unprivileged_port_start=80" in allowed[0][3]
    assert trading_server.SYSCTL_FILE in allowed[0][3], "kept after a reboot too"
