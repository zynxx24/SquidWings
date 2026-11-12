"""Bulk local file tools: find, read many, copy, replace across files, zip, media."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from tool.execute.execute_local_ops.power_tools import (
    archive,
    file_copy,
    file_find,
    file_read_many,
    file_replace_all,
    media_convert,
)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "renders" / "old").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / ".agent_memory").mkdir()
    (root / "renders" / "a.mp4").write_bytes(b"\x00" * 2000)
    (root / "renders" / "old" / "b.mp4").write_bytes(b"\x00" * 10)
    os.utime(root / "renders" / "old" / "b.mp4", (time.time() - 7200, time.time() - 7200))
    (root / "node_modules" / "pkg" / "c.mp4").write_bytes(b"x")
    (root / ".agent_memory" / "d.mp4").write_bytes(b"x")
    (root / ".agent_memory" / "state.json").write_text('{"secret": 1}')
    (root / "src" / "app.py").write_text("name = 'old'\nprint(name)  # old\n")
    (root / "src" / "util.py").write_text("x = 'old'\n")
    (root / "src" / "notes.txt").write_text("old\n")
    return root


def w(ws: Path) -> str:
    return str(ws)


# --- file_find -----------------------------------------------------------


async def test_find_globs_newest_first_and_skips_noise(ws: Path) -> None:
    result = await file_find(pattern="**/*.mp4", workspace_path=w(ws))
    assert result["ok"]
    assert [r["path"] for r in result["results"]] == ["renders/a.mp4", "renders/old/b.mp4"]
    assert result["results"][0]["size"] == 2000


async def test_find_filters(ws: Path) -> None:
    big = await file_find(pattern="*.mp4", min_size=100, workspace_path=w(ws))
    assert [r["path"] for r in big["results"]] == ["renders/a.mp4"]
    recent = await file_find(pattern="*.mp4", modified_within_minutes=30, workspace_path=w(ws))
    assert [r["path"] for r in recent["results"]] == ["renders/a.mp4"]
    named = await file_find(name_contains="UTIL", workspace_path=w(ws))
    assert [r["path"] for r in named["results"]] == ["src/util.py"]
    dirs = await file_find(pattern="old", kind="dir", workspace_path=w(ws))
    assert [r["path"] for r in dirs["results"]] == ["renders/old"]
    capped = await file_find(pattern="**/*", max_results=2, workspace_path=w(ws))
    assert capped["count"] == 2 and capped["truncated"]


async def test_find_refuses_outside_and_private(ws: Path) -> None:
    assert not (await file_find(path="..", workspace_path=w(ws)))["ok"]
    assert not (await file_find(path=".agent_memory", workspace_path=w(ws)))["ok"]


# --- file_read_many ------------------------------------------------------


async def test_read_many_reads_text_and_skips_binary(ws: Path) -> None:
    result = await file_read_many(paths=["src/app.py", "src/util.py", "renders/a.mp4"], workspace_path=w(ws))
    files = {f["path"]: f for f in result["files"]}
    assert files["src/app.py"]["content"].startswith("name = 'old'")
    assert files["src/util.py"]["ok"]
    assert files["renders/a.mp4"]["skipped"] == "binary" and "content" not in files["renders/a.mp4"]
    assert result["read"] == 2


async def test_read_many_caps_and_accepts_a_string(ws: Path) -> None:
    (ws / "big.txt").write_text("y" * 500)
    result = await file_read_many(paths="big.txt, src/util.py", max_chars_per_file=100, workspace_path=w(ws))
    assert result["files"][0]["truncated"] and len(result["files"][0]["content"]) == 100
    total = await file_read_many(paths=["big.txt", "src/util.py"], max_total_chars=500, workspace_path=w(ws))
    assert total["files"][1]["skipped"] == "total character limit reached"


async def test_read_many_refuses_private_and_outside(ws: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.txt").write_text("secret")
    result = await file_read_many(
        paths=[".agent_memory/state.json", "../outside.txt", str(tmp_path / "outside.txt")], workspace_path=w(ws)
    )
    assert result["read"] == 0
    assert all("content" not in f for f in result["files"])


# --- file_copy -----------------------------------------------------------


async def test_copy_file_and_tree(ws: Path) -> None:
    one = await file_copy(source="src/app.py", destination="backup/app.py", workspace_path=w(ws))
    assert one["ok"] and (ws / "backup" / "app.py").read_text() == (ws / "src" / "app.py").read_text()
    into = await file_copy(source="src/util.py", destination="backup", workspace_path=w(ws))
    assert into["destination"] == "backup/util.py"
    tree = await file_copy(source="src", destination="src_copy", workspace_path=w(ws))
    assert tree["ok"] and tree["files_copied"] == 3
    assert (ws / "src_copy" / "notes.txt").exists()


async def test_copy_refusals(ws: Path) -> None:
    again = await file_copy(source="src/app.py", destination="src/util.py", workspace_path=w(ws))
    assert not again["ok"] and "overwrite" in again["error"]
    assert (ws / "src" / "util.py").read_text() == "x = 'old'\n"
    forced = await file_copy(source="src/app.py", destination="src/util.py", overwrite=True, workspace_path=w(ws))
    assert forced["ok"] and forced["backup"]
    assert not (await file_copy(source="src", destination="src/inner", workspace_path=w(ws)))["ok"]
    assert not (await file_copy(source=".agent_memory/state.json", destination="s.json", workspace_path=w(ws)))["ok"]
    assert not (await file_copy(source="src/app.py", destination=".agent_memory/x.py", workspace_path=w(ws)))["ok"]
    assert not (await file_copy(source="src/app.py", destination="../escape.py", workspace_path=w(ws)))["ok"]
    assert not (ws.parent / "escape.py").exists()


async def test_copying_a_tree_leaves_memory_behind(ws: Path) -> None:
    (ws / "proj" / ".vault").mkdir(parents=True)
    (ws / "proj" / ".vault" / "note.md").write_text("private")
    (ws / "proj" / "main.py").write_text("x")
    result = await file_copy(source="proj", destination="proj2", workspace_path=w(ws))
    assert result["ok"] and result["files_copied"] == 1
    assert (ws / "proj2" / "main.py").exists() and not (ws / "proj2" / ".vault").exists()
    assert not (await file_copy(source=".", destination="sub/all", workspace_path=w(ws)))["ok"]


# --- file_replace_all ----------------------------------------------------


async def test_replace_dry_run_changes_nothing(ws: Path) -> None:
    before = (ws / "src" / "app.py").read_text()
    result = await file_replace_all(find="old", replace="new", glob="*.py", workspace_path=w(ws))
    assert result["ok"] and result["dry_run"]
    counts = {f["path"]: f["matches"] for f in result["files"]}
    assert counts == {"src/app.py": 2, "src/util.py": 1}
    assert result["files"][0]["samples"][0]["after"] == "name = 'new'"
    assert (ws / "src" / "app.py").read_text() == before


async def test_replace_applies_with_backup(ws: Path) -> None:
    result = await file_replace_all(find="old", replace="new", glob="src/*.py", dry_run=False, workspace_path=w(ws))
    assert result["ok"] and result["total_matches"] == 3
    assert (ws / "src" / "app.py").read_text() == "name = 'new'\nprint(name)  # new\n"
    assert (ws / "src" / "notes.txt").read_text() == "old\n"
    assert all(f["backup"] and (ws / f["backup"]).exists() for f in result["files"])


async def test_replace_regex_groups_and_literal_backslashes(ws: Path) -> None:
    regex = await file_replace_all(find=r"(\w+) = 'old'", replace=r"\1 = 'fresh'", glob="*.py",
                                   use_regex=True, dry_run=False, workspace_path=w(ws))
    assert regex["ok"] and (ws / "src" / "util.py").read_text() == "x = 'fresh'\n"
    literal = await file_replace_all(find="fresh", replace=r"a\1b", glob="util.py", dry_run=False,
                                     workspace_path=w(ws))
    assert literal["ok"] and (ws / "src" / "util.py").read_text() == "x = 'a\\1b'\n"


async def test_replace_never_touches_private_or_binary(ws: Path) -> None:
    result = await file_replace_all(find="secret", replace="x", glob="**/*", dry_run=False, workspace_path=w(ws))
    assert result["files_matched"] == 0
    assert "secret" in (ws / ".agent_memory" / "state.json").read_text()
    assert not (await file_replace_all(find="a", replace="b", glob="*", path=".agent_memory",
                                       workspace_path=w(ws)))["ok"]
    assert not (await file_replace_all(find="a", replace="b", glob="*", path="..", workspace_path=w(ws)))["ok"]


# --- archive -------------------------------------------------------------


async def test_zip_and_unzip_round_trip(ws: Path) -> None:
    made = await archive(action="zip", paths=["src", "renders/a.mp4"], archive_path="out/bundle.zip",
                         workspace_path=w(ws))
    assert made["ok"] and made["files"] == 4
    with zipfile.ZipFile(ws / "out" / "bundle.zip") as zf:
        assert "src/app.py" in zf.namelist() and "a.mp4" in zf.namelist()
    again = await archive(action="zip", paths=["src"], archive_path="out/bundle.zip", workspace_path=w(ws))
    assert not again["ok"]

    unpacked = await archive(action="unzip", archive_path="out/bundle.zip", destination="unpacked",
                             workspace_path=w(ws))
    assert unpacked["ok"] and (ws / "unpacked" / "src" / "app.py").read_text().startswith("name")
    clash = await archive(action="unzip", archive_path="out/bundle.zip", destination="unpacked",
                          workspace_path=w(ws))
    assert not clash["ok"] and clash["conflicts"]


async def test_zip_refuses_private_sources(ws: Path) -> None:
    assert not (await archive(action="zip", paths=[".agent_memory"], archive_path="m.zip", workspace_path=w(ws)))["ok"]
    whole = await archive(action="zip", paths=["."], archive_path="all.zip", workspace_path=w(ws))
    with zipfile.ZipFile(ws / "all.zip") as zf:
        assert not any(".agent_memory" in name for name in zf.namelist())
    assert whole["ok"]


@pytest.mark.parametrize("name", ["../evil.txt", "/abs/evil.txt", "a/../../evil.txt", "..\\evil.txt"])
async def test_unzip_refuses_zip_slip(ws: Path, name: str) -> None:
    with zipfile.ZipFile(ws / "bad.zip", "w") as zf:
        zf.writestr("fine.txt", "ok")
        zf.writestr(name, "pwned")
    result = await archive(action="unzip", archive_path="bad.zip", destination="out", workspace_path=w(ws))
    assert not result["ok"]
    assert not (ws / "out" / "fine.txt").exists()
    assert not (ws.parent / "evil.txt").exists()


async def test_unzip_refuses_symlinks_and_private_targets(ws: Path) -> None:
    link = zipfile.ZipInfo("link")
    link.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(ws / "link.zip", "w") as zf:
        zf.writestr(link, "/etc/passwd")
    assert "symlink" in (await archive(action="unzip", archive_path="link.zip", workspace_path=w(ws)))["error"]

    with zipfile.ZipFile(ws / "mem.zip", "w") as zf:
        zf.writestr(".agent_memory/state.json", "{}")
    assert not (await archive(action="unzip", archive_path="mem.zip", workspace_path=w(ws)))["ok"]
    assert "secret" in (ws / ".agent_memory" / "state.json").read_text()
    assert not (await archive(action="unzip", archive_path="link.zip", destination=".vault",
                              workspace_path=w(ws)))["ok"]


async def test_unzip_limits(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_local_ops import power_tools

    with zipfile.ZipFile(ws / "many.zip", "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.txt", "x" * 100)
    monkeypatch.setattr(power_tools, "_MAX_ARCHIVE_ENTRIES", 3)
    assert "entries" in (await archive(action="unzip", archive_path="many.zip", workspace_path=w(ws)))["error"]
    monkeypatch.setattr(power_tools, "_MAX_ARCHIVE_ENTRIES", 100)
    monkeypatch.setattr(power_tools, "_MAX_ARCHIVE_BYTES", 300)
    assert "MB" in (await archive(action="unzip", archive_path="many.zip", workspace_path=w(ws)))["error"]


# --- media_convert -------------------------------------------------------


async def test_media_convert_validates_before_running(ws: Path) -> None:
    (ws / "clip.mp4").write_bytes(b"\x00")
    wrong = await media_convert(source="clip.mp4", output="clip.gif", preset="mp4", workspace_path=w(ws))
    assert not wrong["ok"]
    outside = await media_convert(source="clip.mp4", output="../x.mp4", preset="mp4", workspace_path=w(ws))
    assert not outside["ok"]
    private = await media_convert(source="clip.mp4", output=".agent_memory/x.mp4", preset="mp4",
                                  workspace_path=w(ws))
    assert not private["ok"]
    (ws / "list.txt").write_text("file '/etc/passwd'")
    assert not (await media_convert(source="list.txt", output="o.mp4", preset="mp4", workspace_path=w(ws)))["ok"]


async def test_media_convert_without_ffmpeg(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_local_ops import power_tools

    from utils import toolkit

    monkeypatch.setattr(power_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(toolkit, "ffmpeg_exe", lambda: None)      # no system ffmpeg and no bundled copy
    result = await media_convert(source="renders/a.mp4", output="o.mp4", preset="mp4", workspace_path=w(ws))
    assert not result["ok"] and "apt install ffmpeg" in result["error"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_media_convert_real_ffmpeg(ws: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
         str(ws / "src.mkv")],
        check=True, timeout=60,
    )
    mp4 = await media_convert(source="src.mkv", output="renders/share.mp4", preset="mp4", workspace_path=w(ws))
    assert mp4["ok"], mp4
    assert (ws / "renders" / "share.mp4").stat().st_size == mp4["size"] > 0
    webp = await media_convert(source="src.mkv", output="renders/share.webp", preset="webp", fps=5,
                               workspace_path=w(ws))
    assert webp["ok"], webp
    assert (ws / "renders" / "share.webp").read_bytes()[:4] == b"RIFF"
    again = await media_convert(source="src.mkv", output="renders/share.mp4", preset="mp4", workspace_path=w(ws))
    assert not again["ok"] and "overwrite" in again["error"]


# --- registration --------------------------------------------------------


def test_new_tools_are_registered_and_offered() -> None:
    from agent.harness.tiers import scaffold_for
    from agent.harness.tool_select import select_tools
    from config.constants import TaskType
    from tool.register_tools import register_all_tools
    from tool.registry.tool_registry import WORKSPACE_TOOLS

    registry = register_all_tools()
    new = {"file_find", "file_read_many", "file_copy", "file_replace_all", "archive", "media_convert"}
    assert new <= set(registry._tools)
    assert new <= set(WORKSPACE_TOOLS)
    schemas = registry.get_all_schemas()
    lite = scaffold_for("x-flash")

    def offered(text: str) -> set[str]:
        return {s["function"]["name"] for s in select_tools(schemas, TaskType.ACTIVITY, text, lite)}

    assert "media_convert" in offered("ubah video ini jadi gif")
    assert "archive" in offered("unzip file ini")
    assert "file_replace_all" in offered("replace all foo with bar")
    assert "file_find" in offered("cari file mp4 terbaru")
    share = registry._tools["share_file"].schema.description
    assert "never start a web server" in share and "audio" in share
