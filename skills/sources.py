"""Skills that live on GitHub: cloned automatically, only the important ones active.

``sources.json`` (next to this file) lists skill repositories. At start-up the
missing ones are cloned into ``skills/.sources/<owner>__<repo>`` (shallow,
and sparse when the entry lists ``paths``, so a 100 MB repo costs a few MB), and
every 12 hours (``UPDATE_EVERY_HOURS``, or ``SKILL_SOURCES_UPDATE_HOURS``), when online, every clone is
fast-forwarded to its newest commit. The folder is
git-ignored: the skills are fetched, never committed here.

A source decides which of its skills matter:

* ``active``: joins the harness like a built-in skill, with the header given in
  the manifest (``triggers``, ``scope``, ``context``, ``tasks``, ``companion``).
  The upstream ``SKILL.md`` supplies the body and companion files. A built-in
  skill of the same name wins.
* everything else is **stored**: cloned and searchable, but out of the index and
  out of every prompt. ``load_skill(name)`` still opens it by its exact name, and
  an unknown name gets the closest stored skills as suggestions.

``"claude_code": true`` on an active skill, and the built-ins named in
``claude_code_builtin``, are linked into ``~/.claude/skills`` so Claude Code can
use them directly, without going through this harness. Each link is a small
folder with a rewritten ``SKILL.md`` (paths made absolute, harness tool names
explained) and symlinks to the skill's other files, marked with ``.squidwings``
so we only ever touch folders we made.

A manifest entry may set ``url`` to clone from somewhere other than GitHub, and
``ref`` to follow a branch or tag other than the default.

Every source is **pinned** to the ``commit`` in its manifest entry, so the exact version in use is on record.
A source without a pin is pinned to what its first clone got. The daily pull keeps the skills in sync with
upstream by itself (the owner's rule, 2026-11-28): the clone moves to the newest commit, the pin follows, and
the move (from, to, changed files) is added to ``history`` in ``.sources/.state.json`` for audit and rollback.
``SKILL_SOURCES_AUTO_APPROVE=0`` holds new commits as pending instead, for ``python main.py skills approve``.

Environment:
    SKILL_SOURCES=0              no cloning, no loading of GitHub skills
    SKILL_SOURCES_UPDATE_HOURS   how often to pull (default 24; 0 = never)
    SKILL_SOURCES_CLAUDE=0       do not link anything into ~/.claude/skills
    CLAUDE_SKILLS_DIR            where Claude Code reads personal skills
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from brain.utils.logger import get_logger

logger = get_logger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent
MANIFEST = SKILLS_DIR / "sources.json"
SOURCES_DIR = SKILLS_DIR / ".sources"
MARKER = ".squidwings"

_GIT_TIMEOUT = 180
_SKIP_DIRS = {".git", "node_modules", "tests", "test", "__pycache__", "dist", "build"}
_DEFAULT_TOOLS = ("load_skill", "read_file", "file_write", "file_patch", "file_search", "web_read")
_CHECK_EVERY_S = 600   # how often the background loop looks for new repos in sources.json
_started = threading.Lock()
_started_once = False
_linked_once = False


# ─── manifest ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Source:
    repo: str                       # "owner/name"
    paths: tuple[str, ...]          # sparse checkout paths; empty = whole repo
    active: tuple[dict[str, Any], ...]
    ref: str = ""
    remote: str = ""                # optional clone URL; default is the GitHub one
    commit: str = ""                # the pinned commit; empty until the first clone pins it

    @property
    def url(self) -> str:
        return self.remote or f"https://github.com/{self.repo}.git"

    @property
    def folder(self) -> Path:
        return SOURCES_DIR / self.repo.replace("/", "__")


def _state_file() -> Path:
    return SOURCES_DIR / ".state.json"


def enabled() -> bool:
    return os.getenv("SKILL_SOURCES", "1").strip().lower() not in {"0", "false", "no", "off"}


def manifest(path: Path | None = None) -> dict[str, Any]:
    path = path or MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"[skills] cannot read {path.name}: {exc}")
        return {}
    if not isinstance(data, dict):
        logger.warning(f"[skills] {path.name} is not a JSON object")
        return {}
    return data


def sources(path: Path | None = None) -> list[Source]:
    out = []
    for entry in manifest(path).get("sources", []):
        repo = str(entry.get("repo", "")).strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            logger.warning(f"[skills] bad repo in sources.json: {repo!r}")
            continue
        out.append(Source(
            repo=repo,
            paths=tuple(p.strip("/") for p in entry.get("paths", []) if p.strip("/")),
            active=tuple(entry.get("active", [])),
            ref=str(entry.get("ref", "")).strip(),
            remote=str(entry.get("url", "")).strip(),
            commit=str(entry.get("commit", "")).strip(),
        ))
    return out


# ─── git ──────────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _clone(source: Source) -> str:
    """Clone into a temporary folder, then move it into place, so a broken clone never looks installed."""
    tmp = source.folder.with_name(source.folder.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    args = ["clone", "--depth", "1", "--filter=blob:none", "-q"]
    if source.ref:
        args += ["--branch", source.ref]
    if source.paths:
        args.append("--sparse")
    done = _git(*args, source.url, str(tmp))
    if done.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(done.stderr.strip() or "git clone failed")
    if source.paths:
        done = _git("sparse-checkout", "set", "--cone", *source.paths, cwd=tmp)
        if done.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(done.stderr.strip() or "sparse-checkout failed")
    if source.commit:
        _checkout(tmp, source.commit)
    tmp.rename(source.folder)
    if not source.commit:
        pin(source.repo, _git("rev-parse", "HEAD", cwd=source.folder).stdout.strip())
    return "cloned"


def _checkout(folder: Path, commit: str) -> None:
    """Put a clone on exactly ``commit`` (fetched by its id when the shallow clone lacks it)."""
    if _git("cat-file", "-e", f"{commit}^{{commit}}", cwd=folder).returncode != 0:
        done = _git("fetch", "--depth", "1", "-q", "origin", commit, cwd=folder)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip() or f"cannot fetch pinned commit {commit[:12]}")
    done = _git("reset", "--hard", "-q", commit, cwd=folder)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"cannot check out {commit[:12]}")


_manifest_lock = threading.Lock()


def pin(repo: str, commit: str, path: Path | None = None) -> None:
    """Record ``commit`` as the pinned version of ``repo`` in sources.json."""
    path = path or MANIFEST
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"not a commit id: {commit!r}")
    with _manifest_lock:
        data = manifest(path)
        for entry in data.get("sources", []):
            if str(entry.get("repo", "")).strip().strip("/") == repo:
                entry["commit"] = commit
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _pull(source: Source) -> str:
    """Look at upstream without moving: newer commits become a pending update the owner approves."""
    if not source.commit:
        pin(source.repo, _git("rev-parse", "HEAD", cwd=source.folder).stdout.strip())
        return "pinned"
    branch = source.ref or "HEAD"
    done = _git("fetch", "--depth", "1", "-q", "origin", branch, cwd=source.folder)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "git fetch failed")
    latest = _git("rev-parse", "FETCH_HEAD", cwd=source.folder).stdout.strip()
    _checkout(source.folder, source.commit)
    if not latest or latest == source.commit:
        _set_pending(source.repo, None)
        return "up to date"
    files = _git("diff", "--name-only", source.commit, latest, cwd=source.folder).stdout.split()
    if auto_approve():
        _checkout(source.folder, latest)
        pin(source.repo, latest)
        _set_pending(source.repo, None)
        _remember_move(source.repo, source.commit, latest, files)
        return "updated"
    _set_pending(source.repo, {"commit": latest, "files": files[:200], "seen_at": time.time()})
    return f"update available ({len(files)} file(s) changed; approve with: python main.py skills approve {source.repo})"


def auto_approve() -> bool:
    return os.getenv("SKILL_SOURCES_AUTO_APPROVE", "1").strip().lower() not in {"0", "false", "no", "off"}


_HISTORY_MAX = 200


def _remember_move(repo: str, old: str, new: str, files: list[str]) -> None:
    """Keep the last moves for audit and rollback (``python main.py skills history``)."""
    with _manifest_lock:
        state = _read_state()
        history = state.setdefault("history", [])
        history.append({"repo": repo, "from": old, "to": new, "files": files[:200], "at": time.time()})
        del history[:-_HISTORY_MAX]
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(state, indent=1), encoding="utf-8")


def history() -> list[dict[str, Any]]:
    found = _read_state().get("history", [])
    return found if isinstance(found, list) else []


def _read_state() -> dict[str, Any]:
    state: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        loaded = json.loads(_state_file().read_text(encoding="utf-8"))
        state = loaded if isinstance(loaded, dict) else {}
    return state


def _set_pending(repo: str, info: dict[str, Any] | None) -> None:
    with _manifest_lock:
        state = _read_state()
        pending = state.setdefault("pending", {})
        if info is None:
            pending.pop(repo, None)
        else:
            pending[repo] = info
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(state, indent=1), encoding="utf-8")


def pending() -> dict[str, dict[str, Any]]:
    """Upstream updates waiting for approval: repo -> {commit, files}."""
    found = _read_state().get("pending", {})
    return found if isinstance(found, dict) else {}


def approve(which: str = "all") -> list[dict[str, str]]:
    """Move the named source (or all) to its pending upstream commit and pin it there."""
    waiting = pending()
    results = []
    for source in sources():
        if which not in {"all", source.repo} or source.repo not in waiting:
            continue
        commit = str(waiting[source.repo].get("commit", ""))
        try:
            _checkout(source.folder, commit)
            pin(source.repo, commit)
            _set_pending(source.repo, None)
            _remember_move(source.repo, source.commit, commit, list(waiting[source.repo].get("files", [])))
            results.append({"repo": source.repo, "action": f"approved {commit[:12]}"})
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            results.append({"repo": source.repo, "action": "failed", "detail": str(exc)[:300]})
    if results:
        reload()
    return results


def sync(update: bool = False, path: Path | None = None) -> list[dict[str, str]]:
    """Clone missing sources; with ``update`` also pull the ones already here."""
    if shutil.which("git") is None:
        return [{"repo": "*", "action": "skipped", "detail": "git is not installed"}]
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    def one(source: Source) -> dict[str, str]:
        try:
            if not (source.folder / ".git").is_dir():
                return {"repo": source.repo, "action": _clone(source)}
            if update:
                return {"repo": source.repo, "action": _pull(source)}
            return {"repo": source.repo, "action": "present"}
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            return {"repo": source.repo, "action": "failed", "detail": str(exc)[:300]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, sources(path)))
    for item in results:
        if item["action"] in {"cloned", "updated", "failed"}:
            logger.info(f"[skills] {item['repo']}: {item['action']} {item.get('detail', '')}".rstrip())
    if any(item["action"] in {"cloned", "updated"} for item in results):
        reload()
    return results


def _due_for_update(now: float) -> bool:
    from brain.utils import online

    hours = online.every_hours("SKILL_SOURCES_UPDATE_HOURS")
    if hours <= 0:
        return False
    state: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        state = json.loads(_state_file().read_text(encoding="utf-8"))
    return now - float(state.get("pulled_at", 0)) >= hours * 3600


def run_startup(now: float | None = None) -> dict[str, Any]:
    """Clone what is missing, pull when a day has passed, then refresh Claude Code's links."""
    now = time.time() if now is None else now
    update = _due_for_update(now)
    results = sync(update=update)
    if update and not any(r["action"] == "failed" for r in results):
        with contextlib.suppress(OSError), _manifest_lock:
            state = _read_state()
            state.update(pulled_at=now, results=results)
            _state_file().write_text(json.dumps(state, indent=1), encoding="utf-8")
    global _linked_once
    linked = link_claude_code()
    _linked_once = True
    # Every AI installed here (also one added since yesterday) gets the skills through the MCP server.
    try:
        from skills import connect

        connected = connect.connect()
    except Exception as exc:  # noqa: BLE001 - another tool's config must never stop the skill sync
        logger.warning(f"[skills] Could not register the skills server with the agents here: {exc}")
        connected = []
    return {"results": results, "linked": linked, "connected": connected}


def _missing() -> bool:
    return any(not (s.folder / ".git").is_dir() for s in sources())


def _testing() -> bool:
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


def start_background() -> bool:
    """Keep GitHub skills in step, in a daemon thread started once per process.

    First pass: clone what is missing, pull if a day has passed, link Claude Code. Then every
    ten minutes: a repo newly added to sources.json is cloned, and the 12-hourly pull happens on
    time, without a restart. False when disabled, under tests, or already started.
    """
    global _started_once
    if not enabled() or _testing():
        return False
    with _started:
        if _started_once:
            return False
        _started_once = True

    def worker() -> None:
        global _linked_once
        from brain.utils import online

        while True:
            try:
                if not _linked_once and not online.is_online():
                    # Offline at start: link what is already here, pull once the network is back.
                    link_claude_code()
                    _linked_once = True
                elif (_missing() or _due_for_update(time.time()) or not _linked_once) and online.is_online():
                    run_startup()
            except Exception as exc:  # noqa: BLE001 - skills from GitHub must never stop the app
                logger.warning(f"[skills] GitHub skill sync failed: {exc}")
            time.sleep(_CHECK_EVERY_S)

    threading.Thread(target=worker, name="skill-sources", daemon=True).start()
    return True


# ─── reading upstream skills ──────────────────────────────────────────


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Upstream headers are YAML; we only need flat strings, including | and > blocks and quotes."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        return {}, text
    fields: dict[str, str] = {}
    lines = match.group(1).splitlines()
    i = 0
    while i < len(lines):
        key, sep, value = lines[i].partition(":")
        i += 1
        if not sep or lines[i - 1][:1].isspace():
            continue
        value = value.strip()
        if value in {"|", ">", "|-", ">-", "|+", ">+"}:
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1].isspace()):
                block.append(lines[i].strip())
                i += 1
            value = " ".join(part for part in block if part)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip()] = value
    return fields, match.group(2)


def _expand(body: str, repo_root: Path, skill_root: Path) -> str:
    """Make the placeholder paths some upstream skills use point at the real files."""
    return (body.replace("${CLAUDE_PLUGIN_ROOT}", str(repo_root))
                .replace("${CLAUDE_SKILL_DIR}", str(skill_root))
                .replace("{baseDir}", str(skill_root)))


def _skill_files(source: Source) -> list[Path]:
    roots = [source.folder / p for p in source.paths] or [source.folder]
    found: list[Path] = []
    for root in roots:
        if root.is_file() and root.name == "SKILL.md":
            found.append(root)
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            if "SKILL.md" in filenames:
                found.append(Path(dirpath) / "SKILL.md")
    return sorted(found)


def _build(path: Path, source: Source, header: dict[str, Any] | None) -> Any:
    """A Skill from an upstream SKILL.md. ``header`` (from the manifest) makes it active; None makes it stored.

    An active skill with an adapter in skills/adapters/ carries our adjustment in front of (or
    instead of) the upstream text; the upstream file itself is never changed.
    """
    from skills import Skill, _guides, adapters

    fields, body = _frontmatter(path.read_text(encoding="utf-8"))
    name = (header or {}).get("name") or fields.get("name") or path.parent.name
    root = path.parent
    adapter = adapters.get(str(name).strip()) if header else None
    split = lambda key: tuple(  # noqa: E731
        part.strip().lower() if key in {"triggers", "context", "tasks", "wants"} else part.strip()
        for part in str((header or {}).get(key, "")).split(",") if part.strip()
    )
    tools = split("tools")
    if header and not tools:
        # A woken skill must be usable even on a small model's short tool list.
        from skills import _has_code

        tools = _DEFAULT_TOOLS + (("execute_command",) if _has_code(root) else ())
    return Skill(
        name=str(name).strip(),
        description=str((header or {}).get("description") or fields.get("description", "")).strip(),
        triggers=split("triggers"),
        body=adapters.combine(adapter, _expand(body.strip(), source.folder, root), source.repo),
        requires=split("requires"),
        scope=str((header or {}).get("scope", "core")).lower() if header else "stored",
        context=split("context"),
        tasks=split("tasks"),
        tools=tools,
        companion=split("companion"),
        pool=str((header or {}).get("pool", "")).strip().lower(),
        wants=split("wants"),
        root=root,
        guides=tuple(sorted({*_guides(root), *((adapters.UPSTREAM_GUIDE,) if adapter else ())})),
        origin=source.repo,
        upstream_file=path if adapter else None,
        adapted=adapter is not None,
    )


def _fallback(header: dict[str, Any], source: Source) -> Any:
    """The adapter alone standing in for an active skill whose upstream is not cloned yet (or offline)."""
    from skills import Skill, adapters

    name = str(header.get("name", "")).strip()
    adapter = adapters.get(name)
    if not adapter or not adapter.fallback:
        return None
    split = lambda key: tuple(  # noqa: E731
        part.strip().lower() if key in {"triggers", "context", "tasks", "wants"} else part.strip()
        for part in str(header.get(key, "")).split(",") if part.strip()
    )
    return Skill(
        name=name, description=str(header.get("description", "")).strip(), triggers=split("triggers"),
        body=adapter.body, requires=split("requires"), scope=str(header.get("scope", "core")).lower(),
        context=split("context"), tasks=split("tasks"), tools=split("tools") or _DEFAULT_TOOLS,
        companion=split("companion"), pool=str(header.get("pool", "")).strip().lower(), wants=split("wants"),
        origin=source.repo, adapted=True,
    )


def _upstream_path(source: Source, header: dict[str, Any]) -> Path:
    """Where an active skill's upstream file is: <path>/SKILL.md, or <path>/<file> for repos that name it otherwise."""
    return (source.folder / str(header.get("path", ".")) / str(header.get("file") or "SKILL.md")).resolve()


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, Any], dict[str, Any]]:
    """(active, stored) skills from every cloned source. Empty until the first clone finishes."""
    active: dict[str, Any] = {}
    stored: dict[str, Any] = {}
    if not enabled():
        return active, stored
    for source in sources():
        wanted = {_upstream_path(source, header): header for header in source.active}
        if not source.folder.is_dir():
            for header in wanted.values():
                if (skill := _fallback(header, source)) is not None:
                    active.setdefault(skill.name, skill)
            continue
        # Upstream files not named SKILL.md (graphify's skill.md) are read where the manifest says.
        extra = [target for target, header in wanted.items() if header.get("file") and target.is_file()]
        for path in [*_skill_files(source), *extra]:
            key = path.resolve()
            found = wanted.pop(key) if key in wanted else None
            try:
                skill = _build(path, source, found)
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(f"[skills] cannot read {path}: {exc}")
                continue
            if not skill.name or skill.name == "template-skill":
                continue
            bucket = active if found else stored
            if skill.name in active or skill.name in stored:
                logger.debug(f"[skills] duplicate skill name {skill.name} in {source.repo}; first one kept")
                continue
            bucket[skill.name] = skill
        for target, header in wanted.items():
            if (skill := _fallback(header, source)) is not None:
                active.setdefault(skill.name, skill)
                logger.info(f"[skills] {source.repo}: {skill.name} runs on its adapter until the clone has it")
                continue
            logger.warning(f"[skills] {source.repo}: active skill not found at {target}")
    return active, stored


def active() -> dict[str, Any]:
    return dict(_catalog()[0])


def stored() -> dict[str, Any]:
    return dict(_catalog()[1])


def reload() -> None:
    """Forget cached catalogs after a clone or pull, so new skills show up without a restart."""
    _catalog.cache_clear()
    import skills as skills_module
    from skills import adapters

    adapters.reload()

    clear = getattr(skills_module.load_all, "cache_clear", None)
    if clear is not None:
        clear()


def search_stored(query: str, limit: int = 8) -> list[tuple[str, str]]:
    """Stored skills whose name or description matches the words in ``query``, best first."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
    scored = []
    for name, skill in stored().items():
        hay = f"{name} {skill.description}".lower()
        score = sum(3 if w in name else 1 for w in words if w in hay)
        if score:
            scored.append((score, name, skill.description))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(name, desc[:160]) for _, name, desc in scored[:limit]]


def find(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Skills for a need, active and stored in one BM25 ranking (name counts most, then triggers,
    then the description). Ties go to active skills."""
    from skills import load_all, rank

    active_now = load_all()
    stored_now = stored()
    found = []
    for name, score in rank(query.replace("-", " ").replace(":", " "), limit=limit * 2):
        skill = active_now.get(name) or stored_now.get(name)
        if skill is None:
            continue
        status = "active" if name in active_now else "stored"
        path = str(skill.root / "SKILL.md") if skill.root else str(SKILLS_DIR / f"{skill.name}.md")
        found.append((score, status == "active", {"name": skill.name, "status": status,
                                                  "description": skill.description[:200], "path": path}))
    found.sort(key=lambda item: (-round(item[0], 6), not item[1]))
    return [item for *_, item in found[:limit]]


# ─── Claude Code ──────────────────────────────────────────────────────


def claude_skills_dir() -> Path:
    return Path(os.getenv("CLAUDE_SKILLS_DIR", "~/.claude/skills")).expanduser()


_HARNESS_NOTE = (
    "> Linked from the SquidWings harness ({where}). Files named in this guide are in this folder.\n"
    "> Where it says `load_skill(name=\"{name}\", file=\"X\")`, read `X` here. Tools such as `style_pick`,\n"
    "> `contrast_check` or `aislop_scan` belong to the harness: use the scripts in this folder or the\n"
    "> equivalent shell command instead.\n"
)


def _claude_description(skill: Any) -> str:
    text = re.sub(r"\s+", " ", skill.description).strip()
    return text[:1000] or skill.name


def _link_one(skill: Any, target_dir: Path, upstream_header: dict[str, str] | None) -> str:
    folder = target_dir / skill.name
    if folder.exists() and not (folder / MARKER).exists():
        return "kept (not ours)"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / MARKER).write_text("made by skills/sources.py; safe to delete\n", encoding="utf-8")
    description = _claude_description(skill)
    if upstream_header and upstream_header.get("description"):
        description = re.sub(r"\s+", " ", upstream_header["description"]).strip()[:1000]
    note = "" if upstream_header is not None else _HARNESS_NOTE.format(
        where=skill.root or SKILLS_DIR, name=skill.name) + "\n"
    safe = description.replace('"', "'")
    (folder / "SKILL.md").write_text(
        f'---\nname: {skill.name}\ndescription: "{safe}"\n---\n\n{note}{skill.body}\n', encoding="utf-8")
    if skill.root is not None:
        for entry in skill.root.iterdir():
            if entry.name in {"SKILL.md", MARKER} or entry.name.startswith("."):
                continue
            (folder / entry.name).symlink_to(entry.resolve())
    return "linked"


def link_claude_code() -> dict[str, str]:
    """Link the important skills into Claude Code's personal skills folder. Returns name -> what happened."""
    if os.getenv("SKILL_SOURCES_CLAUDE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {}
    from skills import load_all

    target = claude_skills_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(f"[skills] cannot create {target}: {exc}")
        return {}

    wanted: dict[str, tuple[Any, dict[str, str] | None]] = {}
    builtins = load_all()
    for name in manifest().get("claude_code_builtin", []):
        if name in builtins:
            wanted[name] = (builtins[name], None)
    on = active()
    for source in sources():
        for header in source.active:
            name = header.get("name")
            if header.get("claude_code") and name in on:
                skill = on[name]
                upstream, _ = _frontmatter((skill.root / "SKILL.md").read_text(encoding="utf-8"))
                wanted[name] = (skill, upstream)

    done: dict[str, str] = {}
    for name, (chosen, front) in wanted.items():
        try:
            done[name] = _link_one(chosen, target, front)
        except OSError as exc:
            done[name] = f"failed: {exc}"
    # Remove our own links for skills that are no longer wanted (moved to storage, or renamed).
    for folder in target.iterdir() if target.is_dir() else []:
        if (folder / MARKER).exists() and folder.name not in wanted:
            shutil.rmtree(folder, ignore_errors=True)
            done[folder.name] = "removed"
    return done


def status() -> dict[str, Any]:
    on, off = _catalog()
    required = [h["name"] for s in sources() for h in s.active if h.get("required")]
    return {
        "sources": [
            {"repo": s.repo, "cloned": (s.folder / ".git").is_dir(),
             "commit": _git("rev-parse", "--short", "HEAD", cwd=s.folder).stdout.strip() if (s.folder / ".git").is_dir() else ""}
            for s in sources()
        ],
        "active": sorted(on),
        "stored": sorted(off),
        "required_missing": [name for name in required if name not in on],
    }


__all__ = [
    "active", "enabled", "find", "link_claude_code", "manifest", "reload", "run_startup", "search_stored",
    "sources", "start_background", "status", "stored", "sync",
]
