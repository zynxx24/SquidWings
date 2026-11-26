"""Agent Reach tools: read and search the platforms web search misses (YouTube subtitles, Reddit, X, GitHub issues,
Bilibili, V2EX, LinkedIn jobs, Xueqiu...). The integration and its rules live in ``brain.utils.agent_reach``."""

from __future__ import annotations

import asyncio
from typing import Any

from brain.utils import agent_reach

from .catalog import group, param, spec

group("reach", "Agent Reach: YouTube subtitles and transcripts, Reddit, X/Twitter, GitHub issues and PRs, Bilibili, "
      "V2EX, LinkedIn jobs, Xueqiu quotes, Xiaohongshu",
      "youtube, yt, subtitle, transkrip, transcript, reddit, subreddit, twitter, tweet, x.com, thread, github issue, "
      "pull request, bilibili, v2ex, linkedin, lowongan, loker, job, xueqiu, saham china, xiaohongshu, xhs, "
      "instagram, facebook, podcast, komentar video, comments, agent reach")

_READ_ONLY = ("Read only: never posts, comments, likes or messages. Never logs in for the user or asks for a password; "
              "on NEED_LOGIN tell the user the command from `advice` and keep any results already found.")


@spec("reach_read", "Read one link from YouTube (title, description, subtitles), Reddit (post with comments), "
      "X/Twitter, GitHub (repo, issue or PR with comments), Bilibili, V2EX, LinkedIn jobs and more, with the "
      "platform's own tool, when web_read misses the body, the comments or the subtitles. Returns items with title, "
      "url, author, published_at and text. " + _READ_ONLY,
      "reach", [param("url", description="The link to read."),
                param("timeout", "int", "Seconds to wait (default 180).", required=False, default=180)],
      keywords="baca link youtube, subtitle youtube, transkrip video, baca reddit, baca tweet, baca issue github, "
               "read youtube, read reddit, read tweet", lead=True, network=True)
async def reach_read(url: str, timeout: int = 180) -> dict[str, Any]:
    return await asyncio.to_thread(agent_reach.read, url.strip(), max(10, min(int(timeout or 180), 900)))


@spec("reach", "Run one Agent Reach action on a platform: search, trending, subtitles, comments, user posts, quotes, "
      "jobs... e.g. channel='youtube', action='search', params={'query': 'laravel tutorial', 'limit': 5}; "
      "channel='reddit', action='search', params={'query': 'best budget phone'}; channel='github', action='search'. "
      "reach_status(channels=True) lists every channel's actions and parameters. " + _READ_ONLY,
      "reach", [param("channel", description="Platform: youtube, reddit, twitter, github, bilibili, v2ex, linkedin, "
                                              "xueqiu, xiaohongshu, boss, instagram, facebook, xiaoyuzhou, web."),
                param("action", description="The action, e.g. search, read, subtitles, comments, trending."),
                param("params", "obj", "The action's parameters by name, e.g. {'query': '...', 'limit': 5}.",
                      required=False),
                param("timeout", "int", "Seconds to wait (default 180).", required=False, default=180)],
      keywords="cari di youtube, cari di reddit, cari tweet, trending, komentar, lowongan linkedin, search youtube, "
               "search reddit, search twitter", lead=True, network=True)
async def reach(channel: str, action: str, params: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
    wait = max(10, min(int(timeout or 180), 900))
    argv = await asyncio.to_thread(agent_reach.action_argv, channel.strip().lower(), action.strip().lower(), params or {})
    argv[2:2] = ["--timeout", str(wait)]
    result = await asyncio.to_thread(agent_reach.run, argv, wait + 30)
    return agent_reach.explain(result)


@spec("reach_status", "Whether Agent Reach is installed, its version, which channels work now (and which worked "
      "before but broke), and with channels=True every channel's actions and parameters.",
      "reach", [param("channels", "bool", "Also list every channel's actions and parameters.", required=False,
                      default=False)],
      keywords="status agent reach, channel apa saja, agent reach doctor", network=False)
async def reach_status(channels: bool = False) -> dict[str, Any]:
    if not agent_reach.installed():
        return {"ok": False, "installed": False, "advice": agent_reach.ADVICE["NOT_INSTALLED"]}
    doctor = await asyncio.to_thread(agent_reach.run, ["doctor"], 120)
    out: dict[str, Any] = {"ok": bool(doctor.get("ok")), "installed": True,
                           "version": await asyncio.to_thread(agent_reach.version), "doctor": doctor.get("result", doctor)}
    if channels:
        out["channels"] = [
            {"name": c.get("name"), "level": c.get("level"), "description": c.get("description"),
             "actions": [{"name": a.get("name"), "params": a.get("params")} for a in c.get("actions") or []]}
            for c in await asyncio.to_thread(agent_reach.channels)
        ]
    return out


@spec("reach_manage", "Manage Agent Reach when the user asks: install it, update it, add or remove a platform "
      "(installs that platform's tools), or live-check channels. Logging in is never done here: the user runs "
      "`agent-reach login <channel>` in the Terminal themselves.",
      "reach", [param("op", description="install, update, add, remove or check.",
                      enum=["install", "update", "add", "remove", "check"]),
                param("channel", description="The platform for add, remove or check.", required=False)],
      keywords="pasang agent reach, install agent reach, update agent reach, tambah reddit, tambah twitter, "
               "hapus channel", network=True, read_only=False)
async def reach_manage(op: str, channel: str = "") -> dict[str, Any]:
    op = op.strip().lower()
    if op == "install":
        return await asyncio.to_thread(agent_reach.install)
    if op == "update":
        return agent_reach.explain(await asyncio.to_thread(agent_reach.update))
    if op in {"add", "remove", "check"}:
        if op != "check" and not channel.strip():
            raise ValueError(f"{op} butuh nama channel")
        argv = [op, *([channel.strip().lower()] if channel.strip() else [])]
        result = agent_reach.explain(await asyncio.to_thread(agent_reach.run, argv, agent_reach.SETUP_TIMEOUT))
        agent_reach.forget_channels()
        return result
    raise ValueError("op harus install, update, add, remove atau check")
