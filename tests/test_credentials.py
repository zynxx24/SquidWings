"""Keys and IDs set on the team page instead of .env: the store, who reads it, the API, and share_file being offered."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import credentials
from config.credentials import CredentialError, CredentialStore

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"


@pytest.fixture(autouse=True)
def private_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    for spec in credentials.FIELDS:
        monkeypatch.delenv(spec.env, raising=False)
    from config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_value_typed_on_the_page_beats_env_and_clearing_it_brings_env_back(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    get_settings.cache_clear()
    assert credentials.get("telegram_chat_id") == "111" and credentials.source("telegram_chat_id") == "env"
    credentials.get_store().update({"telegram_chat_id": "@kanal_saya"})
    assert credentials.get("telegram_chat_id") == "@kanal_saya" and credentials.source("telegram_chat_id") == "web"
    credentials.get_store().update({"telegram_chat_id": ""})
    assert credentials.get("telegram_chat_id") == "111"
    assert credentials.source("brave_search_api_key") == "" and credentials.get("brave_search_api_key") == ""


def test_values_are_checked_saved_owner_only_and_read_back_after_a_restart(tmp_path):
    store = credentials.get_store()
    with pytest.raises(CredentialError, match="Contoh"):
        store.update({"telegram_bot_token": "not a token"})
    with pytest.raises(CredentialError, match="tidak dikenal"):
        store.update({"API_KEY": "x"})
    store.update({"telegram_bot_token": "123456789:AAbbccddeeffgghhiijjkkllmmnn", "searxng_url": "http://127.0.0.1:8888"})
    path = Path(os.environ["CREDENTIALS_FILE"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = CredentialStore(path)
    assert again.web_value("searxng_url") == "http://127.0.0.1:8888"


def test_the_page_never_gets_a_secret_back():
    credentials.get_store().update({"telegram_bot_token": "123456789:AAbbccddeeffgghhiijjkkllmmnn", "telegram_chat_id": "42"})
    fields = {f["key"]: f for f in credentials.public()["fields"]}
    assert fields["telegram_bot_token"]["masked"] == "…mmnn" and fields["telegram_bot_token"]["set"]
    assert "AAbb" not in str(credentials.public())
    assert fields["telegram_chat_id"]["masked"] == "42", "an ID is not a secret"
    assert {g["key"] for g in credentials.public()["groups"]} == {"telegram", "search", "voice", "google", "database", "limits"}


@pytest.mark.asyncio
async def test_telegram_and_search_read_the_page_values():
    from agent.team_outputs import OutputError, database_options, send_telegram
    from tool.execute.execute_web_search.web_search_tool import _get_config

    with pytest.raises(OutputError, match="Kredensial"):
        await send_telegram("hi")
    credentials.get_store().update({"brave_search_api_key": "BSA1234567890abc", "searxng_url": "http://127.0.0.1:8888/"})
    config = _get_config()
    assert config.brave_api_key == "BSA1234567890abc" and config.searxng_url == "http://127.0.0.1:8888"
    postgres = next(o for o in database_options() if o["id"] == "postgres")
    assert not postgres["ready"] and "Kredensial" in postgres["reason"]


def test_credentials_api(monkeypatch):
    from web.server import app

    client = TestClient(app, client=("127.0.0.1", 50000))
    listed = client.get("/api/teams/credentials").json()
    assert len(listed["fields"]) == len(credentials.FIELDS)
    saved = client.put("/api/teams/credentials", json={"values": {"brave_search_api_key": "BSA1234567890abc"}}).json()
    brave = next(f for f in saved["fields"] if f["key"] == "brave_search_api_key")
    assert brave["source"] == "web" and brave["masked"] == "…0abc"
    assert client.put("/api/teams/credentials", json={"values": {"searxng_url": "ftp://x"}}).status_code == 422
    credentials.get_store().update({"brave_search_api_key": ""})
    r = client.post("/api/teams/credentials/test", json={"group": "search"})
    assert r.status_code == 422 and "pencarian bawaan" in r.json()["detail"]
    assert client.post("/api/teams/credentials/test", json={"group": "nope"}).status_code == 422


def test_share_file_comes_with_any_tool_that_makes_files():
    from agent.harness.tiers import scaffold_for
    from agent.harness.tool_select import select_tools
    from config.constants import TaskType
    from tool.register_tools import register_all_tools

    schemas = register_all_tools().get_all_schemas()
    lite = scaffold_for("x-flash")
    for task, text in ((TaskType.ACTIVITY, "buatkan ringkasan rapat"), (TaskType.CODE, "tulis skrip untuk data ini")):
        names = [s["function"]["name"] for s in select_tools(schemas, task, text, lite)]
        assert "share_file" in names, task
        assert lite.max_tools is None or len(names) <= lite.max_tools


def test_every_element_the_team_editor_uses_is_on_the_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([\w-]+)"', html))
    script = (STATIC / "js" / "team.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\('([\w-]+)'\)", script))
    made_by_script = set(re.findall(r'id="([\w-]+)"', script))   # parts the editor builds itself, such as a run's detail
    assert used - ids - made_by_script == set()
    scripts = re.findall(r'src="(/static/js/[\w.-]+)"', html)
    assert scripts.index("/static/js/team.js") == scripts.index("/static/js/app.js") + 1, "team.js needs window.SQ from app.js"
    assert "/static/css/team.css" in html


def test_the_data_tab_and_the_right_click_menu_are_wired_without_double_clicks():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([\w-]+)"', html))
    data = (STATIC / "js" / "data.js").read_text(encoding="utf-8")
    team = (STATIC / "js" / "team.js").read_text(encoding="utf-8")
    menu = (STATIC / "js" / "ctxmenu.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\('([\w-]+)'\)", data))
    made = set(re.findall(r'id="([\w-]+)"', data))
    assert used - ids - made == set(), "data.js asks for an element that is not on the page"
    scripts = re.findall(r'src="(/static/js/[\w.-]+)"', html)
    assert scripts.index("/static/js/ctxmenu.js") < scripts.index("/static/js/app.js"), "team.js and data.js use SQMenu while loading"
    assert scripts.index("/static/js/data.js") > scripts.index("/static/js/team.js"), "data.js hooks into window.SQTeam"
    assert "/static/css/data.css" in html and 'data-tab="data"' in html
    for name in ("team.js", "data.js", "ctxmenu.js"):
        source = (STATIC / "js" / name).read_text(encoding="utf-8")
        assert "dblclick" not in source and "ondblclick" not in source, f"{name} must not edit on a double click"
    assert "contextmenu" in menu and "ContextMenu" in menu and "longPress" in menu       # mouse, keyboard and touch
    assert "SQMenu.on($('board')" in team and "SQMenu.on(wrap" in data
    assert "innerHTML" not in menu, "menu labels carry names the user typed, so they are never parsed as HTML"


def test_the_google_key_is_set_on_the_page_only_and_the_page_sees_just_the_email() -> None:
    import json

    from tests.test_datastore import make_account

    account = make_account()
    raw = json.dumps({"type": "service_account", "token_uri": "https://evil.example", "client_id": "9", **account})
    credentials.get_store().update({"google_service_account": raw})
    field = next(f for f in credentials.public()["fields"] if f["key"] == "google_service_account")
    assert field["set"] and field["source"] == "web" and field["env"] == "" and field["multiline"]
    assert field["masked"] == account["client_email"]
    assert "PRIVATE" not in str(credentials.public())
    stored = json.loads(credentials.get("google_service_account"))
    assert set(stored) == {"type", "client_email", "private_key", "project_id"}       # nothing else is kept
    assert credentials.google_email() == account["client_email"]
    with pytest.raises(credentials.CredentialError):
        credentials.get_store().update({"google_service_account": '{"type": "authorized_user"}'})
    credentials.get_store().update({"google_service_account": ""})
    assert credentials.get("google_service_account") == "" and credentials.google_email() == ""


def test_team_limits_and_the_sqlite_path_are_set_on_the_page_and_nothing_needs_env(monkeypatch, tmp_path) -> None:
    from agent.team_orchestrator import TeamOrchestrator
    from config.settings import get_settings

    # Untouched: the built-in numbers apply and the page says so.
    assert credentials.number("team_run_seconds") == 600
    assert credentials.source("team_run_seconds") == "default" and credentials.default_of("team_run_seconds") == "600"
    assert credentials.source("team_db_sqlite_path") == "default"

    credentials.get_store().update({
        "team_run_seconds": "900", "team_member_tokens": "250000", "team_video_member_rounds": "55",
        "team_db_sqlite_path": str(tmp_path / "hasil" / "toko.sqlite3"),
    })
    assert credentials.source("team_run_seconds") == "web"
    run = TeamOrchestrator._run_budget(type("Run", (), {"budget": {}})())
    assert run["run_seconds"] == 900 and run["tokens"] == 250_000 and run["rounds"] == 10
    video = TeamOrchestrator._budget(type("Team", (), {"mode": "video"})())
    assert video["rounds"] == 55 and video["run_seconds"] == 3600

    from agent.team_outputs import _save_sqlite

    _save_sqlite("catatan", {"run_id": "r1", "content": "halo"})
    assert (tmp_path / "hasil" / "toko.sqlite3").exists()

    # Clearing the page's copy brings .env, then the built-in value, back.
    monkeypatch.setenv("TEAM_RUN_SECONDS", "700")
    get_settings.cache_clear()
    credentials.get_store().update({"team_run_seconds": ""})
    assert credentials.number("team_run_seconds") == 700 and credentials.source("team_run_seconds") == "env"


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1.5", "99999999", " "])
def test_a_limit_that_would_stop_every_team_is_refused(bad) -> None:
    if not bad.strip():
        credentials.get_store().update({"team_run_seconds": bad})      # empty means "use the default"
        return
    with pytest.raises(CredentialError):
        credentials.get_store().update({"team_run_seconds": bad})


def test_the_limits_card_has_no_test_button_and_is_never_called_connected() -> None:
    script = (STATIC / "js" / "team.js").read_text(encoding="utf-8")
    assert "group.key === 'limits'" in script and "testable" in script
    shown = credentials.public()
    limits = [f for f in shown["fields"] if f["group"] == "limits"]
    assert len(limits) == 8 and all(f["default"] and not f["secret"] for f in limits)
    assert any(g["key"] == "limits" for g in shown["groups"])
