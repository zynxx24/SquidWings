"""Agent Reach (github.com/Panniantong/Agent-Reach) inside SquidWings: the parts of the internet web search misses.

Agent Reach is a read-only CLI: YouTube subtitles and transcripts, Reddit threads, X, GitHub issues, Bilibili, V2EX,
LinkedIn jobs, Xueqiu quotes and more, each behind one command that prints one JSON object. This module is how the
harness uses it. Our adjustments live here, in the harness, never in Agent Reach's own files or skill:

* It is installed from its GitHub release with ``uv tool install`` and kept current with ``agent-reach update`` by
  the daily tool check (``tools/code_intel/tool_updates.py``), like graphify and aislop.
* SquidWings calls it through its own tools (``tools/toolbox/reach_tools.py``) and as the fallback of ``web_read``
  for the sites it covers. Agent Reach's skill is not linked into other AIs' skill folders: skills for Claude Code
  are chosen in ``skills/sources.json`` only.
* Results drop the upstream ``raw`` copy (the same content again), and errors are explained in Indonesian with the
  next step. It never logs in for the user and never asks for a password: a login is a command the user runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from brain.utils import cache
from brain.utils.logger import get_logger

logger = get_logger(__name__)

REPO = "Panniantong/Agent-Reach"
RELEASE_ZIP = f"https://github.com/{REPO}/releases/latest/download/agent-reach.zip"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DEFAULT_TIMEOUT = 180
SETUP_TIMEOUT = 1200
# Sites Agent Reach reads better than a plain page fetch (subtitles, threads with comments, issues, posts).
HOSTS = (
    "youtube.com", "youtu.be", "reddit.com", "redd.it", "x.com", "twitter.com", "bilibili.com", "b23.tv",
    "v2ex.com", "github.com", "linkedin.com", "xiaohongshu.com", "xhslink.com", "xueqiu.com", "instagram.com",
    "facebook.com", "fb.watch", "xiaoyuzhoufm.com", "zhipin.com",
)
_channels_cache = cache.cache("agent-reach-channels", max_entries=1, ttl=3600)

# What to tell the user for each error code, and what the agent must not do.
ADVICE = {
    "NEED_SETUP": "Channel ini belum dipasang. Pasang dengan reach_manage(op='add', channel=...) atau "
                  "`agent-reach add <channel>` di Terminal; kalau butuh key, kamu yang mengisinya dengan "
                  "`agent-reach configure <key>` di Terminal, jangan ditempel di chat.",
    "NEED_LOGIN": "Butuh login di situs itu. Jalankan `agent-reach login <channel>` di Terminal SquidWings lalu login "
                  "sendiri di jendela yang terbuka (sebaiknya akun cadangan). AI tidak pernah login atas namamu.",
    "UPSTREAM_BROKEN": "Platform ini sedang bermasalah dari sisinya. Coba lagi nanti; jangan diulang terus.",
    "TIMEOUT": "Terlalu lama. Cek status dengan reach_status; jangan diulang terus.",
    "RATE_LIMITED": "Platform membatasi permintaan. Tunggu sesuai retry_after_seconds sebelum mencoba lagi.",
    "PLATFORM_BLOCKED": "Platform meminta verifikasi (captcha). Selesaikan sendiri di situs resminya, lalu coba lagi.",
    "BUSY": "Browser Agent Reach sedang penuh atau jendela login masih terbuka. Coba lagi sebentar lagi.",
    "NOT_FOUND": "Kontennya tidak ada. Cek lagi tautan atau ID-nya.",
    "BAD_INPUT": "Argumennya salah. Lihat aksi dan parameter yang benar dengan reach_status(channels=True).",
    "NOT_INSTALLED": "Agent Reach belum terpasang. Pasang dengan `python main.py reach install`, atau minta AI "
                     "menjalankan reach_manage(op='install').",
}


def enabled() -> bool:
    """``AGENT_REACH=off`` turns it off. Tests leave it off unless they ask (they must not reach the network)."""
    value = os.getenv("AGENT_REACH", "").strip().lower()
    if value in {"0", "off", "false", "no"}:
        return False
    return "pytest" not in sys.modules or value in {"1", "on", "true", "yes"}


def binary() -> str | None:
    """The agent-reach program: on PATH, or where ``uv tool install`` puts it."""
    found = shutil.which("agent-reach")
    if found:
        return found
    name = "agent-reach.exe" if os.name == "nt" else "agent-reach"
    candidate = Path.home() / ".local" / "bin" / name
    return str(candidate) if candidate.is_file() else None


def installed() -> bool:
    return enabled() and binary() is not None


def _parse(stdout: str) -> dict[str, Any] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            return None
        try:
            data = json.loads(text[start:])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def run(args: list[str], timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run ``agent-reach <args>`` and return its JSON envelope. Never raises: a failure is an ``ok: false``."""
    program = binary() if enabled() else None
    if program is None:
        return {"ok": False, "error": {"code": "NOT_INSTALLED", "message": "agent-reach is not installed"}}
    env = {**os.environ, "AGENT_REACH_LANG": "en", "PYTHONIOENCODING": "utf-8"}
    try:
        done = subprocess.run([program, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "TIMEOUT", "message": f"agent-reach did not answer within {int(timeout)}s"}}
    except OSError as exc:
        return {"ok": False, "error": {"code": "UPSTREAM_BROKEN", "message": f"agent-reach could not start: {exc}"}}
    data = _parse(done.stdout)
    if data is None:
        tail = ((done.stderr or "") + (done.stdout or "")).strip()[-600:]
        return {"ok": False, "error": {"code": "UPSTREAM_BROKEN", "message": tail or f"exit code {done.returncode}"}}
    return data


def explain(result: dict[str, Any]) -> dict[str, Any]:
    """The envelope as the agent sees it: no duplicate ``raw`` data, and an Indonesian next step on failure."""
    out = dict(result)
    items = out.get("items")
    if isinstance(items, list):
        out["items"] = [{k: v for k, v in item.items() if k != "raw"} if isinstance(item, dict) else item
                        for item in items]
    error = out.get("error")
    if not out.get("ok") and isinstance(error, dict):
        code = str(error.get("code") or "")
        out["advice"] = ADVICE.get(code, "Agent Reach gagal; lihat error.message. Jangan diulang terus.")
    return out


# Sites where a plain page fetch gets little (a player, a login wall, a script shell): Agent Reach reads them first.
_PREFER = ("youtube.com", "youtu.be", "reddit.com", "redd.it", "x.com", "twitter.com", "bilibili.com", "b23.tv",
           "xiaohongshu.com", "xhslink.com", "instagram.com", "facebook.com", "fb.watch", "linkedin.com",
           "xiaoyuzhoufm.com", "zhipin.com")


def prefers(url: str) -> bool:
    """Whether Agent Reach should read this link before the plain fetch does."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in _PREFER)


def forget_channels() -> None:
    _channels_cache.clear()


def handles(url: str) -> bool:
    """Whether Agent Reach has a channel for this link."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in HOSTS)


def channels() -> list[dict[str, Any]]:
    """The channels with their actions and parameters, from the installed version (cached for an hour)."""
    cached = _channels_cache.get("all")
    if cached is not None:
        return list(cached)
    if not installed():
        return []
    data = run(["channels"], timeout=60)
    found = [c for c in data.get("channels") or [] if isinstance(c, dict)] if data.get("ok") else []
    if found:
        _channels_cache.set("all", found)
    return found


def action_argv(channel: str, action: str, params: dict[str, Any] | None) -> list[str]:
    """``agent-reach <channel> <action>`` arguments: required parameters positionally after ``--``, the others as
    ``--name value``. Parameter names come from the installed version, so new upstream actions work unchanged."""
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    spec = next((a for c in channels() if c.get("name") == channel for a in c.get("actions") or []
                 if a.get("name") == action), None)
    required = [p["name"] for p in (spec or {}).get("params") or [] if p.get("required")]
    if spec is None:
        # Not listed yet (cache, or a new version): the usual names of a required argument.
        required = [k for k in ("query", "target", "url", "id", "user", "symbol") if k in params][:1]
    options: list[str] = []
    for key, value in params.items():
        if key in required:
            continue
        options += [f"--{key}", str(value)]
    positional = [str(params[k]) for k in required if k in params]
    return [channel, action, *options, "--", *positional] if positional else [channel, action, *options]


def read(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    return explain(run(["read", "--timeout", str(int(timeout)), url], timeout=timeout + 30))


def keep_to_squidwings() -> list[str]:
    """Take Agent Reach's skill out of other AIs' skill folders (Claude Code, Cursor...): SquidWings uses it through
    its own tools, and the owner picks Claude Code's skills in skills/sources.json. Unlinked once, it stays out
    across updates (Agent Reach remembers). Returns the folders it was taken out of."""
    status = run(["skill"], timeout=60)
    result = status.get("result") if isinstance(status.get("result"), dict) else status
    paths = [*(result.get("links") or []), *(result.get("copies") or [])] if isinstance(result, dict) else []
    real = Path(str(result.get("path") or "")).parent if isinstance(result, dict) else Path()
    removed = []
    for path in paths:
        target = Path(path)
        folder = str(target.parent)
        done = run(["skill", "unlink", folder], timeout=60)
        if done.get("ok"):
            removed.append(folder)
            continue
        # Older releases have no "skill unlink": take out only the link Agent Reach made to its own copy.
        try:
            if target.is_symlink() and real.name and Path(os.path.realpath(target)) == Path(os.path.realpath(real)):
                target.unlink()
                removed.append(folder)
        except OSError as exc:
            logger.warning(f"[reach] Could not take the skill out of {folder}: {exc}")
    return removed


def install() -> dict[str, Any]:
    """Install from the GitHub release (never the unrelated PyPI package of the same name), then set up the
    default channels. Needs uv."""
    steps: list[dict[str, Any]] = []
    uv = shutil.which("uv")
    if uv is None:
        return {"ok": False, "steps": steps, "error": {"code": "NEED_SETUP", "message": "uv belum terpasang. Pasang "
                "dulu: curl -LsSf https://astral.sh/uv/install.sh | sh (Windows: powershell -c \"irm "
                "https://astral.sh/uv/install.ps1 | iex\"), lalu ulangi."}}
    try:
        done = subprocess.run([uv, "tool", "install", "--force", RELEASE_ZIP], capture_output=True, text=True,
                              timeout=SETUP_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "steps": steps, "error": {"code": "UPSTREAM_BROKEN", "message": str(exc)}}
    steps.append({"step": "uv tool install", "ok": done.returncode == 0,
                  "log": ((done.stderr or "") + (done.stdout or "")).strip()[-400:]})
    if done.returncode != 0 or not installed():
        return {"ok": False, "steps": steps, "error": {"code": "UPSTREAM_BROKEN", "message": steps[-1]["log"]}}
    setup = run(["setup"], timeout=SETUP_TIMEOUT)
    steps.append({"step": "agent-reach setup", "ok": bool(setup.get("ok"))})
    removed = keep_to_squidwings()
    forget_channels()
    report = ((setup.get("result") or {}).get("installation_report") or {}).get("markdown", "") \
        if isinstance(setup.get("result"), dict) else ""
    return {"ok": bool(setup.get("ok")), "steps": steps, "report": report, "skill_unlinked_from": removed,
            "error": setup.get("error")}


def update() -> dict[str, Any]:
    """Upgrade the program, every added channel's tools and its skill copy, then keep the skill out of other AIs."""
    if not installed():
        return {"ok": False, "error": {"code": "NOT_INSTALLED", "message": "agent-reach is not installed"}}
    result = run(["update"], timeout=SETUP_TIMEOUT)
    result["skill_unlinked_from"] = keep_to_squidwings()
    forget_channels()
    return result


def version() -> str | None:
    """The installed version, e.g. '1.6.1'. None when it is not installed."""
    if not installed():
        return None
    data = run(["version"], timeout=30)
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    value = (result or {}).get("version") if isinstance(result, dict) else None
    return str(value) if value else None


def latest_release() -> str | None:
    """The newest release tag on GitHub, e.g. '1.6.2'."""
    import httpx

    try:
        resp = httpx.get(LATEST_API, timeout=10.0, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        return str(resp.json().get("tag_name") or "").lstrip("v") or None
    except (httpx.HTTPError, ValueError):
        return None
