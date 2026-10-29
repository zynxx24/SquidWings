"""Early-warning tests for the layered memory stack (L1 boot / L2 graph / L3 notes).

Every test maps to a failure mode that either bit us during the build or would
bite silently in production. The point is to fail *early and loudly* instead of
letting the agent discover a broken vault mid-conversation.

Contract under test
-------------------
1. Every ``vault_*`` tool returns ``{"ok": bool, ...}`` and **never raises** —
   not on ``None``, not on NUL bytes, not on 5 KB of ``../``.
2. Failures carry ``error`` (and usually ``hint``) so the model never guesses.
3. The vault is created on first use; a missing vault is not an error.
4. Index and graph are disposable caches — delete or corrupt them and they
   rebuild from the vault, which is the only source of truth.
5. Paths cannot escape the vault.
6. ``build_context`` injects nothing for an empty vault, something real for a
   populated one, and never raises when the vault is broken.
7. ``vault_recall`` actually returns hits. Regression guard: it once read
   ``text``/``notes`` instead of ``hits``/``layers`` and answered "no matches"
   while silently swallowing the error.

No ``pytest-asyncio``: the tools are ``async def`` but pure local I/O, so
``asyncio.run`` keeps the suite dependency-free.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tool.execute.execute_local_memory import vault_context
from tool.execute.execute_local_memory import vault_tool as V
from tool.execute.execute_local_memory.vault import constants as C
from tool.execute.execute_local_memory.vault.index import VaultIndex

FOLDERS = (
    "00-inbox",
    "10-sessions",
    "20-concepts",
    "30-decisions",
    "40-people-projects",
    "90-meta",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def vault_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the vault under ``tmp_path``; both modules must agree on it."""
    fake = SimpleNamespace(obsidian_vault_path="", project_root=tmp_path)
    monkeypatch.setattr(V, "get_settings", lambda: fake)
    monkeypatch.setattr(vault_context, "get_settings", lambda: fake)
    vault_context.clear_cache()
    yield tmp_path
    vault_context.clear_cache()


@pytest.fixture
def vault(vault_root: Path) -> Path:
    """The resolved vault directory (created by the first tool call)."""
    return vault_root / "vault"


def run(coro):
    return asyncio.run(coro)


def write_raw(vault: Path, rel: str, body: str) -> Path:
    """Drop a file straight on disk, bypassing the tool (dirty-vault tests)."""
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def assert_envelope(result: Any) -> dict:
    """The one invariant every tool must satisfy."""
    assert isinstance(result, dict), f"returned {type(result).__name__}, not dict"
    assert "ok" in result, "result has no 'ok' key"
    assert isinstance(result["ok"], bool), "'ok' must be a bool"
    if not result["ok"]:
        assert result.get("error"), "failed result must carry an 'error'"
    return result


# --------------------------------------------------------------------------- #
# 1. Envelope contract — the whole surface on a cold vault
# --------------------------------------------------------------------------- #
TOOL_CALLS = [
    ("vault_boot", lambda: V.vault_boot()),
    ("vault_graph_build", lambda: V.vault_graph_build()),
    ("vault_neighbours", lambda: V.vault_neighbours(path="")),
    ("vault_explain", lambda: V.vault_explain(source="", target="")),
    ("vault_health", lambda: V.vault_health()),
    ("vault_search", lambda: V.vault_search(query="")),
    ("vault_read", lambda: V.vault_read(path="")),
    ("vault_recall", lambda: V.vault_recall(query="")),
    ("vault_write", lambda: V.vault_write(path="")),
]


@pytest.mark.parametrize("name,call", TOOL_CALLS, ids=[c[0] for c in TOOL_CALLS])
def test_every_tool_returns_envelope_on_cold_vault(vault_root: Path, name, call) -> None:
    assert_envelope(run(call()))


@pytest.mark.parametrize("name,call", TOOL_CALLS, ids=[c[0] for c in TOOL_CALLS])
def test_every_tool_reports_vault_path(vault_root: Path, name, call) -> None:
    """A tool that cannot say where it looked is undebuggable."""
    result = run(call())
    if result["ok"] and name not in ("vault_read", "vault_write"):
        assert result.get("vault"), f"{name} did not report its vault"


# --------------------------------------------------------------------------- #
# 2. Hostile input — nothing may raise, nothing may escape
# --------------------------------------------------------------------------- #
HOSTILE = [
    None,
    "",
    "   ",
    "\x00",
    "..",
    "../../etc/passwd",
    "../../../" * 20,
    "a/../../b",
    "🎉 emoji / 中文 / ünïcode",
    "A" * 5000,
    "name with spaces",
    "CON",
]


@pytest.mark.parametrize("bad", HOSTILE, ids=lambda v: repr(v)[:24])
def test_write_never_raises_and_never_escapes(vault_root: Path, vault: Path, bad: Any) -> None:
    result = assert_envelope(run(V.vault_write(path=bad, body="x")))
    if result["ok"]:
        written = (vault / result["path"]).resolve()
        written.relative_to(vault.resolve())  # raises if it escaped


@pytest.mark.parametrize("bad", HOSTILE, ids=lambda v: repr(v)[:24])
def test_read_search_neighbours_never_raise(vault_root: Path, bad: Any) -> None:
    assert_envelope(run(V.vault_read(path=bad)))
    assert_envelope(run(V.vault_search(query=bad)))
    assert_envelope(run(V.vault_neighbours(path=bad)))


def test_traversal_is_rejected_not_sandboxed(vault_root: Path) -> None:
    """``../outside`` must fail outright, not land in the parent directory."""
    result = run(V.vault_write(path="../outside", body="x"))
    assert result["ok"] is False
    assert not (vault_root / "outside.md").exists()


def test_missing_path_is_an_error_with_hint(vault_root: Path) -> None:
    result = run(V.vault_write(path=""))
    assert result["ok"] is False
    assert result.get("hint"), "write failure gave no recovery hint"


def test_read_missing_note_hints_at_search(vault_root: Path) -> None:
    result = run(V.vault_read(path="does-not-exist"))
    assert result["ok"] is False
    assert "vault_search" in (result.get("hint") or "")


# --------------------------------------------------------------------------- #
# 3. Bootstrap — first use creates the skeleton, no setup step
# --------------------------------------------------------------------------- #
def test_vault_created_on_first_use(vault_root: Path, vault: Path) -> None:
    assert not vault.exists()
    assert run(V.vault_boot())["ok"] is True
    for folder in FOLDERS:
        assert (vault / folder).is_dir(), f"{folder} missing"


def test_bootstrap_is_idempotent(vault_root: Path, vault: Path) -> None:
    run(V.vault_boot())
    before = {p.name for p in vault.iterdir()}
    run(V.vault_boot())
    after = {p.name for p in vault.iterdir()}
    assert before == after


# --------------------------------------------------------------------------- #
# 4. Write → read round trip; frontmatter generated by code
# --------------------------------------------------------------------------- #
def test_write_generates_valid_frontmatter(vault_root: Path, vault: Path) -> None:
    result = run(
        V.vault_write(
            path="RoundTrip",
            body="# RoundTrip\n\nisi.",
            note_type="concept",
            tags=["alpha", "beta"],
            confidence="high",
        )
    )
    assert result["ok"] is True
    assert result["path"] == "20-concepts/RoundTrip.md"
    assert result["type"] == "concept"
    assert result["status"] == C.STATUS_ACTIVE
    assert result["confidence"] == "high"
    assert sorted(result["tags"]) == ["alpha", "beta"]
    assert result["graph_stale"] is True

    raw = (vault / "20-concepts/RoundTrip.md").read_text("utf-8")
    assert raw.lstrip().startswith("---"), f"no frontmatter:\n{raw[:200]}"
    assert "type: concept" in raw
    assert "status: active" in raw
    assert "confidence: high" in raw


def test_bare_name_lands_in_type_folder(vault_root: Path) -> None:
    for note_type, folder in (("concept", "20-concepts"), ("decision", "30-decisions")):
        result = run(V.vault_write(path=f"Bare-{note_type}", body="x", note_type=note_type))
        assert result["ok"] is True
        assert result["path"].startswith(folder + "/"), result["path"]


def test_read_returns_written_body(vault_root: Path) -> None:
    run(V.vault_write(path="Body", body="# Body\n\nMARKER-12345", note_type="concept"))
    result = assert_envelope(run(V.vault_read(path="Body")))
    assert result["ok"] is True
    assert result["path"] == "20-concepts/Body.md"
    assert "MARKER-12345" in result["body"]
    assert "MARKER-12345" in result["text"], "text must be prompt-ready"


def test_read_truncates_oversized_body(vault_root: Path) -> None:
    run(V.vault_write(path="Big", body="x" * 500, note_type="concept"))
    result = run(V.vault_read(path="Big", max_chars=100))
    assert result["ok"] is True
    assert result["truncated"] is True
    assert "[truncated]" in result["body"]


def test_write_is_atomic_no_tmp_files_left(vault_root: Path, vault: Path) -> None:
    run(V.vault_write(path="Atomic", body="x", note_type="concept"))
    assert list(vault.rglob("*.tmp")) == []


def test_append_does_not_duplicate_frontmatter(vault_root: Path, vault: Path) -> None:
    run(V.vault_write(path="App", body="first", note_type="concept"))
    result = run(V.vault_write(path="App", body="second", note_type="concept", append=True))
    assert result["ok"] is True
    assert result["appended"] is True
    raw = (vault / "20-concepts/App.md").read_text("utf-8")
    assert raw.count("---") == 2, f"frontmatter duplicated:\n{raw[:300]}"
    assert "first" in raw and "second" in raw


def test_unicode_body_survives_round_trip(vault_root: Path, vault: Path) -> None:
    body = "# Ünïcode\n\n🎉 中文 emoji — dash"
    run(V.vault_write(path="Uni", body=body, note_type="concept"))
    raw = (vault / "20-concepts/Uni.md").read_text("utf-8")
    assert "🎉 中文" in raw


def test_empty_body_is_accepted(vault_root: Path) -> None:
    assert run(V.vault_write(path="Empty", body="", note_type="concept"))["ok"] is True


# --------------------------------------------------------------------------- #
# 5. Loose path resolution — the model never needs the exact path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ref",
    ["Loose", "Loose.md", "20-concepts/Loose.md", "20-concepts/Loose"],
    ids=["bare", "with-ext", "full-path", "path-no-ext"],
)
def test_read_resolves_loose_references(vault_root: Path, ref: str) -> None:
    run(V.vault_write(path="Loose", body="# Loose\n\nresolve me", note_type="concept"))
    result = run(V.vault_read(path=ref))
    assert result["ok"] is True, f"{ref!r} failed to resolve"
    assert result["path"] == "20-concepts/Loose.md"


def test_read_resolves_by_title(vault_root: Path) -> None:
    run(V.vault_write(path="stem-name", body="# Pretty Title\n\nby title", note_type="concept"))
    result = run(V.vault_read(path="Pretty Title"))
    assert result["ok"] is True
    assert result["path"] == "20-concepts/stem-name.md"


# --------------------------------------------------------------------------- #
# 6. Search — filters must actually filter
# --------------------------------------------------------------------------- #
def test_search_finds_by_title_and_tag(vault_root: Path) -> None:
    run(V.vault_write(path="SearchMe", body="# SearchMe\n\nbody", note_type="concept", tags=["needle"]))
    by_title = run(V.vault_search(query="SearchMe"))
    assert by_title["ok"] and by_title["count"] >= 1
    by_tag = run(V.vault_search(query="", tag="needle"))
    assert by_tag["ok"] and by_tag["count"] >= 1


def test_search_filters_by_note_type(vault_root: Path) -> None:
    run(V.vault_write(path="TypedConcept", body="x", note_type="concept"))
    run(V.vault_write(path="TypedDecision", body="x", note_type="decision"))
    result = run(V.vault_search(query="", note_type="decision"))
    assert result["ok"] is True
    assert all(r["type"] == "decision" for r in result["results"])


def test_search_respects_limit(vault_root: Path) -> None:
    for i in range(5):
        run(V.vault_write(path=f"Limit{i}", body="x", note_type="concept"))
    result = run(V.vault_search(query="Limit", limit=2))
    assert result["ok"] is True
    assert result["count"] <= 2


def test_search_no_match_is_not_an_error(vault_root: Path) -> None:
    result = run(V.vault_search(query="zzz-nothing-matches-zzz"))
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["text"] == "no matches"


def test_excluded_folders_are_not_indexed(vault_root: Path, vault: Path) -> None:
    """A vault accidentally pointed at a project root must not eat .venv."""
    write_raw(vault, ".venv/lib/pkg/DEPENDENCY.md", "---\ntype: concept\n---\n\nnoise")
    assert run(V.vault_search(query="DEPENDENCY"))["count"] == 0


# --------------------------------------------------------------------------- #
# 7. L2 graph — builds, caches, and survives corruption
# --------------------------------------------------------------------------- #
def test_graph_build_reports_health(vault_root: Path) -> None:
    run(V.vault_write(path="G1", body="# G1\n\nlinks to [[G2]]", note_type="concept"))
    run(V.vault_write(path="G2", body="# G2\n\nbody", note_type="concept"))
    result = run(V.vault_graph_build())
    assert result["ok"] is True
    assert result["layer"] == "L2"
    assert result["nodes"] >= 2, result
    assert result["graph_path"]
    assert result["text"], "graph summary must be prompt-ready"


def test_graph_corruption_rebuilds(vault_root: Path, vault: Path) -> None:
    run(V.vault_write(path="C1", body="x", note_type="concept"))
    run(V.vault_graph_build())
    for path in (vault / ".squidwings").glob("*.json"):
        path.write_text("{ not json", encoding="utf-8")
    result = run(V.vault_graph_build())
    assert result["ok"] is True, "corrupt graph cache must rebuild, not fail"


def test_health_reports_orphans(vault_root: Path) -> None:
    run(V.vault_write(path="Lonely", body="# Lonely\n\nno links", note_type="concept"))
    result = run(V.vault_health())
    assert result["ok"] is True
    assert "graph" in result and "index" in result
    assert result["text"]


def test_neighbours_of_unknown_note_is_an_error(vault_root: Path) -> None:
    result = run(V.vault_neighbours(path="no-such-note"))
    assert result["ok"] is False
    assert result.get("hint")


def test_explain_unknown_note_is_an_error(vault_root: Path) -> None:
    result = run(V.vault_explain(source="ghost-a", target="ghost-b"))
    assert result["ok"] is False


# --------------------------------------------------------------------------- #
# 8. Recall — the regression that made memory silently empty
# --------------------------------------------------------------------------- #
def test_recall_returns_hits_not_no_matches(vault_root: Path) -> None:
    run(V.vault_write(path="RecallProbe", body="# RecallProbe\n\nfindable body.", note_type="concept"))
    result = assert_envelope(run(V.vault_recall(query="RecallProbe")))
    assert result["ok"] is True
    assert result["notes"], "recall returned no notes — the hits/layers bug is back"
    assert any("RecallProbe" in n.get("path", "") for n in result["notes"]), result["notes"]
    assert "no matches" not in result["text"].lower()
    assert "RecallProbe" in result["text"]


def test_recall_reports_layers(vault_root: Path) -> None:
    run(V.vault_write(path="LayerProbe", body="# LayerProbe\n\nbody.", note_type="concept"))
    result = assert_envelope(run(V.vault_recall(query="LayerProbe")))
    assert isinstance(result["layers"], list) and result["layers"]
    assert result["layer"] == "+".join(result["layers"])


def test_recall_empty_query_still_returns_envelope(vault_root: Path) -> None:
    assert_envelope(run(V.vault_recall(query="")))


# --------------------------------------------------------------------------- #
# 9. The prompt bridge — memory is injected by code, not by the model
# --------------------------------------------------------------------------- #
def test_context_is_empty_for_empty_vault(vault_root: Path) -> None:
    """An empty vault must inject nothing, not a hollow wrapper block."""
    vault_context.clear_cache()
    text = vault_context.build_context()
    assert text == "", f"empty vault injected {len(text)} chars of wrapper noise"


def test_context_appears_when_vault_has_content(vault_root: Path) -> None:
    run(V.vault_write(path="Bridge", body="# Bridge\n\nreal memory.", note_type="concept"))
    vault_context.clear_cache()
    text = vault_context.build_context()
    assert text, "populated vault produced no memory block"
    assert "[MEMORY" in text
    assert "[END MEMORY]" in text
    assert "vault_search" in text, "the block must tell the model how to go deeper"


def test_context_never_raises_on_broken_vault(vault_root: Path, vault: Path) -> None:
    """A corrupt index degrades to 'no memory', never to 'no session'."""
    (vault / ".squidwings").mkdir(parents=True, exist_ok=True)
    (vault / ".squidwings" / "index.json").write_text("{ broken", encoding="utf-8")
    vault_context.clear_cache()
    text = vault_context.build_context()
    assert isinstance(text, str)  # empty is fine, an exception is not


def test_context_never_raises_when_settings_explode(vault_root: Path, monkeypatch) -> None:
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(vault_context, "get_settings", boom)
    vault_context.clear_cache()
    assert vault_context.build_context() == ""


def test_context_is_cached_between_calls(vault_root: Path) -> None:
    run(V.vault_write(path="Cached", body="# Cached\n\nbody", note_type="concept"))
    vault_context.clear_cache()
    first = vault_context.build_context()
    second = vault_context.build_context()
    assert first == second
    assert vault_context.stats()["cached_vaults"] == 1


def test_stamp_moves_after_write_into_existing_folder(vault_root: Path, vault: Path) -> None:
    """Regression: ``_stamp`` once watched only the vault root mtime.

    Writing a note into an existing subfolder does not touch the root, so the
    stamp never moved and the cached context never invalidated.
    """
    run(V.vault_write(path="StampA", body="x", note_type="concept"))
    first = vault_context._stamp(vault_context.vault_path())
    run(V.vault_write(path="StampB", body="y", note_type="concept"))
    second = vault_context._stamp(vault_context.vault_path())
    assert second > first, "stamp did not move after a write into an existing folder"


def test_context_updates_after_write_without_refresh(vault_root: Path) -> None:
    """The cached empty context must not survive a write.

    This is the failure the user actually feels: the agent answers without
    memory even though the vault has content, because the cache said "empty".
    """
    vault_context.clear_cache()
    assert vault_context.build_context() == ""  # prime the empty cache
    run(V.vault_write(path="Fresh", body="# Fresh\n\nnew memory", note_type="concept"))
    text = vault_context.build_context()  # deliberately no refresh=True
    assert text, "cached empty context survived a write — _stamp is too narrow"
    assert "[MEMORY" in text


def test_context_sees_external_edit(vault_root: Path, vault: Path) -> None:
    """Regression: ``_stamp`` once watched folders but not note files.

    Editing a note in place — Obsidian, an editor, ``git pull`` — bumps only
    the file's mtime. The parent folder does not move, so the cache never
    invalidated and the agent kept serving the old title. This is the most
    common way a vault changes, so it must not be the blind spot.

    The edit targets the **H1**, not a ``title:`` frontmatter key:
    ``schema.build_record`` derives the title from the first heading and
    ``make_record`` never emits ``title:``. An earlier version of this test
    replaced ``title: External``, which did not exist — the file was
    unchanged, so the test proved nothing and failed on its own assertion.
    """
    run(V.vault_write(path="External", body="# External\n\nbody", note_type="concept"))
    vault_context.clear_cache()
    assert "External" in vault_context.build_context()  # prime the cache

    note = vault / "20-concepts/External.md"
    raw = note.read_text("utf-8")
    assert "# External" in raw, "fixture assumption broken: H1 missing from note"
    note.write_text(raw.replace("# External", "# RenamedExternally"), "utf-8")

    text = vault_context.build_context()  # deliberately no refresh=True
    assert "RenamedExternally" in text, "external edit invisible — _stamp misses note files"


def test_stamp_moves_on_external_edit(vault_root: Path, vault: Path) -> None:
    run(V.vault_write(path="StampExt", body="x", note_type="concept"))
    first = vault_context._stamp(vault_context.vault_path())
    note = vault / "20-concepts/StampExt.md"
    note.write_text(note.read_text("utf-8") + "\nmore", "utf-8")
    second = vault_context._stamp(vault_context.vault_path())
    assert second > first, "stamp did not move after an in-place note edit"


def test_context_refresh_bypasses_cache(vault_root: Path) -> None:
    vault_context.clear_cache()
    assert vault_context.build_context() == ""
    run(V.vault_write(path="AfterCache", body="# AfterCache\n\nbody", note_type="concept"))
    refreshed = vault_context.build_context(refresh=True)
    assert refreshed, "refresh=True returned stale empty context"


def test_vault_path_is_under_project_root(vault_root: Path) -> None:
    assert vault_context.vault_path() == vault_root / "vault"


# --------------------------------------------------------------------------- #
# 10. Cross-layer consistency — tool, index, and graph must agree
# --------------------------------------------------------------------------- #
def test_write_then_all_layers_see_the_note(vault_root: Path) -> None:
    run(V.vault_write(path="Consistent", body="# Consistent\n\nbody", note_type="concept"))

    assert run(V.vault_read(path="Consistent"))["ok"] is True
    assert run(V.vault_search(query="Consistent"))["count"] >= 1
    assert run(V.vault_recall(query="Consistent"))["notes"]
    assert run(V.vault_graph_build())["nodes"] >= 1

    vault_context.clear_cache()
    assert "Consistent" in vault_context.build_context()


def test_index_refresh_sees_raw_file_drops(vault_root: Path, vault: Path) -> None:
    """Files written outside the tool must still be indexed."""
    write_raw(vault, "20-concepts/External.md", "---\ntype: concept\n---\n\n# External\n\nbody")
    index = VaultIndex(vault)
    records = index.refresh()
    assert "20-concepts/External.md" in records


def test_graph_stale_flag_after_write(vault_root: Path) -> None:
    first = run(V.vault_write(path="Stale", body="a", note_type="concept"))
    assert first["graph_stale"] is True
    second = run(V.vault_write(path="Stale", body="b", note_type="concept"))
    assert second["graph_stale"] is True, "graph must be marked stale on every write"


def test_tools_accept_explicit_workspace_path(vault_root: Path, tmp_path: Path) -> None:
    """``workspace_path`` is injected by the agent; it must be honoured."""
    other = tmp_path / "other"
    other.mkdir()
    result = run(V.vault_boot(workspace_path=str(other)))
    assert result["ok"] is True
    assert str(other) in result["vault"], result["vault"]
