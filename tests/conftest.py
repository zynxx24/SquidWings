"""Make the default test suite deterministic and strictly offline."""

from __future__ import annotations

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
os.environ["MT4_RECORD_TICKS"] = "0"     # bridge tests must not write into trading/data
os.environ["TRADE_NEWS_AUTO"] = "0"      # proposals must not fetch the live economic calendar
os.environ["QUANT_WORKER"] = "0"          # keep backtests in the test process
os.environ["DOCUMENT_OCR"] = "false"      # each image would be a model call
os.environ["AUTO_UPDATE"] = "false"       # tests must not pull from GitHub or restart
os.environ["DAILY_BRIEF"] = "false"       # the start-up brief would write into the developer's real .agent_memory
os.environ["SEND_IMAGES"] = "false"       # a developer .env with SEND_IMAGES=true must not change upload tests
os.environ["TOOLS_AUTO_UPDATE"] = "false"   # no network or global installs during tests
# Learned model stats must not land in the real .agent_memory: every test run would add fake failures.
import tempfile as _tempfile  # noqa: E402

os.environ["MODEL_STATS_PATH"] = os.path.join(_tempfile.mkdtemp(prefix="model-stats-"), "stats.json")
# Router providers and the token log must not touch the real web/data/router.
os.environ["ROUTER_STORE_DIR"] = _tempfile.mkdtemp(prefix="router-store-")
os.environ["STYLE_HISTORY_FILE"] = os.path.join(_tempfile.mkdtemp(prefix="style-history-"), "h.json")  # looks used, across chats
os.environ["ROUTER_IMPORT_ENV"] = "0"      # the server's start-up must not copy .env endpoints into it

import pytest

from config.edition import trading_enabled


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``@pytest.mark.enterprise`` tests when the Enterprise trading module is not installed."""
    if trading_enabled():
        return
    skip = pytest.mark.skip(reason="needs the Enterprise trading module")
    for item in items:
        if "enterprise" in item.keywords:
            item.add_marker(skip)


# Offline tests must pass on a fresh clone, which has no .env.
os.environ["API_KEY"] = "test-key-not-real"


@pytest.fixture(autouse=True)
def block_all_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail immediately if production code tries to contact a real model/API."""

    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Network access is forbidden in the offline test suite")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture(autouse=True)
def isolated_workspace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Team runs and web sessions create folders; keep them out of the real workspace."""
    from utils import workspace_context

    monkeypatch.setattr(workspace_context, "SESSIONS_ROOT", tmp_path / "workspace" / "sessions")
    monkeypatch.setattr(workspace_context, "PROJECTS_ROOT", tmp_path / "workspace" / "projects")


@pytest.fixture(autouse=True)
def _roomy_disk_for_video_teams(monkeypatch):
    """Video team runs refuse to start on a nearly full disk; tests must not depend on this machine's."""
    import agent.team_orchestrator as team_module

    monkeypatch.setattr(team_module, "free_bytes", lambda path: 50 * 2**30)
