"""Adapters for GitHub skills, the offline fallback, upstream files not named SKILL.md, and the
rotating design partner. No network: a local repo stands in for GitHub."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import skills
from skills import adapters, sources


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin"})


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "upstream"
    (repo / "skills" / "inliner").mkdir(parents=True)
    (repo / "skills" / "inliner" / "SKILL.md").write_text("---\nname: inliner\n---\nUPSTREAM INLINE BODY\n")
    (repo / "skills" / "longone").mkdir(parents=True)
    (repo / "skills" / "longone" / "SKILL.md").write_text("---\nname: longone\n---\n" + "UPSTREAM LONG " * 500)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "skill.md").write_text("---\nname: oddname\n---\nUPSTREAM ODD FILE\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    folder = tmp_path / "adapters"
    folder.mkdir()
    (folder / "inliner.md").write_text("---\nskill: inliner\n---\nOUR INLINE NOTE\n")
    (folder / "longone.md").write_text("---\nskill: longone\nupstream: guide\nfallback: true\n---\nOUR SHORT ROUTE\n")
    (folder / "ghost.md").write_text("---\nskill: ghost\nupstream: guide\nfallback: true\n---\nGHOST WORKS OFFLINE\n")

    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"sources": [
        {"repo": "someone/kit", "url": str(repo), "active": [
            {"name": "inliner", "path": "skills/inliner", "triggers": "inliner", "scope": "contextual"},
            {"name": "longone", "path": "skills/longone", "triggers": "longone", "scope": "contextual"},
            {"name": "oddname", "path": "pkg", "file": "skill.md", "triggers": "oddname", "scope": "contextual"},
        ]},
        {"repo": "nobody/absent", "url": str(tmp_path / "missing-repo"), "active": [
            {"name": "ghost", "path": "x", "triggers": "ghost", "scope": "contextual", "tools": "geocode"},
        ]},
    ]}), encoding="utf-8")
    monkeypatch.setattr(sources, "MANIFEST", manifest)
    monkeypatch.setattr(sources, "SOURCES_DIR", tmp_path / ".sources")
    monkeypatch.setattr(adapters, "ADAPTERS_DIR", folder)
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(tmp_path / "claude-skills"))
    sources.reload()
    yield tmp_path
    sources.reload()


def test_inline_adapter_goes_in_front_of_the_unchanged_upstream(world: Path):
    sources.sync()
    skill = sources.active()["inliner"]
    assert skill.adapted and skill.body.startswith("OUR INLINE NOTE")
    assert "UPSTREAM INLINE BODY" in skill.body
    upstream = (world / ".sources" / "someone__kit" / "skills" / "inliner" / "SKILL.md").read_text()
    assert "OUR INLINE NOTE" not in upstream, "the clone itself is never edited"


def test_guide_adapter_keeps_a_long_upstream_one_load_away(world: Path):
    sources.sync()
    skill = sources.active()["longone"]
    assert "UPSTREAM LONG" not in skill.body and skill.body.startswith("OUR SHORT ROUTE")
    assert 'file="SKILL.md"' in skill.body
    assert "SKILL.md" in skill.guides and "UPSTREAM LONG" in (skill.guide("SKILL.md") or "")


def test_an_upstream_file_with_another_name_is_found(world: Path):
    sources.sync()
    assert "UPSTREAM ODD FILE" in sources.active()["oddname"].body


def test_the_adapter_stands_in_while_the_clone_is_missing(world: Path):
    skill = sources.active()["ghost"]      # nobody/absent was never cloned
    assert skill.body == "GHOST WORKS OFFLINE" and skill.origin == "nobody/absent"
    assert skill.tools == ("geocode",) and skill.upstream_file is None
    assert "ghost" in skills.load_all()


def test_real_adapters_name_skills_listed_in_sources_json():
    listed = {a["name"] for s in json.loads(sources.MANIFEST.read_text())["sources"] for a in s.get("active", [])}
    found = adapters.all_adapters()
    assert {"google-maps", "graphify", "ponytail", "obsidian-markdown"} <= set(found)
    assert set(found) <= listed, "an adapter for a skill nobody lists would never be used"
    assert found["google-maps"].fallback and found["graphify"].upstream == "guide"


def test_google_maps_works_without_its_clone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(sources, "SOURCES_DIR", tmp_path / "empty")
    sources.reload()
    try:
        maps = skills.load_all()["google-maps"]
        assert "gmaps_import" in maps.body and "osm_nearby" in maps.tools
    finally:
        sources.reload()


# ─── design partner ───────────────────────────────────────────────────


def test_design_requests_take_turns_across_chats():
    picked = [skills.claim_design_partner("buatkan landing page kopi", "code", []) for _ in range(4)]
    members = {s.name for s in skills.pool_members(skills.DESIGN_POOL, "code")}
    assert set(picked) == members and len(set(picked)) == 4, picked


def test_a_chat_keeps_its_design_partner():
    first = skills.claim_design_partner("bikin website portfolio", "code", [])
    again = skills.claim_design_partner("lanjutkan, ubah warnanya", "code", [first]) or first
    assert again == first
    names = [s.name for s in skills.select("rapikan layout halaman web ini", active=[first], task="code")]
    assert first in names and sum(n in names for n in ("design-ui", "ui-ux-pro-max", "impeccable", "accessibility")) == 1


def test_naming_a_design_guide_picks_it():
    assert skills.design_partner("redesign tampilan toko online ini", "code").name == "impeccable"


def test_non_design_work_gets_no_partner():
    assert skills.design_partner("perbaiki bug login error 500", "code") is None
    assert skills.design_partner("aku lagi galau nih", "reason") is None


def test_ponytail_prompt_comes_from_the_upstream_skill(monkeypatch: pytest.MonkeyPatch):
    from brain.classifier.intent_classifier import TaskType
    from brain.system_prompt import base_system_prompt as bsp

    monkeypatch.setenv("PONYTAIL_TASKS", "code")
    manager = bsp.SystemPromptManager()
    text = manager._ponytail("ponytail_ultra.txt")
    skill = skills.load_all().get("ponytail")
    if skill is not None and skill.upstream_file is not None:
        assert "Penyesuaian SquidWings untuk Ponytail" in text and "level for this chat: ultra" in text
    else:
        assert text == manager._load_file("ponytail_ultra.txt")
    assert TaskType.CODE in bsp._ponytail_tasks()


def test_the_skill_index_stays_short_and_relevant(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKILL_INDEX_MAX", "3")
    text = skills.index_text([], "code", query="perbaiki bug login laravel error 500")
    listed = [line.split(":")[0][2:] for line in text.splitlines() if line.startswith("- ")]
    assert len(listed) == 3 and "fix-bug" in listed and "laravel" in listed
    assert "more skills fit less well" in text


def test_overlapping_triggers_count_once():
    from brain.harness.relevance import TriggerWeights

    weights = TriggerWeights({"web": ["landing page", "landing", "page"], "other": ["page"]})
    assert weights.score("web", "bikin landing page") == pytest.approx(weights.weight("landing page"))
