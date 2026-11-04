"""Google Maps results into the session vault: contacts are removed unless asked for, and the notes
land in the session's own vault folder."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from knowledge import gmaps_import as gmaps

CSV = (
    "title,category,address,website,phone,emails,review_rating,review_count,latitude,longitude,place_id\n"
    "Klinik Gigi Sehat,Dentist,Jl. Merdeka 1 Jakarta,https://www.sehatgigi.example/?utm=x,021-555-0101,"
    "info@sehatgigi.example,4.7,220,-6.2,106.8,ChIJabc\n"
    ",Dentist,no name row,,,,,,,,\n"
    "Dental Care Plus,Dentist,Jl. Sudirman 9,,0812-0000-1111,,4.2,31,-6.21,106.81,ChIJdef\n"
)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    from tool.execute.execute_local_memory import vault_tool
    monkeypatch.setattr(vault_tool, "get_active_workspace", lambda: str(tmp_path))
    return tmp_path


def test_contacts_are_removed_by_default(tmp_path):
    src = tmp_path / "out.csv"
    src.write_text(CSV, encoding="utf-8")
    rows = gmaps.read_results(src)
    place = gmaps.normalize(rows[0])
    assert "phone" not in place and "emails" not in place
    assert place["website_domain"] == "sehatgigi.example"       # no tracking query string
    assert place["rating"] == 4.7 and place["reviews"] == 220


def test_contacts_only_with_explicit_opt_in(tmp_path):
    src = tmp_path / "out.csv"
    src.write_text(CSV, encoding="utf-8")
    place = gmaps.normalize(gmaps.read_results(src)[0], keep_contacts=True)
    assert place["phone"] == "021-555-0101" and place["emails"] == "info@sehatgigi.example"


def test_rows_without_a_name_are_skipped(tmp_path):
    src = tmp_path / "out.csv"
    src.write_text(CSV, encoding="utf-8")
    assert gmaps.normalize(gmaps.read_results(src)[1]) is None


def test_json_results_are_read_too(tmp_path):
    src = tmp_path / "out.json"
    src.write_text(json.dumps([{"title": "Cafe A", "category": "Cafe", "review_rating": 4.1}]), encoding="utf-8")
    assert gmaps.normalize(gmaps.read_results(src)[0])["name"] == "Cafe A"


def test_note_path_is_safe_and_grouped_by_query():
    path, body = gmaps.note_for({"name": "Klinik Gigi Sehat!", "category": "Dentist"}, "Dentists in Berlin")
    assert path == "places/dentists-in-berlin/klinik-gigi-sehat"
    assert "Phone" not in body


def test_import_writes_notes_into_the_session_vault(tmp_path, workspace):
    src = tmp_path / "out.csv"
    src.write_text(CSV, encoding="utf-8")
    res = asyncio.run(gmaps.import_results(src, query="dentist jakarta", workspace=str(workspace)))
    assert res["written"] == 2 and res["skipped"] == 1 and res["contacts_kept"] is False
    vault = Path(workspace) / ".vault"
    notes = list(vault.rglob("*.md"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in notes)
    assert "Klinik Gigi Sehat" in text and "Dental Care Plus" in text
    assert "021-555-0101" not in text and "info@sehatgigi.example" not in text
    assert "Scrape summary" in text
