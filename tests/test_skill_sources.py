"""GitHub skill sources: clone, active vs stored, loader merge, Claude Code links. No network: a local repo stands in."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

import skills
from skills import sources


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin"})


def _skill(root: Path, name: str, body: str, description: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    desc = description or f"|\n  Does {name} things.\n  Second line."
    (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\nlicense: MIT\n---\n{body}\n", encoding="utf-8")


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    _skill(repo / "skills" / "alpha", "alpha", "Run ${CLAUDE_PLUGIN_ROOT}/skills/alpha/run.py and {baseDir}/ref.md")
    (repo / "skills" / "alpha" / "ref.md").write_text("reference", encoding="utf-8")
    _skill(repo / "skills" / "beta", "beta", "Beta body", description='"Quoted beta description"')
    _skill(repo / "skills" / "fix-bug", "fix-bug", "An upstream skill that clashes with a built-in")
    _skill(repo / "other" / "gamma", "gamma", "outside the sparse path")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def setup(tmp_path: Path, upstream: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({
        "claude_code_builtin": ["clean-code"],
        "sources": [{
            "repo": "someone/skills", "url": str(upstream), "paths": ["skills"],
            "active": [
                {"name": "alpha", "path": "skills/alpha", "description": "Alpha untuk tes.",
                 "triggers": "alpha", "scope": "contextual", "tasks": "code", "claude_code": True},
                {"name": "fix-bug", "path": "skills/fix-bug", "triggers": "bug"},
            ],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(sources, "MANIFEST", manifest)
    monkeypatch.setattr(sources, "SOURCES_DIR", tmp_path / ".sources")
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(tmp_path / "claude-skills"))
    sources.reload()
    yield tmp_path
    sources.reload()


def test_frontmatter_reads_block_and_quoted_values():
    fields, body = sources._frontmatter('---\nname: x\ndescription: |\n  one\n  two\nother: "q"\n---\nBody')
    assert fields == {"name": "x", "description": "one two", "other": "q"}
    assert body == "Body"


def test_sync_clones_sparse_and_sorts_active_from_stored(setup: Path):
    results = sources.sync()
    assert results == [{"repo": "someone/skills", "action": "cloned"}]
    folder = setup / ".sources" / "someone__skills"
    assert (folder / "skills" / "alpha" / "SKILL.md").exists()
    assert not (folder / "other").exists()            # sparse: only the listed path is checked out

    on, off = sources.active(), sources.stored()
    assert set(on) == {"alpha", "fix-bug"}
    assert set(off) == {"beta"}                      # not listed as active: stored
    assert off["beta"].description == "Quoted beta description"
    alpha = on["alpha"]
    assert alpha.scope == "contextual" and alpha.triggers == ("alpha",) and alpha.tasks == ("code",)
    assert str(folder) + "/skills/alpha/run.py" in alpha.body           # placeholders made absolute
    assert str(folder / "skills" / "alpha") + "/ref.md" in alpha.body
    assert alpha.guides == ("ref.md",)


def test_loader_merges_active_keeps_builtins_and_hides_stored(setup: Path):
    sources.sync()
    loaded = skills.load_all()
    assert "alpha" in loaded and "beta" not in loaded
    assert ".sources" not in str(loaded["fix-bug"].root or "")         # the built-in wins the name clash
    assert skills.stored("beta").name == "beta"
    assert "alpha" not in skills.index_text([], "code")                # contextual: asleep until woken
    assert skills.woken("tolong pakai alpha", "code") == ["alpha"]
    assert sources.search_stored("beta things")[0][0] == "beta"


def test_load_skill_opens_stored_skills_and_suggests_close_ones(setup: Path):
    from tools.skills.skill_tool import load_skill

    sources.sync()
    opened = asyncio.run(load_skill("beta"))
    assert opened["ok"] and opened["guide"].endswith("Beta body") and "Stored" in opened["note"]
    missing = asyncio.run(load_skill("beta-thing-that-does-not-exist"))
    assert not missing["ok"] and "beta" in missing["error"].split("Stored skills")[1]


def test_update_pulls_new_commits(setup: Path, upstream: Path):
    sources.sync()
    (upstream / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\nNew body\n", encoding="utf-8")
    _git(upstream, "commit", "-qam", "change")
    assert sources.sync(update=True)[0]["action"] == "updated"
    assert sources.active()["alpha"].body == "New body"
    assert sources.sync(update=True)[0]["action"] == "up to date"


def test_claude_code_links_only_our_folders(setup: Path):
    sources.sync()
    target = setup / "claude-skills"
    (target / "alpha").mkdir(parents=True)                       # a folder the user made: never touched
    done = sources.link_claude_code()
    assert done["alpha"] == "kept (not ours)"
    assert done["clean-code"] == "linked"
    text = (target / "clean-code" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: clean-code\ndescription: ") and "SquidWings harness" in text
    assert (target / "clean-code" / ".squidwings").exists()

    (target / "alpha").rmdir()
    done = sources.link_claude_code()
    assert done["alpha"] == "linked"
    linked = target / "alpha"
    assert (linked / "ref.md").is_symlink() and (linked / "ref.md").read_text() == "reference"
    assert "description: \"Does alpha things. Second line.\"" in (linked / "SKILL.md").read_text()

    stale = target / "old-skill"
    stale.mkdir()
    (stale / ".squidwings").write_text("x")
    assert sources.link_claude_code()["old-skill"] == "removed" and not stale.exists()


def test_disabled_means_no_github_skills(setup: Path, monkeypatch: pytest.MonkeyPatch):
    sources.sync()
    monkeypatch.setenv("SKILL_SOURCES", "0")
    sources.reload()
    assert "alpha" not in skills.load_all() and not sources.start_background()


def test_runnable_installs_code_skills_and_rewrites_paths(setup: Path, monkeypatch: pytest.MonkeyPatch):
    sources.sync()
    alpha = sources.active()["alpha"]
    (alpha.root / "run.py").write_text("print('ok')\n", encoding="utf-8")   # a skill that ships code
    workspace = setup / "ws"
    workspace.mkdir()
    text, installed_at = skills.runnable(alpha, workspace=workspace, session_id="s1")
    assert installed_at == ".skills/s1/alpha"
    assert (workspace / installed_at / "run.py").exists()
    assert str(alpha.root) not in text and ".skills/s1/alpha/run.py" in text
    assert text.startswith("[This guide comes from someone/skills") and "load_skill(name=" in text
    assert "execute_command" in alpha.tools or "execute_command" in sources._build(
        alpha.root / "SKILL.md", sources.sources()[0], {"name": "alpha"}).tools
    fix_bug = skills.load_all()["fix-bug"]
    assert skills.runnable(fix_bug) == (fix_bug.body, None)          # our own skills are handed out unchanged


def test_load_skill_search_and_claude_style_names(setup: Path):
    from tools.skills.skill_tool import load_skill

    sources.sync()
    found = asyncio.run(load_skill(search="beta things"))
    top = found["skills"][0]
    assert found["ok"] and (top["name"], top["status"]) == ("beta", "stored")
    named = asyncio.run(load_skill("someone:alpha"))
    assert named["ok"] and named["name"] == "alpha"
    assert "stored skills not listed here" in skills.index_text([], "code")


def test_background_keeper_never_runs_under_tests(setup: Path):
    assert sources._testing() and not sources.start_background()
