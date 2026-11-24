"""Skills that live on GitHub: cloned automatically, only the important ones active.

``sources.json`` (next to this file) lists skill repositories. At start-up the
missing ones are cloned into ``brain/skills/.sources/<owner>__<repo>`` (shallow,
and sparse when the entry lists ``paths``, so a 100 MB repo costs a few MB), and
once a day every clone is fast-forwarded to its newest commit. The folder is
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent
MANIFEST = SKILLS_DIR / "sources.json"
SOURCES_DIR = SKILLS_DIR / ".sources"
MARKER = ".squidwings"

_GIT_TIMEOUT = 180
_SKIP_DIRS = {".git", "node_modules", "tests", "test", "__pycache__", "dist", "build"}
_HEADER_KEYS = ("triggers", "scope", "context", "tasks", "tools", "companion", "requires")
_started = threading.Lock()
_started_once = False


# ─── manifest ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Source:
    repo: str                       # "owner/name"
    paths: tuple[str, ...]          # sparse checkout paths; empty = whole repo
    active: tuple[dict[str, Any], ...]
    ref: str = ""
    remote: str = ""                # optional clone URL; default is the GitHub one

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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"[skills] cannot read {path.name}: {exc}")
        return {}


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
    tmp.rename(source.folder)
    return "cloned"


def _pull(source: Source) -> str:
    """Move a clone to the newest upstream commit. We never edit clones, so a hard reset is safe."""
    before = _git("rev-parse", "HEAD", cwd=source.folder).stdout.strip()
    branch = source.ref or "HEAD"
    done = _git("fetch", "--depth", "1", "-q", "origin", branch, cwd=source.folder)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "git fetch failed")
    if source.paths:   # keep the sparse set in step with the manifest
        _git("sparse-checkout", "set", "--cone", *source.paths, cwd=source.folder)
    _git("reset", "--hard", "-q", "FETCH_HEAD", cwd=source.folder)
    after = _git("rev-parse", "HEAD", cwd=source.folder).stdout.strip()
    return "updated" if after != before else "up to date"


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
    hours = float(os.getenv("SKILL_SOURCES_UPDATE_HOURS", "24") or 0)
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
        with contextlib.suppress(OSError):
            _state_file().write_text(json.dumps({"pulled_at": now, "results": results}, indent=1), encoding="utf-8")
    linked = link_claude_code()
    return {"results": results, "linked": linked}


def start_background() -> bool:
    """Run ``run_startup`` in a daemon thread once per process. False when disabled or already started."""
    global _started_once
    if not enabled():
        return False
    with _started:
        if _started_once:
            return False
        _started_once = True

    def worker() -> None:
        try:
            run_startup()
        except Exception as exc:  # noqa: BLE001 - skills from GitHub must never stop the app
            logger.warning(f"[skills] GitHub skill sync failed: {exc}")

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


def _build(path: Path, source: Source, header: dict[str, Any] | None):
    """A Skill from an upstream SKILL.md. ``header`` (from the manifest) makes it active; None makes it stored."""
    from brain.skills import Skill, _guides

    fields, body = _frontmatter(path.read_text(encoding="utf-8"))
    name = (header or {}).get("name") or fields.get("name") or path.parent.name
    root = path.parent
    split = lambda key: tuple(  # noqa: E731
        part.strip().lower() if key in {"triggers", "context", "tasks"} else part.strip()
        for part in str((header or {}).get(key, "")).split(",") if part.strip()
    )
    return Skill(
        name=str(name).strip(),
        description=str((header or {}).get("description") or fields.get("description", "")).strip(),
        triggers=split("triggers"),
        body=_expand(body.strip(), source.folder, root),
        requires=split("requires"),
        scope=str((header or {}).get("scope", "core")).lower() if header else "stored",
        context=split("context"),
        tasks=split("tasks"),
        tools=split("tools"),
        companion=split("companion"),
        root=root,
        guides=_guides(root),
    )


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, Any], dict[str, Any]]:
    """(active, stored) skills from every cloned source. Empty until the first clone finishes."""
    active: dict[str, Any] = {}
    stored: dict[str, Any] = {}
    if not enabled():
        return active, stored
    for source in sources():
        if not source.folder.is_dir():
            continue
        wanted = {}
        for header in source.active:
            target = (source.folder / header.get("path", ".") / "SKILL.md").resolve()
            wanted[target] = header
        for path in _skill_files(source):
            header = wanted.pop(path.resolve(), None)
            try:
                skill = _build(path, source, header)
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(f"[skills] cannot read {path}: {exc}")
                continue
            if not skill.name or skill.name == "template-skill":
                continue
            bucket = active if header else stored
            if skill.name in active or skill.name in stored:
                logger.debug(f"[skills] duplicate skill name {skill.name} in {source.repo}; first one kept")
                continue
            bucket[skill.name] = skill
        for target in wanted:
            logger.warning(f"[skills] {source.repo}: active skill not found at {target}")
    return active, stored


def active() -> dict[str, Any]:
    return dict(_catalog()[0])


def stored() -> dict[str, Any]:
    return dict(_catalog()[1])


def reload() -> None:
    """Forget cached catalogs after a clone or pull, so new skills show up without a restart."""
    _catalog.cache_clear()
    from brain import skills as skills_module

    skills_module.load_all.cache_clear()


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
    (folder / MARKER).write_text("made by brain/skills/sources.py; safe to delete\n", encoding="utf-8")
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
    from brain.skills import load_all

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
    for name, (skill, upstream) in wanted.items():
        try:
            done[name] = _link_one(skill, target, upstream)
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
    "active", "enabled", "link_claude_code", "manifest", "reload", "run_startup", "search_stored",
    "sources", "start_background", "status", "stored", "sync",
]
