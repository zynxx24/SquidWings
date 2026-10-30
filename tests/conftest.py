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

import pytest


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

    monkeypatch.setattr(workspace_context, "WORKSPACE_ROOT", tmp_path / "workspace")
