"""Feature flags for the web UI.

Development runs chat-only: the team canvas tab is hidden and the team API is
not mounted at all. Other environments keep the canvas. ``WEB_TEAMS_ENABLED``
overrides the APP_ENV default in both directions.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config.settings import Settings
from web import server, team_api


def test_teams_follow_app_env() -> None:
    assert Settings(app_env="development").teams_enabled is False
    assert Settings(app_env="production").teams_enabled is True


def test_explicit_flag_wins() -> None:
    assert Settings(app_env="development", web_teams_enabled=True).teams_enabled is True
    assert Settings(app_env="production", web_teams_enabled=False).teams_enabled is False


def test_config_endpoint_reports_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "get_settings", lambda: Settings(app_env="development"))
    response = TestClient(server.app).get("/api/config")
    assert response.status_code == 200
    assert response.json()["features"]["teams"] is False


def test_team_api_degrades_when_backend_missing() -> None:
    """A missing team orchestrator answers 503, never a 500 on ``None.store``."""
    app = FastAPI()
    app.state.team_orchestrator = None
    app.include_router(team_api.router)
    response = TestClient(app).get("/api/teams")
    assert response.status_code == 503
