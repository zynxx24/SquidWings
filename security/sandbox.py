"""Real isolation for commands the agent runs.

The policy engine and command sanitizer decide what looks allowed, and a clever
command can get past both. This module limits what a command can do once it runs:

* It sees the system's programs read-only, plus one writable project folder.
* Your home folder, ``.env`` files, SSH keys, and API keys are not visible.
* The network is off unless ``SANDBOX_NETWORK=true``.
* CPU time, file size, and process count are limited.

Backends, in order of preference: bubblewrap (Linux namespaces, no daemon) and
Docker. With neither available, commands are refused rather than run unprotected.
``SANDBOX_MODE=off`` turns this off, and only then do commands run on the host.
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import get_settings
from utils.logger import get_logger
from utils.workspace_context import get_active_workspace

logger = get_logger(__name__)

BACKEND_BWRAP = "bwrap"
BACKEND_DOCKER = "docker"
BACKEND_NONE = "none"

_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64")
_SYSTEM_FILES = (
    "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ssl", "/etc/ca-certificates",
    "/etc/passwd", "/etc/group", "/etc/alternatives", "/etc/ld.so.cache", "/etc/localtime",
)
# Tools installed outside the system folders (nvm, uv, ...) stay usable.
_USER_TOOLS = ("node", "npm", "npx", "pnpm", "yarn", "uv", "go", "cargo", "rustc", "aislop", "graphify")
# Where uv keeps the Python that uv-installed tools such as graphify run on.
_USER_TOOL_DIRS = ("~/.local/share/uv/tools", "~/.local/share/uv/python")
# Commands can read these but not rewrite them: edited memory would plant
# instructions in every later prompt.
_READ_ONLY_STATE = (".agent_memory", ".vault")
# A git hook or config written here would later run on the host, outside the sandbox.
_GIT_READ_ONLY = (".git/hooks", ".git/config")
# Inside the agent's own folder these hold chats, logs, and long-term memory.
_PRIVATE_IN_PROJECT = ("vault", "workspace", "brain/memory/chat_store", "brain/memory/store", ".internal", "web/data")
_PRIVATE_FILES_IN_PROJECT = ("audit.jsonl", "tunnel.log")
_SECRET_SCAN_DEPTH = 2
_SKIP_SCAN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
_MAX_PROCESSES = 4096
_MAX_OPEN_FILES = 4096
_DOCKER_PROCESSES = 512
_DOCKER_DEFAULT_MEMORY_MB = 2048
_PROBE_TIMEOUT = 15

_INSTALL_HELP = (
    "Install bubblewrap (Debian/Ubuntu: sudo apt install bubblewrap), or start Docker, "
    "or set SANDBOX_MODE=off to run commands on this machine without isolation."
)


class SandboxError(RuntimeError):
    """A command was refused because it could not be isolated."""


@dataclass(frozen=True)
class SandboxPolicy:
    mode: str = "auto"
    network: bool = False
    memory_mb: int = 0
    cpu_seconds: int = 300
    file_size_mb: int = 1024
    image: str = "python:3.12-slim"
    ro_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scope:
    """The folder a command may see, and whether it may write there."""

    root: Path
    workdir: Path
    writable: bool


@dataclass
class Prepared:
    """A command ready to hand to subprocess."""

    argv: list[str]
    env: dict[str, str]
    host_cwd: str
    backend: str
    network: bool
    writable: bool
    preexec: Callable[[], None] | None = None
    container: str = ""
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, object]:
        """What the model should know: the limits explain otherwise odd failures."""
        return {"backend": self.backend, "network": self.network, "writable": self.writable}


def load_policy() -> SandboxPolicy:
    settings = get_settings()
    mode = settings.sandbox_mode.strip().lower() or "auto"
    # The older switch still works: SANDBOX_ENABLED=false means off.
    if not settings.sandbox_enabled:
        mode = "off"
    return SandboxPolicy(
        mode=mode,
        network=settings.sandbox_network,
        memory_mb=settings.sandbox_memory_mb,
        cpu_seconds=settings.sandbox_cpu_seconds,
        file_size_mb=settings.sandbox_file_size_mb,
        image=settings.sandbox_image,
        ro_paths=tuple(p.strip() for p in settings.sandbox_ro_paths.split(",") if p.strip()),
    )


_probe_results: dict[str, tuple[bool, str]] = {}


def probe(backend: str) -> tuple[bool, str]:
    """Whether a backend really works here, not just whether it is installed.

    Some systems install bubblewrap but block the user namespaces it needs, so
    it has to be run once to find out. The answer is cached for the process.
    """
    if backend in _probe_results:
        return _probe_results[backend]
    if backend == BACKEND_BWRAP:
        command = ["bwrap", "--unshare-user", "--ro-bind", "/", "/", "--dev", "/dev", "true"]
    else:
        command = ["docker", "version", "--format", "{{.Server.Version}}"]
    if shutil.which(command[0]) is None:
        result = (False, f"{command[0]} is not installed")
    else:
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
            detail = (done.stderr or done.stdout).strip()[:300]
            result = (done.returncode == 0, detail or "ok")
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = (False, str(exc))
    _probe_results[backend] = result
    return result


def reset_probes() -> None:
    _probe_results.clear()


def active_backend(policy: SandboxPolicy | None = None) -> str:
    """The backend commands will use.

    Raises:
        SandboxError: If isolation is required and nothing can provide it.
    """
    policy = policy or load_policy()
    if policy.mode == "off":
        return BACKEND_NONE
    if policy.mode in {BACKEND_BWRAP, BACKEND_DOCKER}:
        works, detail = probe(policy.mode)
        if not works:
            raise SandboxError(f"SANDBOX_MODE={policy.mode} is not usable: {detail}. {_INSTALL_HELP}")
        return policy.mode
    if policy.mode != "auto":
        raise SandboxError(f"Unknown SANDBOX_MODE '{policy.mode}'. Use auto, bwrap, docker, or off.")
    for backend in (BACKEND_BWRAP, BACKEND_DOCKER):
        if probe(backend)[0]:
            return backend
    raise SandboxError(f"Command refused: no sandbox is available. {_INSTALL_HELP}")


def python_command(policy: SandboxPolicy | None = None) -> str:
    """The Python to use inside the sandbox."""
    try:
        backend = active_backend(policy)
    except SandboxError:
        backend = BACKEND_NONE
    # A container has its own Python; the agent's virtualenv is not inside it.
    return "python" if backend == BACKEND_DOCKER else sys.executable


def resolve_scope(cwd: str | Path = ".", root: Path | None = None) -> Scope:
    """Work out what a command may touch. The model chooses cwd, so it is not trusted."""
    project = Path(get_settings().project_root).resolve()
    workspace = get_active_workspace() or os.getenv("SQUIDWINGS_WORKSPACE", "").strip()
    base = root.resolve() if root else Path(workspace).resolve() if workspace else project
    home = Path.home().resolve()
    if base == Path(base.anchor) or base == home or home.is_relative_to(base):
        raise SandboxError(f"Refusing to expose {base}: it contains your home folder.")

    requested = Path(cwd)
    target = (requested if requested.is_absolute() else base / requested).resolve()
    if not target.is_relative_to(base):
        raise SandboxError(f"Working directory {cwd} is outside the allowed folder {base}.")
    if not target.is_dir():
        raise SandboxError(f"Working directory does not exist: {target}")
    # Without a project folder, the agent's own source is read-only: a command
    # must not be able to rewrite the program that runs it.
    return Scope(root=base, workdir=target, writable=base != project)


def _secret_files(root: Path) -> list[Path]:
    """``.env`` files near the top of the tree, which stay hidden even when in scope."""
    found: list[Path] = []
    for directory, subdirs, names in os.walk(root):
        depth = len(Path(directory).relative_to(root).parts)
        subdirs[:] = [] if depth >= _SECRET_SCAN_DEPTH else [d for d in subdirs if d not in _SKIP_SCAN_DIRS]
        found.extend(
            Path(directory) / name
            for name in names
            if (name == ".env" or name.startswith(".env.")) and name != ".env.example"
        )
    return found


def _private_paths(scope: Scope) -> tuple[list[Path], list[Path]]:
    """Folders and files to hide when the scope is the agent's own folder."""
    project = Path(get_settings().project_root).resolve()
    if scope.root != project:
        return [], []
    folders = [project / name for name in _PRIVATE_IN_PROJECT if (project / name).is_dir()]
    files = [project / name for name in _PRIVATE_FILES_IN_PROJECT if (project / name).is_file()]
    return folders, files


def _user_tool_paths() -> tuple[list[str], list[str]]:
    """Install folders and PATH entries for tools outside the system folders."""
    prefixes: list[str] = []
    bins: list[str] = []
    for tool in _USER_TOOLS:
        located = shutil.which(tool)
        if not located:
            continue
        real_bin = Path(located).parent
        if str(real_bin).startswith(("/usr/", "/bin", "/sbin")):
            continue
        # ~/.local also holds unrelated app data, so only its bin folder is exposed.
        shared = real_bin.parent == Path.home() / ".local"
        prefix = str(real_bin.parent if real_bin.name == "bin" and not shared else real_bin)
        if prefix not in prefixes and Path(prefix).resolve() != Path.home().resolve():
            prefixes.append(prefix)
        if str(real_bin) not in bins:
            bins.append(str(real_bin))
    return prefixes, bins


def _limits(policy: SandboxPolicy) -> Callable[[], None]:
    """Resource limits applied in the child, just before it starts."""

    def apply() -> None:
        limits = [
            (resource.RLIMIT_CORE, 0),
            (resource.RLIMIT_CPU, policy.cpu_seconds),
            (resource.RLIMIT_FSIZE, policy.file_size_mb * 1024 * 1024),
            (resource.RLIMIT_NOFILE, _MAX_OPEN_FILES),
            (resource.RLIMIT_NPROC, _MAX_PROCESSES),
        ]
        # Off by default: Node reserves far more address space than it uses.
        if policy.memory_mb > 0:
            limits.append((resource.RLIMIT_AS, policy.memory_mb * 1024 * 1024))
        for name, value in limits:
            _, hard = resource.getrlimit(name)
            ceiling = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(name, (ceiling, hard))

    return apply


def _clean_env(path: str, home: str) -> dict[str, str]:
    """Only these reach a command. API keys in this process's environment do not."""
    return {
        "PATH": path,
        "HOME": home,
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "TERM": "dumb",
        "CI": "1",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "AISLOP_NO_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
    }


def _build_bwrap(policy: SandboxPolicy, scope: Scope, command: list[str]) -> tuple[list[str], dict[str, str]]:
    argv = [
        "bwrap", "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid",
        "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
    ]
    if not policy.network:
        argv.append("--unshare-net")
    for directory in _SYSTEM_DIRS:
        path = Path(directory)
        if path.is_symlink():
            argv += ["--symlink", os.readlink(path), directory]
        elif path.exists():
            argv += ["--ro-bind", directory, directory]
    for item in _SYSTEM_FILES:
        argv += ["--ro-bind-try", item, item]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home"]

    bind = "--bind" if scope.writable else "--ro-bind"
    argv += [bind, str(scope.root), str(scope.root)]

    # After the root, so that these mount on top of it.
    for secret in _secret_files(scope.root):
        argv += ["--ro-bind", "/dev/null", str(secret)]
    folders, files = _private_paths(scope)
    for folder in folders:
        argv += ["--tmpfs", str(folder)]
    for file in files:
        argv += ["--ro-bind", "/dev/null", str(file)]
    for name in _GIT_READ_ONLY:
        path = scope.root / name
        if path.exists():
            argv += ["--ro-bind", str(path), str(path)]
    for name in _READ_ONLY_STATE:
        path = scope.root / name
        if path.exists():
            argv += ["--ro-bind", str(path), str(path)]

    prefixes, tool_bins = _user_tool_paths()
    # The agent's virtualenv supplies pytest and ruff, and holds no secrets.
    python_dirs = {sys.prefix, sys.base_prefix}
    for extra in sorted(python_dirs) + prefixes + list(_USER_TOOL_DIRS) + list(policy.ro_paths):
        expanded = str(Path(extra).expanduser())
        if not str(expanded).startswith(("/usr", "/bin", "/sbin", "/lib")):
            argv += ["--ro-bind-try", expanded, expanded]

    project_venv = scope.root / ".venv" / "bin"
    path_entries = [str(project_venv)] if project_venv.is_dir() else []
    env = _clean_env(":".join([*path_entries, *tool_bins, _SYSTEM_PATH]), "/tmp/home")
    argv += ["--clearenv"]
    for key, value in env.items():
        argv += ["--setenv", key, value]
    argv += ["--chdir", str(scope.workdir), *command]
    # bwrap needs PATH to find itself; everything else was cleared inside.
    return argv, {"PATH": _SYSTEM_PATH, "LANG": env["LANG"]}


def _build_docker(policy: SandboxPolicy, scope: Scope, command: list[str], name: str) -> list[str]:
    memory = policy.memory_mb or _DOCKER_DEFAULT_MEMORY_MB
    argv = [
        "docker", "run", "--rm", "-i", "--name", name, "--read-only", "--tmpfs", "/tmp",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", f"{memory}m", "--pids-limit", str(_DOCKER_PROCESSES),
        "--user", f"{os.getuid()}:{os.getgid()}",
    ]
    if not policy.network:
        argv += ["--network", "none"]
    mode = "rw" if scope.writable else "ro"
    argv += ["-v", f"{scope.root}:{scope.root}:{mode}"]
    for secret in _secret_files(scope.root):
        argv += ["-v", f"/dev/null:{secret}:ro"]
    folders, files = _private_paths(scope)
    for folder in folders:
        argv += ["--tmpfs", str(folder)]
    for file in files:
        argv += ["-v", f"/dev/null:{file}:ro"]
    for relative in (*_GIT_READ_ONLY, *_READ_ONLY_STATE):
        path = scope.root / relative
        if path.exists():
            argv += ["-v", f"{path}:{path}:ro"]
    for key, value in _clean_env(_SYSTEM_PATH, "/tmp").items():
        argv += ["-e", f"{key}={value}"]
    argv += ["-w", str(scope.workdir), policy.image, *command]
    return argv


def prepare(
    command: str | list[str],
    cwd: str | Path = ".",
    *,
    root: Path | None = None,
    policy: SandboxPolicy | None = None,
) -> Prepared:
    """Turn a command into what should actually be executed.

    Args:
        command: A shell string, or an argument list.
        cwd: Working directory, resolved against the active workspace.
        root: Use this folder as the scope instead of the active workspace.
        policy: Limits to apply. Defaults to the configured ones.

    Raises:
        SandboxError: If the command cannot be isolated, or asks for a folder
            outside its scope.
    """
    policy = policy or load_policy()
    backend = active_backend(policy)
    argv_command = ["sh", "-c", command] if isinstance(command, str) else list(command)

    if backend == BACKEND_NONE:
        # Explicitly off. Still keep API keys away from what the model runs.
        target = Path(cwd)
        base = root or Path(get_active_workspace() or os.getcwd())
        host_cwd = target if target.is_absolute() else base / target
        env = _clean_env(os.environ.get("PATH", _SYSTEM_PATH), os.environ.get("HOME", "/tmp"))
        return Prepared(
            argv=argv_command, env=env, host_cwd=str(host_cwd), backend=backend,
            network=True, writable=True, preexec=_limits(policy),
        )

    scope = resolve_scope(cwd, root)
    if backend == BACKEND_BWRAP:
        argv, env = _build_bwrap(policy, scope, argv_command)
        return Prepared(
            argv=argv, env=env, host_cwd=str(scope.workdir), backend=backend,
            network=policy.network, writable=scope.writable, preexec=_limits(policy),
        )
    name = f"squidwings-{uuid.uuid4().hex[:12]}"
    return Prepared(
        argv=_build_docker(policy, scope, argv_command, name),
        env={"PATH": os.environ.get("PATH", _SYSTEM_PATH), "HOME": os.environ.get("HOME", "/tmp")},
        host_cwd=str(scope.workdir), backend=backend, network=policy.network,
        writable=scope.writable, container=name,
    )


def kill_container(prepared: Prepared) -> None:
    """Stop a timed-out container. Killing the docker client alone leaves it running."""
    if not prepared.container:
        return
    try:
        subprocess.run(["docker", "kill", prepared.container], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(f"[sandbox] could not stop {prepared.container}: {exc}")


def run_blocking(
    command: str | list[str],
    cwd: str | Path = ".",
    *,
    timeout: float = 120,
    root: Path | None = None,
    policy: SandboxPolicy | None = None,
) -> tuple[subprocess.CompletedProcess[str], Prepared]:
    """Run a command to completion from synchronous code.

    Raises:
        SandboxError: If the command cannot be isolated.
        subprocess.TimeoutExpired: If it runs too long.
    """
    prepared = prepare(command, cwd, root=root, policy=policy)
    try:
        done = subprocess.run(
            prepared.argv, cwd=prepared.host_cwd, env=prepared.env, capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL, preexec_fn=prepared.preexec,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        kill_container(prepared)
        raise
    return done, prepared


def self_check(policy: SandboxPolicy | None = None) -> list[tuple[str, bool, str]]:
    """Try to break out, and report what happened. Used by ``python main.py doctor``.

    Returns:
        One ``(check, passed, detail)`` per attempt.
    """
    policy = policy or load_policy()
    results: list[tuple[str, bool, str]] = []
    try:
        backend = active_backend(policy)
    except SandboxError as exc:
        return [("sandbox available", False, str(exc))]
    results.append(("sandbox available", backend != BACKEND_NONE, f"backend: {backend}"))
    if backend == BACKEND_NONE:
        return results

    project = Path(get_settings().project_root).resolve()
    outside = Path.home() / f".squidwings-sandbox-probe-{uuid.uuid4().hex[:8]}"
    secret_name = "DOCTOR_SECRET_VALUE"
    os.environ[secret_name] = "must-not-leak"
    try:
        with tempfile.TemporaryDirectory(prefix="squidwings-doctor-") as temporary:
            workspace = Path(temporary)
            (workspace / ".env").write_text("API_KEY=must-not-leak\n", encoding="utf-8")

            def attempt(command: str, cwd_root: Path = workspace) -> subprocess.CompletedProcess[str]:
                return run_blocking(command, ".", timeout=30, root=cwd_root, policy=policy)[0]

            done = attempt("echo ok > inside.txt")
            results.append(("can write inside the project", (workspace / "inside.txt").exists(), done.stderr.strip()[:120]))

            attempt(f"touch {outside}")
            results.append(("cannot write to your home folder", not outside.exists(), str(outside)))

            done = attempt("cat .env")
            results.append((".env in the project is hidden", "must-not-leak" not in done.stdout, ""))

            done = attempt(f"ls -a {Path.home()}")
            ssh_visible = ".ssh" in done.stdout or ".bashrc" in done.stdout
            results.append(("home folder contents are hidden", not ssh_visible, ""))

            done = attempt("env")
            leaked = "must-not-leak" in done.stdout or "API_KEY" in done.stdout
            results.append(("API keys are not in the environment", not leaked, ""))

            done = attempt(f"cat {project / '.env'}")
            results.append(("the agent's own .env is hidden", "API_KEY=" not in done.stdout, ""))

            done = attempt("ls /proc")
            pids = [item for item in done.stdout.split() if item.isdigit()]
            results.append(("other processes are hidden", len(pids) < 20, f"{len(pids)} visible"))

            python = python_command(policy)
            done = attempt(
                f"{python} -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3); print('CONNECTED')\""
            )
            connected = "CONNECTED" in done.stdout
            if policy.network:
                results.append(("network is on (SANDBOX_NETWORK=true)", True, "connected" if connected else "no route"))
            else:
                results.append(("network is off", not connected, ""))

        done = run_blocking("touch .squidwings-probe; cat .env", ".", timeout=30, root=project, policy=policy)[0]
        probe_file = project / ".squidwings-probe"
        wrote = probe_file.exists()
        probe_file.unlink(missing_ok=True)
        results.append(("the agent's own source is read-only", not wrote, ""))
        results.append(("the agent's own .env is hidden in its folder", "API_KEY=" not in done.stdout, ""))
    except (SandboxError, OSError, subprocess.SubprocessError) as exc:
        results.append(("self check finished", False, str(exc)))
    finally:
        os.environ.pop(secret_name, None)
        outside.unlink(missing_ok=True)
    return results
