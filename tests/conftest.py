"""Make the default test suite deterministic and strictly offline."""

from __future__ import annotations

import asyncio.base_events as _asyncio_base
import os
import socket
from typing import Any

# Read once at import by web.server: the test client's Host, no token, and no
# user cap, whatever the developer's own .env says.
os.environ["WEB_ALLOWED_HOSTS"] = "testserver,test"
os.environ["WEB_MAX_USERS"] = "100000"
os.environ["WEB_RATE_LIMIT_RPM"] = "100000"
# Tests must not depend on bubblewrap being installed. test_sandbox.py covers it.
os.environ["SANDBOX_MODE"] = "off"
# The local graphify and aislop programs must not run during ordinary tests.
os.environ["GRAPHIFY_AUTO"] = "false"
os.environ["AISLOP_AUTO"] = "false"
os.environ["MT4_RECORD_TICKS"] = "0"     # bridge tests must not write into addons/trading/data
os.environ["TRADE_NEWS_AUTO"] = "0"      # proposals must not fetch the live economic calendar
os.environ["QUANT_WORKER"] = "0"          # keep backtests in the test process
os.environ["DOCUMENT_OCR"] = "false"      # each image would be a model call
os.environ["AUTO_UPDATE"] = "false"       # tests must not pull from GitHub or restart
os.environ["UPDATE_REPO"] = "/nonexistent/update-repo.git"   # a folder without .git must never reach GitHub
os.environ["DAILY_BRIEF"] = "false"       # the start-up brief would write into the developer's real .agent_memory
os.environ["SEND_IMAGES"] = "false"       # a developer .env with SEND_IMAGES=true must not change upload tests
os.environ["TOOLS_AUTO_UPDATE"] = "false"   # no network or global installs during tests
# Learned model stats must not land in the real .agent_memory: every test run would add fake failures.
import tempfile as _tempfile  # noqa: E402

os.environ["MODEL_STATS_PATH"] = os.path.join(_tempfile.mkdtemp(prefix="model-stats-"), "stats.json")
# Router providers and the token log must not touch the real web/data/router.
os.environ["ROUTER_STORE_DIR"] = _tempfile.mkdtemp(prefix="router-store-")
os.environ["STYLE_HISTORY_FILE"] = os.path.join(_tempfile.mkdtemp(prefix="style-history-"), "h.json")  # looks used, across chats
os.environ["SQUIDWINGS_DATA_DIR"] = _tempfile.mkdtemp(prefix="sw-data-")   # audit chain, logs, reports
os.environ["SQUIDWINGS_HOME"] = _tempfile.mkdtemp(prefix="sw-home-")       # uploaded skills, moved data
os.environ["ROUTER_IMPORT_ENV"] = "0"      # the server's start-up must not copy .env endpoints into it
# Add-ons are off by default; the suite covers them, so switch both on (a test may still turn one off).
os.environ.setdefault("ADDON_TRADING", "1")
os.environ.setdefault("ADDON_STUDIO", "1")

from collections.abc import Iterator

import pytest

from brain.config.edition import trading_enabled


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``@pytest.mark.enterprise`` tests when the optional trading module is not installed."""
    if trading_enabled():
        return
    skip = pytest.mark.skip(reason="needs the optional trading module")
    for item in items:
        if "enterprise" in item.keywords:
            item.add_marker(skip)


# Offline tests must pass on a fresh clone, which has no .env.
os.environ["API_KEY"] = "test-key-not-real"


@pytest.fixture(autouse=True)
def fresh_caches() -> None:
    """Shared caches (classifier answers, pages, indexes) must not carry over from one test to the next."""
    from brain.utils import cache

    cache.clear()



@pytest.fixture
def loopback_only(block_all_network: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """For a test that drives a local program (headless Chrome, a server it starts itself): connections to this
    machine work, everything else is still refused, so the suite stays offline."""

    def host_of(address: Any) -> str:
        return str(address[0]) if isinstance(address, tuple) and address else ""

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if host_of(address) not in _LOOPBACK:
            raise AssertionError("Network access is forbidden in the offline test suite")
        return _REAL_CREATE_CONNECTION(address, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> Any:
        if host_of(address) not in _LOOPBACK:
            raise AssertionError("Network access is forbidden in the offline test suite")
        return _REAL_CONNECT(self, address)

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket.socket, "connect", connect)
    _block_asyncio_connections(monkeypatch, loopback=True)


_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_CONNECT = socket.socket.connect
_REAL_ASYNC_CREATE = _asyncio_base.BaseEventLoop.create_connection
_REAL_ASYNC_SOCK_CONNECT = _asyncio_base.BaseEventLoop.sock_connect
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

def _inside_socketpair() -> bool:
    """On Windows, socket.socketpair() is made from two loopback sockets (socket._fallback_socketpair), and every
    asyncio event loop makes one for itself. That is not network access; nothing else is let through."""
    import sys

    frame = sys._getframe(2)
    while frame is not None:
        if frame.f_code.co_name == "_fallback_socketpair" and frame.f_code.co_filename.endswith("socket.py"):
            return True
        frame = frame.f_back
    return False


@pytest.fixture(autouse=True)
def block_all_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail immediately if production code tries to contact a real model/API."""

    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Network access is forbidden in the offline test suite")

    def connect(self: socket.socket, address: Any) -> Any:
        if _inside_socketpair():
            return _REAL_CONNECT(self, address)
        raise AssertionError("Network access is forbidden in the offline test suite")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    # On Windows asyncio connects through the proactor (ConnectEx), never through socket.connect: block it there
    # too, or an httpx call in a test reaches the network.
    _block_asyncio_connections(monkeypatch, loopback=False)


def _block_asyncio_connections(monkeypatch: pytest.MonkeyPatch, *, loopback: bool) -> None:
    base_events = _asyncio_base
    real_create = _REAL_ASYNC_CREATE
    real_sock_connect = _REAL_ASYNC_SOCK_CONNECT

    def allowed(host: Any) -> bool:
        return loopback and str(host or "") in _LOOPBACK

    async def create_connection(self: Any, protocol_factory: Any, host: Any = None, port: Any = None, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("sock") is None and not allowed(host):
            raise AssertionError("Network access is forbidden in the offline test suite")
        return await real_create(self, protocol_factory, host, port, *args, **kwargs)

    async def sock_connect(self: Any, sock: socket.socket, address: Any) -> Any:
        if not allowed(address[0] if isinstance(address, tuple) and address else ""):
            raise AssertionError("Network access is forbidden in the offline test suite")
        return await real_sock_connect(self, sock, address)

    monkeypatch.setattr(base_events.BaseEventLoop, "create_connection", create_connection)
    monkeypatch.setattr(base_events.BaseEventLoop, "sock_connect", sock_connect)


@pytest.fixture(autouse=True)
def no_agent_config_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The skill sync registers the skills server with Claude Code, Codex...: never with the real ones from a test."""
    monkeypatch.setenv("SKILLS_MCP_CONNECT", "0")


@pytest.fixture(autouse=True)
def isolated_credentials_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Keys and limits typed on the web page live in a file; tests must neither read nor write the real one."""
    monkeypatch.setenv("CREDENTIALS_FILE", str(tmp_path / "credentials.json"))


@pytest.fixture(autouse=True)
def isolated_workspace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Team runs and web sessions create folders; keep them out of the real workspace."""
    from brain.utils import workspace_context

    monkeypatch.setattr(workspace_context, "SESSIONS_ROOT", tmp_path / "workspace" / "sessions")
    monkeypatch.setattr(workspace_context, "PROJECTS_ROOT", tmp_path / "workspace" / "projects")


@pytest.fixture(autouse=True)
def _roomy_disk_for_video_teams(monkeypatch):
    """Video team runs refuse to start on a nearly full disk; tests must not depend on this machine's."""
    import brain.agent.team_orchestrator as team_module

    monkeypatch.setattr(team_module, "free_bytes", lambda path: 50 * 2**30)


@pytest.fixture(autouse=True)
def _owner_limits_do_not_leak_into_tests(monkeypatch):
    """The owner's .env lifts limits (AGENT_MAX_ROUNDS=0, a saved sudo password); once any test loads it, those
    values sit in os.environ. Tests count rounds and must never run sudo, so each starts without them."""
    for name in ("AGENT_MAX_ROUNDS", "SUDO_PASSWORD", "AGENT_CONTEXT_CHARS", "AGENT_TOOL_RESULT_CHARS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _test_client_is_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Web tests act as the owner's browser on this machine (loopback), as they were written.

    Remote access needs the access PIN (brain/security/access_gate.py); tests for that pass ``client=`` themselves.
    """
    from starlette import testclient

    original = testclient.TestClient.__init__

    def init(self: testclient.TestClient, *args: object, client: tuple[str, int] = ("127.0.0.1", 50000),
             **kwargs: object) -> None:
        original(self, *args, client=client, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(testclient.TestClient, "__init__", init)


@pytest.fixture(autouse=True)
def _no_access_pin_between_tests() -> Iterator[None]:
    """An access PIN one test sets never carries over to the next."""
    yield
    from brain.config import paths

    (paths.data_dir() / "web_access.json").unlink(missing_ok=True)
