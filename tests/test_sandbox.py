"""How commands are isolated. Builds the commands without needing bubblewrap or Docker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from security import sandbox
from security.sandbox import SandboxError, SandboxPolicy
from utils.workspace_context import set_active_workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Any:
    root = tmp_path / "project"
    root.mkdir()
    set_active_workspace(root)
    yield root
    set_active_workspace("")


@pytest.fixture
def bwrap_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sandbox._probe_results, sandbox.BACKEND_BWRAP, (True, "ok"))


def values_after(argv: list[str], flag: str) -> list[list[str]]:
    return [argv[i + 1 : i + 3] for i, item in enumerate(argv) if item == flag]


def test_no_backend_refuses_instead_of_running_unprotected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sandbox._probe_results, sandbox.BACKEND_BWRAP, (False, "missing"))
    monkeypatch.setitem(sandbox._probe_results, sandbox.BACKEND_DOCKER, (False, "missing"))
    with pytest.raises(SandboxError, match="refused"):
        sandbox.active_backend(SandboxPolicy(mode="auto"))


def test_unknown_mode_is_an_error_not_a_silent_off() -> None:
    with pytest.raises(SandboxError, match="Unknown"):
        sandbox.active_backend(SandboxPolicy(mode="ofonce"))


def test_network_is_off_unless_asked_for(workspace: Path, bwrap_available: None) -> None:
    closed = sandbox.prepare("true", policy=SandboxPolicy(mode="bwrap")).argv
    opened = sandbox.prepare("true", policy=SandboxPolicy(mode="bwrap", network=True)).argv
    assert "--unshare-net" in closed
    assert "--unshare-net" not in opened


def test_only_the_workspace_is_writable(workspace: Path, bwrap_available: None) -> None:
    prepared = sandbox.prepare("true", policy=SandboxPolicy(mode="bwrap"))
    assert values_after(prepared.argv, "--bind") == [[str(workspace), str(workspace)]]
    assert prepared.writable
    assert str(Path.home()) not in [pair[0] for pair in values_after(prepared.argv, "--bind")]


def test_env_files_are_masked_inside_the_workspace(workspace: Path, bwrap_available: None) -> None:
    (workspace / ".env").write_text("API_KEY=secret")
    (workspace / ".env.example").write_text("API_KEY=")
    (workspace / "sub").mkdir()
    (workspace / "sub" / ".env.local").write_text("X=1")
    pairs = values_after(sandbox.prepare("true", policy=SandboxPolicy(mode="bwrap")).argv, "--ro-bind")
    assert ["/dev/null", str(workspace / ".env")] in pairs
    assert ["/dev/null", str(workspace / "sub" / ".env.local")] in pairs
    assert ["/dev/null", str(workspace / ".env.example")] not in pairs


def test_git_config_stays_read_only(workspace: Path, bwrap_available: None) -> None:
    (workspace / ".git" / "hooks").mkdir(parents=True)
    (workspace / ".git" / "config").write_text("")
    pairs = values_after(sandbox.prepare("true", policy=SandboxPolicy(mode="bwrap")).argv, "--ro-bind")
    assert [str(workspace / ".git/hooks"), str(workspace / ".git/hooks")] in pairs
    assert [str(workspace / ".git/config"), str(workspace / ".git/config")] in pairs


def test_api_keys_do_not_reach_the_command(
    workspace: Path, bwrap_available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", "sk-secret")
    prepared = sandbox.prepare("true", policy=SandboxPolicy(mode="bwrap"))
    assert "--clearenv" in prepared.argv
    assert "sk-secret" not in " ".join(prepared.argv)
    assert "API_KEY" not in prepared.env


def test_even_with_sandbox_off_api_keys_are_removed(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "sk-secret")
    prepared = sandbox.prepare("true", policy=SandboxPolicy(mode="off"))
    assert prepared.backend == sandbox.BACKEND_NONE
    assert "API_KEY" not in prepared.env


@pytest.mark.parametrize("cwd", ["/etc", "/", "../.."])
def test_model_cannot_choose_a_folder_outside_the_workspace(workspace: Path, cwd: str) -> None:
    with pytest.raises(SandboxError, match="outside"):
        sandbox.resolve_scope(cwd)


def test_home_folder_is_never_a_scope(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="home"):
        sandbox.resolve_scope(".", root=Path.home())


def test_without_a_workspace_the_agents_own_source_is_read_only(bwrap_available: None) -> None:
    set_active_workspace("")
    prepared = sandbox.prepare("true", policy=SandboxPolicy(mode="bwrap"))
    assert not prepared.writable
    assert "--bind" not in prepared.argv


def test_docker_is_locked_down(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sandbox._probe_results, sandbox.BACKEND_DOCKER, (True, "ok"))
    (workspace / ".env").write_text("X=1")
    prepared = sandbox.prepare("true", policy=SandboxPolicy(mode="docker"))
    joined = " ".join(prepared.argv)
    assert "--network none" in joined
    assert "--cap-drop ALL" in joined
    assert "--read-only" in joined
    assert f"/dev/null:{workspace / '.env'}:ro" in joined
    assert prepared.container


async def test_refused_command_reports_why(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_command_prompt.run_command_tool import execute_command

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise SandboxError("no sandbox")

    monkeypatch.setattr(sandbox, "prepare", refuse)
    result = await execute_command("echo hi", cwd=str(workspace))
    assert result["success"] is False
    assert "no sandbox" in result["stderr"]


async def test_commands_run_and_children_die_on_timeout(workspace: Path) -> None:
    from tool.execute.execute_command_prompt.run_command_tool import execute_command

    done = await execute_command("echo halo", cwd=str(workspace))
    assert done["success"] and "halo" in done["stdout"]
    late = await execute_command("sleep 30 & wait", cwd=str(workspace), timeout=1)
    assert late["timed_out"]


@pytest.mark.parametrize("path", [".git/hooks/pre-commit", ".git/config", ".agent_memory/state.json"])
async def test_file_tools_cannot_plant_files_that_run_on_the_host(tmp_path: Path, path: str) -> None:
    from tool.execute.execute_local_ops.local_ops_tool import file_write

    result = await file_write(path=path, content="x", workspace_path=str(tmp_path))
    assert result["ok"] is False
    assert not (tmp_path / path).exists()


def test_user_profile_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_local_memory import user_profile, vault_context

    monkeypatch.setattr(vault_context, "global_vault_path", lambda *_a, **_k: tmp_path)
    assert user_profile.prompt_block() == ""
    assert user_profile.capture_from_message("ingat: saya memakai pnpm") == ["saya memakai pnpm"]
    assert user_profile.capture_from_message("ingat: saya memakai pnpm") == []
    assert user_profile.capture_from_message("halo, nama saya Wira") == ["Nama: Wira"]
    assert user_profile.entries() == ["saya memakai pnpm", "Nama: Wira"]
    assert "pnpm" in user_profile.prompt_block()
    user_profile.capture_from_message("nama saya Budi")
    assert "Nama: Wira" not in user_profile.entries()
    assert user_profile.forget("pnpm") == 1
    assert user_profile.entries() == ["Nama: Budi"]


@pytest.mark.parametrize(
    "message",
    ["ingat gak nama saya?", "siapa nama saya?", "nama saya dan file apa", "ingatkan saya besok", "simpan file ini"],
)
def test_ordinary_sentences_are_not_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    from tool.execute.execute_local_memory import user_profile, vault_context

    monkeypatch.setattr(vault_context, "global_vault_path", lambda *_a, **_k: tmp_path)
    assert user_profile.capture_from_message(message) == []


def test_credentials_are_never_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tool.execute.execute_local_memory import user_profile, vault_context

    monkeypatch.setattr(vault_context, "global_vault_path", lambda *_a, **_k: tmp_path)
    assert user_profile.remember("api key saya sk-abcdefghijklmnopqrstuvwxyz123456") is False


def test_relevant_memory_outranks_recent_memory(tmp_path: Path) -> None:
    from brain.memory.project_memory import ProjectMemory

    memory = ProjectMemory(tmp_path)
    for index in range(8):
        memory.remember("fact", f"hal lain nomor{index} tidak penting")
    memory.remember("fact", "autentikasi memakai JWT")
    top = memory.summary(query="perbaiki autentikasi").split("Facts:\n")[1].splitlines()[0]
    assert "autentikasi" in top


def test_wrong_memory_can_be_forgotten(tmp_path: Path) -> None:
    from brain.memory.project_memory import ProjectMemory

    memory = ProjectMemory(tmp_path)
    memory.remember("decision", "pakai postgres")
    memory.remember("fact", "port 8080")
    assert memory.forget("postgres") == 1
    assert memory.state["decisions"] == []
    assert memory.state["facts"]
