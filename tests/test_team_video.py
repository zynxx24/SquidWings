"""Video teams: they must end with a video, or say exactly why not. Offline, except one real render."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from agent.harness import RunLimits
from agent.harness.tiers import scaffold_for
from agent.team_orchestrator import (
    _OLD_VIDEO_INSTRUCTIONS,
    REPORTS_DIR,
    VIDEO_INSTRUCTIONS,
    VIDEO_SKILL,
    TeamOrchestrator,
    TeamStore,
    team_templates,
)
from tests.test_teams_flow import finish, make, member

KIT = Path(__file__).resolve().parent.parent / "brain" / "skills" / "animate"


# ---------------------------------------------------------------- limits

def test_video_limits_lift_a_cheap_models_caps():
    # Regression: a flash model got 20 rounds, a 1,500-char skill guide and 6,000-char reads,
    # and spent the whole budget re-reading the kit.
    lite = scaffold_for("vky/deepseek/deepseek-v4.1-flash-1017")
    video = RunLimits(max_rounds=40, tokens=400_000, deadline=time.time() + 60, override_tier=True,
                      context_chars=60_000, tool_result_chars=16_000, max_tools=16, skill_chars=7_000)
    applied = video.apply(lite)
    assert applied.max_iterations == 40 > lite.max_iterations
    assert applied.skill_chars == 7_000 and applied.tool_result_chars == 16_000
    assert applied.max_tools == 16 and applied.advisor_calls == 0


def test_ordinary_limits_still_only_shrink():
    lite = scaffold_for("x-flash")
    applied = RunLimits(max_rounds=40, tokens=1, deadline=0).apply(lite)
    assert applied.max_iterations == lite.max_iterations
    assert applied.skill_chars <= lite.skill_chars


def test_budget_share_counts_tokens_rounds_and_time():
    now = time.time()
    limits = RunLimits(max_rounds=10, tokens=1000, deadline=now + 100, started_at=now - 100)
    assert limits.used(0, 0, 10) == pytest.approx(0.5, abs=0.02)      # half the time gone
    assert limits.used(900, 0, 10) == pytest.approx(0.9)
    assert limits.used(0, 8, 10) == pytest.approx(0.8)


async def test_team_members_get_video_limits(tmp_path):
    manager, fake = make(tmp_path)
    team = manager.create_team("t", "", [member("lead"), member("a")], "lead", mode="video")
    await finish(manager, team.id)
    worker = next(c["limits"] for c in fake.calls if c["session_id"].endswith("worker"))
    assert worker.override_tier and worker.skill_chars >= 7_000 and worker.started_at > 0


# ---------------------------------------------------------------- template and old teams

def test_template_uses_the_short_path():
    video = next(t for t in team_templates(["m-flash", "claude-sonnet"]) if t["key"] == "video-studio")
    steps = {m["id"]: m["instructions"] for m in video["members"]}
    assert steps == VIDEO_INSTRUCTIONS
    assert "templates/quick" in steps["animator"] and "QUICKSTART.md" in steps["animator"]
    assert "jangan berhenti" in steps["animator"] and "kesempatan terakhir" in steps["editor"]


@pytest.mark.parametrize("version", [0, 1])
def test_old_template_teams_get_the_new_steps_and_edited_ones_do_not(tmp_path, version):
    folder = tmp_path / "teams"
    folder.mkdir()
    old = [{"id": key, "name": key, "role": "r", "model_id": "m", "instructions": texts[version], "depends_on": []}
           for key, texts in _OLD_VIDEO_INSTRUCTIONS.items()]
    old[1]["instructions"] += " Pakai gaya pixel."      # the user edited the animator
    (folder / "teams.json").write_text(json.dumps([{"id": "team-v", "name": "Studio", "description": "", "members": old,
                                                     "lead_id": "editor", "mode": "video"}]), encoding="utf-8")
    store = TeamStore(folder)
    steps = {m.id: m.instructions for m in store.get_team("team-v").members}
    assert steps["script"] == VIDEO_INSTRUCTIONS["script"]
    assert steps["editor"] == VIDEO_INSTRUCTIONS["editor"]
    assert steps["animator"].endswith("Pakai gaya pixel.")
    saved = json.loads((folder / "teams.json").read_text(encoding="utf-8"))
    assert saved[0]["members"][0]["instructions"] == VIDEO_INSTRUCTIONS["script"]


# ---------------------------------------------------------------- reports and the reason

async def test_reports_are_saved_for_later_members(tmp_path):
    from agent.session_stack import session_workspace

    manager, fake = make(tmp_path, {"-script-": ["BRIEF: api di gambut"]})
    team = manager.create_team("t", "", [member("script"), member("animator", "script"), member("lead")], "lead")
    run = await finish(manager, team.id)
    saved = session_workspace(run.id) / REPORTS_DIR / "script.md"
    assert "BRIEF: api di gambut" in saved.read_text(encoding="utf-8")
    animator = next(c for c in fake.calls if "-animator-" in c["session_id"])
    assert f"{REPORTS_DIR}/script.md" in animator["workspace_context"]


async def test_a_video_run_without_video_says_why_per_member(tmp_path, monkeypatch):
    monkeypatch.setattr(TeamOrchestrator, "_render_leftover",
                        staticmethod(lambda workspace, run_id: (None, "belum ada piece yang ditulis.")))
    manager, _ = make(tmp_path, {"-a-": ["## Laporan\nBelum ada satu frame pun yang dibangun karena batas habis."]})
    team = manager.create_team("t", "", [member("a"), member("lead")], "lead", mode="video")
    run = await finish(manager, team.id)
    assert run.status == "failed"
    assert "A: Belum ada satu frame pun" in run.error
    assert "Render otomatis: belum ada piece" in run.error
    assert "Cek log render animator" not in run.error


async def test_a_leftover_piece_is_rendered_and_shared(tmp_path, monkeypatch):
    from utils import shared_files

    monkeypatch.setattr(TeamOrchestrator, "_render_leftover",
                        staticmethod(lambda workspace, run_id: ("pieces/x/renders/share.mp4", "")))
    manager, _ = make(tmp_path)
    team = manager.create_team("t", "", [member("a"), member("lead")], "lead", mode="video")
    run = await finish(manager, team.id)
    assert run.status == "completed", run.error
    from agent.session_stack import session_workspace

    assert "pieces/x/renders/share.mp4" in shared_files.shared(session_workspace(run.id))


def test_leftover_render_explains_a_missing_kit_or_piece(tmp_path):
    assert TeamOrchestrator._render_leftover(tmp_path, "run-1")[1].startswith("kit animate belum dipasang")
    kit = tmp_path / ".skills" / "run-1" / VIDEO_SKILL / "tools"
    kit.mkdir(parents=True)
    (kit / "export.mjs").write_text("", encoding="utf-8")
    assert "belum ada piece" in TeamOrchestrator._render_leftover(tmp_path, "run-1")[1]


def _quick_piece(root: Path, kit: Path, seconds: float = 1.5) -> Path:
    piece = root / "pieces" / "karhutla"
    shutil.copytree(kit / "templates" / "quick", piece)
    head = (piece / "src" / "head.html").read_text(encoding="utf-8").replace("GANTI", "Hutan")
    head = head.replace("const DURATION = 4.5;", f"const DURATION = {seconds};")
    (piece / "src" / "head.html").write_text(head, encoding="utf-8")
    scenes = (piece / "src" / "scenes.js").read_text(encoding="utf-8").replace("GANTI", "Api di gambut")
    (piece / "src" / "scenes.js").write_text(scenes, encoding="utf-8")
    meta = json.loads((piece / "piece.json").read_text(encoding="utf-8"))
    meta.update(name="karhutla", title="Karhutla")
    meta["format"].update(duration=seconds, frames=round(seconds * 24))
    (piece / "piece.json").write_text(json.dumps(meta), encoding="utf-8")
    return piece


def _installed_kit(root: Path) -> Path:
    kit = root / ".skills" / "run-1" / VIDEO_SKILL
    shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns("examples", "__pycache__"))
    return kit


def test_a_copied_demo_or_template_is_never_rendered(tmp_path):
    kit = _installed_kit(tmp_path)
    demo = tmp_path / "pieces" / "x"
    shutil.copytree(kit / "styles" / "cut-paper" / "demo", demo)
    assert TeamOrchestrator._unfinished_piece(demo, kit)
    template = tmp_path / "pieces" / "y"
    shutil.copytree(kit / "templates" / "quick", template)
    assert TeamOrchestrator._unfinished_piece(template, kit)          # still says GANTI
    video, note = TeamOrchestrator._render_leftover(tmp_path, "run-1")
    assert video is None and "salah subjek" in note
    assert not TeamOrchestrator._unfinished_piece(_quick_piece(tmp_path, kit), kit)


def test_a_full_disk_is_named_before_rendering(tmp_path, monkeypatch):
    import agent.team_orchestrator as team_module

    kit = _installed_kit(tmp_path)
    piece = _quick_piece(tmp_path, kit, seconds=24)
    monkeypatch.setattr(team_module, "free_bytes", lambda path: 140 * 1024)
    note = TeamOrchestrator._disk_short(piece)
    assert "disk hampir penuh" in note and "576 frame" in note
    assert TeamOrchestrator._render_leftover(tmp_path, "run-1") == (None, note)


async def test_a_video_run_stops_at_once_on_a_full_disk(tmp_path, monkeypatch):
    import agent.team_orchestrator as team_module

    monkeypatch.setattr(team_module, "free_bytes", lambda path: 300 * 2**20)
    manager, fake = make(tmp_path)
    team = manager.create_team("t", "", [member("a"), member("lead")], "lead", mode="video")
    run = await finish(manager, team.id)
    assert run.status == "failed" and "Disk tinggal 300 MB" in run.error
    assert fake.calls == [], "no model call may be spent on a run that cannot render"


def test_quickstart_names_only_real_kit_functions():
    import re

    text = (KIT / "QUICKSTART.md").read_text(encoding="utf-8")
    code = "\n".join(p.read_text(encoding="utf-8") for p in [KIT / "kit" / "core.js", KIT / "styles" / "cut-paper" / "kit.js"])
    for name in ["cut", "rect", "ellipsePts", "capsulePts", "arcPts", "spark", "person", "cat", "clock", "mug",
                 "plant", "books", "sparkle", "face", "tag", "yearTag", "capStrip", "ink", "popS"]:
        assert f"`{name}(" in text, name
        assert re.search(rf"function {name}\(|const {name} = ", code), f"{name} is not in the kit"


@pytest.mark.skipif(not shutil.which("node") or not shutil.which("ffmpeg"), reason="needs node and ffmpeg")
def test_leftover_render_really_renders_a_piece(tmp_path, monkeypatch):
    """The real kit, the real sandbox: the quick template, filled in, becomes share.mp4 and leaves no frames."""
    import agent.team_orchestrator as team_module
    from security import sandbox

    monkeypatch.setattr(team_module, "free_bytes", lambda path: shutil.disk_usage(path).free)   # the real disk
    try:
        sandbox.run_blocking(["node", "--version"], ".", root=tmp_path, timeout=30)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"sandbox cannot run node here: {exc}")
    kit = _installed_kit(tmp_path)
    piece = _quick_piece(tmp_path, kit)
    if TeamOrchestrator._disk_short(piece):
        pytest.skip("not enough free disk for the frames")
    video, note = TeamOrchestrator._render_leftover(tmp_path, "run-1")
    if video is None and ("playwright" in note.lower() or "chromium" in note.lower()):
        pytest.skip(f"no browser for the renderer here: {note}")
    assert video == "pieces/karhutla/renders/share.mp4", note
    assert (tmp_path / video).stat().st_size > 50_000
    assert not (piece / "frames").exists(), "frames must be deleted after the render"
