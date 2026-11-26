"""Apps get an app's look, and a page that ignores its direction or falls back to the stock template is caught."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from brain.harness import design_check
from brain.utils.workspace_context import set_active_workspace
from tools.toolbox import design_genome, style_tools


@pytest.fixture(autouse=True)
def fresh_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STYLE_HISTORY_FILE", str(tmp_path / "history.json"))


def test_a_dashboard_or_school_system_gets_an_app_direction_with_a_skeleton() -> None:
    picked = style_tools.style_pick(medium="web", subject="dashboard ppdb sekolah")
    assert picked["medium"] == "app" and picked["skeleton"].startswith(("body{", "main{"))
    assert "centred hero" in picked["structure"]
    assert style_tools.medium_of("aplikasi absensi laravel") == "app"
    assert style_tools.medium_of("landing page kedai kopi") == "web"


def test_app_screens_never_get_poster_or_handwritten_type() -> None:
    banned = {f["heading"] for f in design_genome.data()["fonts"] if set(f.get("tags") or []) & design_genome._NOT_FOR_UI}
    headings = {style_tools.style_pick(medium="app", subject="sistem kasir")["type"]["heading"] for _ in range(40)}
    assert banned and not headings & banned and len(headings) >= 6


def test_the_layout_follows_what_the_screen_is_for() -> None:
    layouts = {style_tools.style_pick(medium="app", subject="absensi siswa kamera selfie")["layout"].split(":")[0] for _ in range(12)}
    words = {"absensi", "siswa", "kamera", "selfie"}
    fitting = {x["name"] for x in design_genome.data()["layouts"]["app"] if words & set(x.get("fits") or [])}
    assert layouts and layouts <= fitting


def test_the_pick_is_noted_in_the_chats_folder(tmp_path: Path) -> None:
    set_active_workspace(tmp_path)
    try:
        picked = style_tools.style_pick(medium="app", subject="inventaris gudang", style="g03-5a1f9c")
    finally:
        set_active_workspace("")
    noted = json.loads((tmp_path / ".agent_memory" / "style.json").read_text())
    assert noted["style"] == "g03-5a1f9c" and noted["fonts"] == [picked["type"]["heading"], picked["type"]["body"]]
    assert picked["palette"]["accent"].lower() in [c.lower() for c in noted["colors"]]


def note(root: Path, fonts: list[str], colors: list[str], age: float = 0) -> None:
    (root / ".agent_memory").mkdir(parents=True, exist_ok=True)
    (root / ".agent_memory" / "style.json").write_text(json.dumps(
        {"style": "g01-abcdef", "medium": "app", "fonts": fonts, "colors": colors, "at": time.time() - age}))


def test_a_page_without_the_directions_fonts_and_colours_is_flagged(tmp_path: Path) -> None:
    note(tmp_path, ["Lexend", "Literata"], ["#1a5c4f", "#f7f3ea"])
    page = tmp_path / "index.html"
    page.write_text("<style>body{font-family:Arial;color:#333}</style><h1>Halo</h1>")
    found = design_check.findings(tmp_path, [page])
    assert len(found) == 2 and "Lexend / Literata" in found[0] and "css_tokens" in found[1]
    # The fonts set in another file of the project count.
    (tmp_path / "app.css").write_text(":root{--accent:#1A5C4F} body{font-family:'Lexend',sans-serif}")
    assert design_check.findings(tmp_path, [page]) == []


def test_the_stock_template_is_flagged_and_old_or_compiled_files_are_not(tmp_path: Path) -> None:
    page = tmp_path / "resources" / "views" / "welcome.blade.php"
    page.parent.mkdir(parents=True)
    page.write_text('<section class="bg-gradient-to-br from-indigo-600 to-purple-600">'
                    '<div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8"><h1 style="font-family: Inter">x</h1></div></section>')
    [finding] = design_check.findings(tmp_path, [page])
    assert "gradasi Tailwind bawaan" in finding and "style_pick" in finding
    compiled = tmp_path / "app.css"
    compiled.write_text("/*! tailwindcss v4 */ .a{} bg-gradient-to-br from-indigo-600 backdrop-blur")
    assert design_check.findings(tmp_path, [compiled]) == []
    assert design_check.findings(tmp_path, [tmp_path / "main.py"]) == []
    note(tmp_path, ["Lexend"], ["#123456"], age=design_check.DIRECTION_AGE + 60)     # yesterday's work: ignored
    assert design_check.direction(tmp_path) is None
