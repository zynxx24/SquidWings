"""The IDE add-on's API: projects, files, the guard around them, search, and the AI's changes."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from addons.ide.web import ide_api
from brain.utils.file_history import FileHistory


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    sessions = tmp_path / "sessions"
    folder = sessions / "[web] Toko Kopi"
    (folder / "src").mkdir(parents=True)
    (folder / "index.html").write_text("<h1>Kopi</h1>\n<script src='src/app.js'></script>\n", encoding="utf-8")
    (folder / "src" / "app.js").write_text("const harga = 15000;\n", encoding="utf-8")
    (folder / ".session-id").write_text("sess-abc", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("rahasia", encoding="utf-8")
    monkeypatch.setattr(ide_api, "ROOTS", {"s": sessions, "p": tmp_path / "projects"})
    app = FastAPI()
    app.include_router(ide_api.router)
    return TestClient(app, client=("127.0.0.1", 5000)), folder


P = "/api/ide/p/s:%5Bweb%5D%20Toko%20Kopi"


def test_projects_are_the_chat_folders(setup: Any) -> None:
    client, _ = setup
    [project] = client.get("/api/ide/projects").json()["projects"]
    assert project["id"] == "s:[web] Toko Kopi" and project["name"] == "Toko Kopi" and project["tag"] == "web"
    assert project["session"] == "sess-abc" and project["files"] == 2


def test_tree_read_and_save(setup: Any) -> None:
    client, folder = setup
    tree = client.get(f"{P}/tree").json()
    assert [f["path"] for f in tree["files"]] == ["index.html", "src/app.js"] and tree["folders"] == ["src"]
    opened = client.get(f"{P}/file", params={"path": "src/app.js"}).json()
    assert opened["text"] == "const harga = 15000;\n"
    saved = client.put(f"{P}/file", json={"path": "src/app.js", "text": "const harga = 18000;\n", "mtime": opened["mtime"]})
    assert saved.status_code == 200 and (folder / "src/app.js").read_text() == "const harga = 18000;\n"
    # The AI wrote it again meanwhile: the old version from the editor is refused, unless forced.
    stale = client.put(f"{P}/file", json={"path": "src/app.js", "text": "lama", "mtime": opened["mtime"]})
    assert stale.status_code == 409
    assert client.put(f"{P}/file?force=1", json={"path": "src/app.js", "text": "paksa", "mtime": opened["mtime"]}).status_code == 200


@pytest.mark.parametrize("path", ["../../secret.txt", "/etc/passwd", ".agent_memory/history/index.json", ".session-id"])
def test_nothing_outside_the_project_or_private_is_reachable(setup: Any, path: str) -> None:
    client, folder = setup
    (folder / ".agent_memory" / "history").mkdir(parents=True)
    (folder / ".agent_memory" / "history" / "index.json").write_text("{}")
    assert client.get(f"{P}/file", params={"path": path}).status_code == 404
    assert client.put(f"{P}/file", json={"path": path, "text": "x"}).status_code == 404


@pytest.mark.parametrize("project", ["s:..", "s:.agent_memory", "x:anything", "s:nope", "s:a%2Fb"])
def test_unknown_projects_are_refused(setup: Any, project: str) -> None:
    client, _ = setup
    assert client.get(f"/api/ide/p/{project}/tree").status_code == 404


def test_a_page_previews_sandboxed(setup: Any) -> None:
    client, _ = setup
    page = client.get(f"{P}/raw/index.html")
    assert page.status_code == 200 and "<h1>Kopi</h1>" in page.text
    assert page.headers["content-security-policy"].startswith("sandbox allow-scripts")
    assert "allow-same-origin" not in page.headers["content-security-policy"]
    assert client.get(f"{P}/raw/src").status_code == 404     # a folder without index.html


def test_search_finds_lines(setup: Any) -> None:
    client, _ = setup
    found = client.get(f"{P}/search", params={"q": "HARGA"}).json()
    assert found["hits"] == [{"path": "src/app.js", "line": 1, "col": 7, "text": "const harga = 15000;"}]
    assert client.get(f"{P}/search", params={"q": "harga", "case": True, "regex": True}).json()["files"] == 1
    assert client.get(f"{P}/search", params={"q": "(", "regex": True}).status_code == 422


def test_changes_diff_and_restore(setup: Any) -> None:
    client, folder = setup
    history = FileHistory(folder)
    started = history.begin()
    (folder / "src/app.js").write_text("const harga = 20000;\nconst diskon = 0.1;\n", encoding="utf-8")
    turn = history.end(started, "naikkan harga", "sess-abc")
    assert turn is not None
    [listed] = client.get(f"{P}/turns").json()["turns"]
    assert listed["prompt"] == "naikkan harga" and listed["added"] == 2 and listed["removed"] == 1
    tree = client.get(f"{P}/tree").json()
    assert tree["last_turn"] == {"src/app.js": "modified"}
    diff = client.get(f"{P}/diff", params={"turn": turn.id, "path": "src/app.js"}).json()
    assert diff["before"] == "const harga = 15000;\n" and "diskon" in diff["after"] and diff["stored"]
    assert client.post(f"{P}/restore", json={"turn": turn.id}).status_code == 200
    assert (folder / "src/app.js").read_text() == "const harga = 15000;\n"


def test_delete_goes_to_the_project_trash_and_zip_has_the_work(setup: Any) -> None:
    client, folder = setup
    assert client.post(f"{P}/delete", json={"path": "index.html"}).status_code == 200
    assert not (folder / "index.html").exists()
    assert list((folder / ".agent_memory" / "trash").rglob("index.html"))
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"{P}/zip").content))
    assert archive.namelist() == ["src/app.js"]


def test_folders_and_renames(setup: Any) -> None:
    client, folder = setup
    assert client.post(f"{P}/mkdir", json={"path": "docs"}).status_code == 200
    assert "docs" in client.get(f"{P}/tree").json()["folders"]
    assert client.post(f"{P}/rename", json={"path": "src/app.js", "to": "src/main.js"}).json()["path"] == "src/main.js"
    assert (folder / "src/main.js").exists()
    assert client.post(f"{P}/rename", json={"path": "src/main.js", "to": "../../x.js"}).status_code == 404
