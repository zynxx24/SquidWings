"""Design work gets tools and a fresh look: the image route no longer strips tools, and styles vary across chats."""

from __future__ import annotations

import random
import re

import pytest

from brain.classifier.intent_classifier import IntentClassifier
from config.constants import TaskType
from tool.execute.execute_toolbox import CATALOG, _themes, style_tools
from utils import style_history


@pytest.fixture(autouse=True)
def fresh_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STYLE_HISTORY_FILE", str(tmp_path / "history.json"))


# ── Classifier: UI work and existing pictures are not "generate an image" ─────
@pytest.mark.parametrize("text", [
    "desain landing page untuk warung kopi",
    "buatkan desain ui dashboard admin",
    "design a pricing page",
    "ada gambar di pdf ini, tolong baca",
    "edit foto ini jadi hitam putih",
    "buat logo svg untuk toko roti",
])
def test_ui_work_and_existing_pictures_are_not_image_generation(text):
    assert IntentClassifier().classify(text).task_type != TaskType.GENERATE_IMG


@pytest.mark.parametrize("text", ["buat gambar kucing astronot", "generate image of a sunset", "desain poster lomba 17 agustus"])
def test_real_picture_requests_stay_image_requests(text):
    assert IntentClassifier().classify(text).task_type == TaskType.GENERATE_IMG


def test_an_image_request_gets_tools_to_draw_save_and_share():
    from agent.harness.tiers import _SCAFFOLDS as SCAFFOLDS
    from agent.harness.tiers import Tier
    from agent.harness.tool_select import select_tools

    names = {"style_pick", "file_write", "execute_command", "share_file", "read_file", "calc"}
    schemas = [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
               for n in names]
    offered = {s["function"]["name"] for s in select_tools(schemas, TaskType.GENERATE_IMG, "buat gambar kucing astronot",
                                                         SCAFFOLDS[Tier.LITE])}
    assert {"style_pick", "file_write", "execute_command", "share_file"} <= offered


# ── style_pick ─────────────────────────────────────────────────────────────────
def test_every_medium_has_many_directions_and_complete_entries():
    entries = style_tools.styles()
    assert len(entries) >= 30
    for medium in style_tools.MEDIA:
        assert sum(medium in s["media"] for s in entries) >= (7 if medium == "document" else 20), medium
    hex_colour = re.compile(r"#[0-9A-Fa-f]{6}")
    for s in entries:
        assert s["palettes"] and all(all(hex_colour.fullmatch(v) for v in p.values()) for p in s["palettes"]), s["id"]
        assert {"heading", "body"} <= set(s["type"]) and s["layout"] and s["avoid"], s["id"]
        if {"slides", "document"} & set(s["media"]):
            assert s["office_theme"] in _themes.THEMES, s["id"]


def test_consecutive_picks_never_repeat_a_recent_look():
    seen = [CATALOG["style_pick"].func(medium="landing page", subject="toko online")["style"] for _ in range(8)]
    assert len(set(seen)) == 8


def test_history_is_shared_across_chats_not_kept_per_chat():
    first = CATALOG["style_pick"].func(medium="web")["style"]
    assert first in style_history.recent("web")
    # Another chat (no workspace at all) still skips it.
    pool = [style_tools.choose("web", rng=random.Random(seed))["id"] for seed in range(40)]
    assert first not in pool


def test_the_subject_steers_the_direction():
    picks = [style_tools.choose("web", "kedai kopi dan roti", rng=random.Random(seed))["id"] for seed in range(60)]
    fitting = {s["id"] for s in style_tools.styles() if style_tools._fit(s, "kedai kopi dan roti")}
    assert sum(p in fitting for p in picks) > len(picks) / 2


def test_avoid_and_style_work_and_slides_get_an_office_theme():
    out = CATALOG["style_pick"].func(medium="pptx", subject="seminar sejarah", style="arsip-museum")
    assert out["ok"] and out["style"] == "arsip-museum" and out["office_theme"] == "arsip"
    again = CATALOG["style_pick"].func(medium="slides", avoid=["arsip-museum"])
    assert again["style"] != "arsip-museum"
    web = CATALOG["style_pick"].func(medium="web", style="risograph")
    assert web["fonts_css"].startswith("https://fonts.googleapis.com/css2?family=Rubik+Mono+One")


def test_medium_words_are_understood():
    assert style_tools.medium_of("landing page") == "web"
    assert style_tools.medium_of("presentasi pptx") == "slides"
    assert style_tools.medium_of("poster lomba") == "image"
    assert style_tools.medium_of("reels tiktok") == "video"
    assert style_tools.medium_of("laporan word") == "document"


# ── Office themes ──────────────────────────────────────────────────────────────
def test_twenty_office_themes_and_a_new_chat_skips_other_chats_recent_ones(tmp_path):
    assert len(_themes.THEMES) == 20
    chat_a = tmp_path / "a"
    chat_a.mkdir()
    used = {_themes.pick(None, str(chat_a)).name for _ in range(5)}
    history = tmp_path / "history.json"
    saved = history.read_text()
    for seed in range(20):
        history.write_text(saved)
        chat_b = tmp_path / f"b{seed}"
        chat_b.mkdir()
        assert _themes.pick(None, str(chat_b), rng=random.Random(seed)).name not in used
