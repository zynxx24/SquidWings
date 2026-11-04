"""The chat skills: that they reach the prompt for real messages, and fit what cheap models are given."""

from __future__ import annotations

import pytest

from agent.harness.briefing import skill_guidance
from agent.harness.tiers import scaffold_for
from brain import skills
from config.constants import TaskType

LITE = scaffold_for("prov/glm-5-3-flash")


@pytest.mark.parametrize("message", [
    "capek bgt gw, kerja lembur mulu", "aku habis putus sama pacar", "lagi galau nih, mau curhat",
    "gw ngerasa kesepian akhir akhir ini",
])
def test_a_person_venting_gets_the_friend_guide(message):
    text, chosen = skill_guidance(message, TaskType.ACTIVITY, LITE.skill_chars)
    assert "teman-curhat" in chosen and "Never say" in text


@pytest.mark.parametrize("message", [
    "jelasin kenapa daun hijau buat tugas ipa smp", "bantu bikin makalah tentang banjir", "tolong rangkum materi sejarah ini",
])
def test_schoolwork_gets_the_homework_guide(message):
    text, chosen = skill_guidance(message, TaskType.SCHOOL_TASK, LITE.skill_chars)
    assert "jawab-tugas" in chosen and "Phrases that give AI away" in text


def test_a_coding_request_does_not_get_the_chat_guides():
    _, chosen = skill_guidance("perbaiki bug di fungsi login", TaskType.CODE, LITE.skill_chars)
    assert "fix-bug" in chosen and "teman-curhat" not in chosen


@pytest.mark.parametrize("message", ["bingung kenapa error ini", "soal bug ini gimana", "bikin pr buat fix login"])
def test_chat_words_in_a_coding_request_do_not_load_the_chat_guides(message):
    _, chosen = skill_guidance(message, TaskType.CODE, LITE.skill_chars)
    assert not {"teman-curhat", "jawab-tugas"} & set(chosen)


def test_every_core_guide_fits_the_cheap_model_budget_with_a_second_one():
    loaded = [s for s in skills.load_all().values() if s.scope == "core"]
    biggest = sorted((len(s.body) for s in loaded), reverse=True)[:2]
    assert sum(biggest) <= LITE.skill_chars


def test_every_core_guide_says_how_to_think():
    for skill in skills.load_all().values():
        if skill.scope == "core":
            assert "How to think" in skill.body, skill.name


def test_the_safety_rule_is_in_the_friend_guide():
    assert "119 extension 8" in skills.load_all()["teman-curhat"].body
