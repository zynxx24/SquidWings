"""The Data tab's HTTP API: sources, an editable grid, and the guards around changes."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import credentials
from utils.datastore import registry
from web.data_api import router
from web.router_api import admin
from web.team_api import router as team_router


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path / "datastore")
    monkeypatch.setenv("CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    from config.settings import get_settings

    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(router)
    app.include_router(team_router)
    yield TestClient(app, client=("127.0.0.1", 5000))
    get_settings.cache_clear()


def shop(api: TestClient) -> None:
    assert api.post("/api/data/starter/shop").status_code == 201


def rows(api: TestClient, **params: Any) -> dict[str, Any]:
    out = api.get("/api/data/sources/katalog/rows", params=params)
    assert out.status_code == 200, out.text
    return out.json()


def test_every_change_needs_the_pin_but_reading_does_not() -> None:
    open_routes = {"/api/data/sources/{ref}/check"}
    for route in router.routes:
        methods = getattr(route, "methods", set()) - {"GET", "HEAD"}
        if methods and route.path not in open_routes:                                  # type: ignore[attr-defined]
            guards = [d.call for d in route.dependant.dependencies]              # type: ignore[attr-defined]
            assert admin in guards, f"{route.path} changes data without the PIN"      # type: ignore[attr-defined]


def test_the_shop_starter_makes_two_sheets_once(api: TestClient) -> None:
    assert api.get("/api/data/sources").json()["sources"] == []
    made = api.post("/api/data/starter/shop").json()
    assert [s["name"] for s in made["created"]] == ["katalog", "pesanan"]
    assert api.post("/api/data/starter/shop").json()["created"] == []                  # a second call changes nothing
    listing = api.get("/api/data/sources").json()
    assert [s["name"] for s in listing["sources"]] == ["katalog", "pesanan"] and listing["sources"][0]["format"] == "xlsx"
    assert ".xlsx" in listing["formats"] and listing["ready"] == {"sheets": False, "postgres": False, "mysql": False, "mongodb": False}
    assert "Google Sheets" in listing["online"].values()


def test_reading_pages_sorting_and_searching(api: TestClient) -> None:
    shop(api)
    tables = api.get("/api/data/sources/katalog/tables").json()
    assert tables["positional"] is True and tables["tables"][0]["columns"][:3] == ["kode", "nama", "kategori"]
    page = rows(api, limit=1, sort="harga", desc=True)
    assert page["total"] == 2 and page["rows"][0]["values"]["nama"] == "Kaos Polos Premium" and page["key"] == ""
    assert rows(api, search="tote")["rows"][0]["values"]["kode"] == "TS-002"
    assert api.get("/api/data/sources/katalog/rows", params={"limit": 9999}).status_code == 422
    assert api.get("/api/data/sources/tidak-ada/rows").status_code == 404
    bad = api.get("/api/data/sources/katalog/rows", params={"sort": "kolom_ngawur"})
    assert bad.status_code == 422 and "kolom_ngawur" in bad.json()["detail"]


def test_add_edit_and_delete_a_row(api: TestClient) -> None:
    shop(api)
    added = api.post("/api/data/sources/katalog/rows", json={"values": {"kode": "HD-003", "nama": "Hoodie", "harga": "180.000", "stok": "7"}})
    assert added.status_code == 201 and added.json()["key"] == 3
    row = rows(api, search="hoodie")["rows"][0]
    assert row["key"] == 3 and row["values"]["harga"] == 180000 and row["values"]["stok"] == 7         # typed like the column
    ok = api.patch("/api/data/sources/katalog/rows", json={"key": 3, "values": {"stok": "5"}, "expect": {"nama": "Hoodie"}})
    assert ok.status_code == 200 and rows(api, search="hoodie")["rows"][0]["values"]["stok"] == 5
    api.post("/api/data/sources/katalog/rows", json={"values": {"kode": "AA-000", "nama": "Baris di atas"}, "at": 1})   # shifts every row down
    stale = api.patch("/api/data/sources/katalog/rows", json={"key": 3, "values": {"stok": "0"}, "expect": {"nama": "Hoodie"}})
    assert stale.status_code == 409 and "Muat ulang" in stale.json()["detail"]
    assert rows(api, search="hoodie")["rows"][0]["values"]["stok"] == 5                                # nothing was touched
    wrong = api.post("/api/data/sources/katalog/rows/delete", json={"keys": [4], "expect": [{"nama": "Bukan Hoodie"}]})
    assert wrong.status_code == 409
    gone = api.post("/api/data/sources/katalog/rows/delete", json={"keys": [4], "expect": [{"nama": "Hoodie"}]})
    assert gone.json() == {"deleted": 1} and rows(api)["total"] == 3
    assert api.post("/api/data/sources/katalog/rows/delete", json={"keys": [99]}).status_code == 422
    assert api.post("/api/data/sources/katalog/rows/delete", json={"keys": []}).status_code == 422
    assert api.patch("/api/data/sources/katalog/rows", json={"key": 1, "values": {"kolom_x": 1}}).status_code == 422


def test_columns_change_through_one_endpoint(api: TestClient) -> None:
    shop(api)
    added = api.post("/api/data/sources/pesanan/columns", json={"action": "add", "name": "resi"})
    assert added.json()["columns"][-1] == "resi"
    renamed = api.post("/api/data/sources/pesanan/columns", json={"action": "rename", "name": "resi", "new_name": "no_resi"})
    assert "no_resi" in renamed.json()["columns"]
    assert api.post("/api/data/sources/pesanan/columns", json={"action": "rename", "name": "no_resi"}).status_code == 422
    dropped = api.post("/api/data/sources/pesanan/columns", json={"action": "drop", "name": "no_resi"})
    assert "no_resi" not in dropped.json()["columns"]
    assert api.post("/api/data/sources/pesanan/columns", json={"action": "bakar", "name": "x"}).status_code == 422


def test_new_files_uploads_and_a_sqlite_file_with_tables(api: TestClient) -> None:
    made = api.post("/api/data/sources/new-file", json={"filename": "kontak.csv", "columns": ["nama", "hp"], "label": "Kontak"})
    assert made.status_code == 201 and made.json()["name"] == "kontak"
    assert api.post("/api/data/sources/new-file", json={"filename": "kontak.csv", "columns": ["nama"]}).status_code == 422   # name taken
    assert api.post("/api/data/sources/new-file", json={"filename": "../../etc/x.csv", "columns": ["a"]}).status_code in (201, 422)
    assert not (Path("/etc") / "x.csv").exists()
    raw = base64.b64encode(b"nama,hp\nBudi,0812\n").decode()
    up = api.post("/api/data/sources/import", json={"filename": "pelanggan.csv", "content": raw})
    assert up.status_code == 201
    got = api.get("/api/data/sources/pelanggan/rows").json()
    assert got["rows"][0]["values"] == {"nama": "Budi", "hp": "0812"}
    junk = api.post("/api/data/sources/import", json={"filename": "rusak.xlsx", "content": base64.b64encode(b"bukan excel").decode()})
    assert junk.status_code == 422 and not (registry.files_dir() / "rusak.xlsx").exists()
    assert api.post("/api/data/sources/import", json={"filename": "virus.exe", "content": raw}).status_code == 422
    lite = api.post("/api/data/sources/new-file", json={"filename": "stok.sqlite", "columns": ["item", "qty"]})
    assert lite.status_code == 201
    assert api.post("/api/data/sources/stok/tables", json={"table": "gudang", "columns": ["rak", "isi"]}).status_code == 201
    names = [t["name"] for t in api.get("/api/data/sources/stok/tables").json()["tables"]]
    assert names == ["data", "gudang"]
    assert api.post("/api/data/sources/stok/tables/drop", json={"table": "gudang"}).status_code == 200
    assert [t["name"] for t in api.get("/api/data/sources/stok/tables").json()["tables"]] == ["data"]


def test_download_rename_and_remove(api: TestClient) -> None:
    shop(api)
    file = api.get("/api/data/sources/katalog/download")
    assert file.status_code == 200 and file.content[:2] == b"PK"                      # an xlsx is a zip
    assert api.patch("/api/data/sources/katalog", json={"label": "Produk toko"}).json()["label"] == "Produk toko"
    assert api.patch("/api/data/sources/katalog", json={"name": "pesanan"}).status_code == 422            # name already used
    assert api.delete("/api/data/sources/katalog").json()["name"] == "katalog"
    assert (registry.files_dir() / "katalog.xlsx").exists()                           # the file stays unless asked
    assert api.delete("/api/data/sources/pesanan", params={"delete_file": True}).status_code == 200
    assert not (registry.files_dir() / "pesanan.xlsx").exists()
    assert api.get("/api/data/sources/katalog/download").status_code == 404


def test_online_sources_need_their_login_first(api: TestClient) -> None:
    no_key = api.post("/api/data/sources", json={"kind": "sheets", "location": "https://docs.google.com/spreadsheets/d/abc123XYZ_-abcdefghijk/edit"})
    assert no_key.status_code == 422 and "Kunci Google belum diisi" in no_key.json()["detail"]
    assert api.get("/api/data/sources").json()["sources"] == []                      # a source that cannot be reached is not kept
    no_url = api.post("/api/data/sources", json={"kind": "postgres", "name": "gudang"})
    assert no_url.status_code == 422 and "belum diisi" in no_url.json()["detail"]
    assert api.post("/api/data/sources", json={"kind": "oracle"}).status_code == 422
    assert api.post("/api/data/sources", json={"kind": "sheets", "location": "bukan link"}).status_code == 422


def test_the_google_key_can_be_tested_from_the_credentials_tab(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_datastore import make_account
    from utils.datastore.sheets import SheetsBackend

    empty = api.post("/api/teams/credentials/test", json={"group": "google"})
    assert empty.status_code == 422 and "Google" in empty.json()["detail"]
    account = make_account()
    credentials.get_store().update({"google_service_account": json.dumps({"type": "service_account", **account})})
    monkeypatch.setattr(SheetsBackend, "verify_account", lambda self: self.account["client_email"])
    ok = api.post("/api/teams/credentials/test", json={"group": "google"})
    assert ok.status_code == 200 and account["client_email"] in ok.json()["message"] and "Editor" in ok.json()["message"]
    assert "PRIVATE" not in ok.text
    assert api.get("/api/data/sources").json()["google_email"] == account["client_email"]
    assert api.get("/api/data/sources").json()["ready"]["sheets"] is True
