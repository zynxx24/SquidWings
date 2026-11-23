"""Generated design directions: readable palettes, reproducible ids, real variety."""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from tool.execute.execute_toolbox import design_genome as g


@pytest.fixture(autouse=True)
def _history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STYLE_HISTORY_FILE", str(tmp_path / "style_history.json"))


def test_every_generated_palette_is_readable() -> None:
    rng = random.Random(3)
    for _ in range(400):
        d = g.build(g.new_id("", rng), "web", avoid_recent=False)
        c = d["contrast"]
        assert c["text_on_bg"] >= 4.5 and c["muted_on_bg"] >= 4.5 and c["accent_on_bg"] >= 3
        assert c["ink_on_accent"] >= 4.5 and c["link_on_bg"] >= 4.5 and c["text_on_surface"] >= 4.5
        if not d["palette_notes"].startswith("bold"):
            assert c["text_on_bg"] >= 7


def test_an_id_is_the_same_look_in_every_medium() -> None:
    web = g.build("g03-5a1f9c", "web", avoid_recent=False)
    again = g.build("g03-5a1f9c", "web", avoid_recent=False)
    slides = g.build("g03-5a1f9c", "slides", avoid_recent=False)
    assert web == again
    assert web["palette"] == slides["palette"] and web["type"] == slides["type"]
    assert slides["office_theme"] and slides["accent_color"] == web["palette"]["accent"]


def test_font_links_ask_only_for_weights_a_family_has() -> None:
    url = g.fonts_css({"heading": "Anton", "body": "Barlow Condensed", "hw": "400", "bw": "400;600"})
    assert "family=Anton:wght@400&" in url and "Barlow+Condensed:wght@400;600" in url


def test_the_ai_default_purple_blue_is_rare() -> None:
    rng = random.Random(11)
    full_range = [i for i, p in enumerate(g.personalities()) if p.get("hues") == [[0, 360]]]
    hues = [g.build(f"g{rng.choice(full_range):02d}-{rng.randrange(1 << 24):06x}", "web", avoid_recent=False)
            for _ in range(300)]
    share = sum(1 for d in hues if 255 <= int(re.search(r"hue (\d+)", d["palette_notes"]).group(1)) <= 295) / len(hues)
    assert share < 0.06   # a uniform pick would land there about 11% of the time


def test_style_pick_takes_a_genome_id_and_still_knows_the_classics() -> None:
    from tool.execute.execute_toolbox.style_tools import style_pick, styles

    picked = style_pick("web", style="g05-00beef")
    assert picked["style"] == "g05-00beef" and picked["source"] == "genome" and picked["fonts_css"].startswith("https://")
    classic = styles()[0]["id"]
    assert style_pick("web", style=classic)["style"] == classic


def test_design_explore_writes_a_sheet_of_distinct_options(tmp_path: Path) -> None:
    from tool.execute.execute_toolbox.style_tools import design_explore

    out = design_explore("web", "warung kopi di Bandung", 9, workspace_path=str(tmp_path))
    page = (tmp_path / out["file"]).read_text(encoding="utf-8")
    assert out["count"] == 9 and page.count('class="card"') == 9
    fonts = [o["name"].split(" · ")[1] for o in out["options"]]
    assert len(set(fonts)) == 9


def test_there_are_millions_of_combinations_before_any_palette() -> None:
    assert g.combinations("web") > 1_000_000
