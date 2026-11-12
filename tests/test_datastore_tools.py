"""The agent's database tools: exact edits with guards, and a customer-facing agent held to a stricter rule."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tool.execute.execute_toolbox import CATALOG, match
from utils import datastore
from utils.datastore import access, registry


def call(name: str, **kwargs: Any) -> dict[str, Any]:
    out = CATALOG[name].func(**kwargs)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    assert isinstance(out, dict) and "ok" in out, out
    return out


def ok(name: str, **kwargs: Any) -> dict[str, Any]:
    out = call(name, **kwargs)
    assert out["ok"], out
    return out


@pytest.fixture(autouse=True)
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path / "datastore")
    datastore.ensure_shop_starter()
    katalog, _ = datastore.open_source("katalog")
    katalog.add_column("", "_harga_modal")
    for kode, nama, harga, stok, modal in [
        ("HD-003", "Hoodie Fleece", 180000, 0, 90000),
        ("KS-004", "Kaos Hitam Oversize", 85000, 8, 40000),
    ]:
        katalog.insert("", {"kode": kode, "nama": nama, "kategori": "Baju", "harga": harga, "stok": stok, "_harga_modal": modal, "aktif": "ya"})
    katalog.update("", 1, {"_harga_modal": 30000})
    return katalog


def test_the_tools_are_in_the_hidden_toolbox_and_wake_on_their_words() -> None:
    for name in ("db_sources", "db_schema", "db_read", "db_search", "db_append", "db_update", "db_delete", "db_alter", "owner_notify"):
        assert name in CATALOG and CATALOG[name].group == "datastore"
    assert not CATALOG["db_read"].network and not CATALOG["db_update"].read_only and CATALOG["db_read"].read_only
    assert "db_update" in match("tolong ubah stok kaos hitam di katalog jadi 5")
    assert "db_search" in match("cari produk kaos hitam di katalog")


def test_read_and_search(shop: Any) -> None:
    listed = ok("db_sources")
    assert {s["source"] for s in listed["sources"]} == {"katalog", "pesanan"}
    schema = ok("db_schema", source="katalog", table="katalog")
    assert "kode" in schema["tables"][0]["columns"] and schema["sample"][0]["values"]["nama"] == "Kaos Polos Premium"
    out = ok("db_read", source="katalog", conditions=[{"column": "stok", "op": ">", "value": 0}], sort="harga", columns=["nama", "stok"])
    assert [r["values"]["nama"] for r in out["rows"]] == ["Tote Bag Kanvas", "Kaos Polos Premium", "Kaos Hitam Oversize"]
    assert out["rows"][0]["values"].keys() == {"nama", "stok"}
    by_word = ok("db_search", source="katalog", query="kaos item oversise")             # a typo still finds it
    assert by_word["rows"][0]["values"]["kode"] == "KS-004"
    assert ok("db_search", source="katalog", query="drone")["rows"] == []
    bad = call("db_read", source="katalog", where={"warna_x": "a"})
    assert bad["ok"] is False and "Kolom 'warna_x' tidak ada" in bad["error"] and "usage" in bad
    missing = call("db_read", source="tidak-ada")
    assert missing["ok"] is False and "Yang terdaftar" in missing["error"]


def test_a_data_file_in_the_workspace_works_without_registering(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "kontak.csv").write_text("nama,hp\nBudi,0812\nSiti,0813\n", encoding="utf-8")
    out = ok("db_read", source="data/kontak.csv", where={"nama": "siti"}, workspace_path=str(tmp_path))
    assert out["rows"][0]["values"] == {"nama": "Siti", "hp": "0813"}
    ok("db_update", source="data/kontak.csv", where={"nama": "Siti"}, values={"hp": "0899"}, workspace_path=str(tmp_path))
    assert "0899" in (tmp_path / "data" / "kontak.csv").read_text(encoding="utf-8")
    escape = call("db_read", source="../../etc/passwd.csv", workspace_path=str(tmp_path))
    assert escape["ok"] is False


def test_update_and_delete_are_guarded(shop: Any) -> None:
    refused = call("db_update", source="katalog", values={"stok": 0})                    # no filter: would hit every row
    assert refused["ok"] is False and "where" in refused["error"]
    nothing = call("db_update", source="katalog", where={"kode": "XXX"}, values={"stok": 1})
    assert nothing["ok"] is False and "Tidak ada baris" in nothing["error"]
    many = call("db_update", source="katalog", conditions=[{"column": "harga", "op": ">", "value": 1}], values={"stok": 1}, max_rows=2)
    assert many["ok"] is False and "4 baris cocok" in many["error"]
    done = ok("db_update", source="katalog", where={"kode": "KS-004"}, values={"stok": "5", "harga": "90.000"})
    assert done["changed_rows"] == 1 and done["changes"][0]["changed"]["stok"] == {"from": 8, "to": "5"}
    row = ok("db_read", source="katalog", where={"kode": "KS-004"})["rows"][0]["values"]
    assert row["stok"] == 5 and row["harga"] == 90000                                    # typed like the column
    gone = ok("db_delete", source="katalog", where={"kode": "HD-003"})
    assert gone["deleted_rows"] == 1 and gone["deleted"][0]["values"]["nama"] == "Hoodie Fleece"
    assert call("db_delete", source="katalog")["ok"] is False                           # no filter
    assert "3 baris cocok" in call("db_delete", source="katalog", conditions=[{"column": "harga", "op": ">", "value": 1}], max_rows=2)["error"]
    assert ok("db_delete", source="katalog", conditions=[{"column": "harga", "op": ">", "value": 1}])["deleted_rows"] == 3
    assert ok("db_read", source="katalog")["total"] == 0


def test_append_and_alter(shop: Any) -> None:
    added = ok("db_append", source="pesanan", rows=[{"nama": "Budi", "produk": "Kaos", "jumlah": 2}])
    assert added["added"] == 1
    assert call("db_append", source="pesanan", rows=[{"kolom_ngawur": 1}])["ok"] is False
    assert call("db_append", source="pesanan", rows=[])["ok"] is False
    ok("db_alter", source="pesanan", action="add_column", column="resi")
    assert "resi" in ok("db_schema", source="pesanan")["tables"][0]["columns"]
    ok("db_alter", source="pesanan", action="rename_column", column="resi", new_name="no_resi")
    ok("db_alter", source="pesanan", action="drop_column", column="no_resi")
    assert call("db_alter", source="pesanan", action="explode")["ok"] is False


def test_a_team_agent_only_sees_what_it_was_granted() -> None:
    with access.restrict(access.Access({"katalog": "read"})):
        assert [s["source"] for s in ok("db_sources")["sources"]] == ["katalog"]
        assert ok("db_read", source="katalog")["total"] == 4
        assert "tidak boleh mengubah" in call("db_update", source="katalog", where={"kode": "KS-004"}, values={"stok": 1})["error"]
        assert "tidak diberi akses" in call("db_read", source="pesanan")["error"]
        assert "tidak boleh menambah" in call("db_append", source="katalog", rows=[{"nama": "x"}])["error"]
    with access.restrict(access.Access({})):
        assert ok("db_sources")["sources"] == []


def test_the_customer_agent_cannot_change_rows_or_see_private_columns() -> None:
    customer = access.Access({"katalog": "write", "pesanan": "append"}, public=True, hidden={"katalog": ("deskripsi",)},
                             context="Budi, chat 5")
    with access.restrict(customer):
        schema = ok("db_schema", source="katalog", table="katalog")
        assert "_harga_modal" not in schema["tables"][0]["columns"] and "deskripsi" not in schema["tables"][0]["columns"]
        assert "_harga_modal" not in str(schema["sample"])
        read = ok("db_read", source="katalog")
        assert all("_harga_modal" not in r["values"] and "deskripsi" not in r["values"] for r in read["rows"])
        assert "_harga_modal" not in str(ok("db_search", source="katalog", query="kaos")["rows"])
        # It cannot even ask about a hidden column.
        assert "tidak tersedia" in call("db_read", source="katalog", conditions=[{"column": "_harga_modal", "op": "<", "value": 50000}])["error"]
        assert "tidak tersedia" in call("db_read", source="katalog", sort="_harga_modal")["error"]
        assert "tidak tersedia" in call("db_read", source="katalog", columns=["_harga_modal"])["error"]
        # A search word that only occurs in a hidden column finds nothing.
        assert ok("db_read", source="katalog", search="30000")["rows"] == []
        # Never change or delete, even though the node was granted write.
        assert "tidak boleh mengubah" in call("db_update", source="katalog", where={"kode": "KS-004"}, values={"harga": 1})["error"]
        assert "tidak boleh mengubah" in call("db_delete", source="katalog", where={"kode": "KS-004"})["error"]
        assert "tidak boleh mengubah" in call("db_alter", source="katalog", action="drop_table", table="katalog")["error"]
        # Orders: it may add but not read other customers' orders, and a formula becomes plain text.
        assert "tidak boleh membaca" in call("db_read", source="pesanan")["error"]
        # ... but it may learn the columns to fill in, without a glimpse of the rows already there.
        form = ok("db_schema", source="pesanan", table="pesanan")
        assert "alamat" in form["tables"][0]["columns"] and "sample" not in form and form["tables"][0]["rows"] is None
        ok("db_append", source="pesanan", rows=[{"nama": '=HYPERLINK("http://x","klik")', "produk": "Kaos"}])
    stored = datastore.open_source("pesanan")[0].read("").rows[0]["values"]
    assert stored["nama"].startswith("'=HYPERLINK")
    from openpyxl import load_workbook

    cell = load_workbook(registry.files_dir() / "pesanan.xlsx")["pesanan"]["C2"]
    assert cell.data_type != "f"                      # not a formula in the file either


def test_notifying_the_owner_is_limited_and_names_the_customer(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def fake_send(text: str, chat_id: str = "") -> int:
        sent.append(text)
        return 1

    monkeypatch.setattr("agent.team_outputs.send_telegram", fake_send)
    with access.restrict(access.Access(public=True, context="Budi (@budi), chat 5")):
        for _ in range(3):
            ok("owner_notify", message="Pelanggan minta nego harga")
        assert "tiga kali" in call("owner_notify", message="lagi")["error"]
    assert len(sent) == 3 and "Budi (@budi), chat 5" in sent[0] and "nego harga" in sent[0]
    assert call("owner_notify", message="   ")["ok"] is False
