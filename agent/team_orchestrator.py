"""Persistent multi-agent teams with parallel workers and a lead synthesis step."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.orchestrator import AgentResponse, Orchestrator
from config.constants import ResponseStatus
from utils.logger import get_logger

logger = get_logger(__name__)
MAX_TEAM_MEMBERS = 6
MAX_CONTEXT_CHARS = 60_000


@dataclass
class TeamMember:
    id: str
    name: str
    role: str
    model_id: str
    instructions: str = ""


@dataclass
class Team:
    id: str
    name: str
    description: str
    members: list[TeamMember]
    lead_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class TeamRun:
    id: str
    team_id: str
    task: str
    status: str = "queued"
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    total_tokens: int = 0

    @property
    def finished(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}


class TeamEventHub:
    """Small in-process pub/sub hub. Events are replayed to reconnecting browsers."""

    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        history = self._history.setdefault(run_id, [])
        history.append(event)
        del history[:-100]
        for queue in tuple(self._subscribers.get(run_id, ())):
            queue.put_nowait(event)

    def subscribe(self, run_id: str) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=120)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue, list(self._history.get(run_id, ()))

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)


class TeamStore:
    """Atomic JSON persistence; no database or extra dependency required."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.runs_directory = directory / "runs"
        self.runs_directory.mkdir(parents=True, exist_ok=True)
        self._teams: dict[str, Team] = {}
        self._runs: dict[str, TeamRun] = {}
        self._load()

    @staticmethod
    def _team_from_dict(data: dict[str, Any]) -> Team:
        data = dict(data)
        data["members"] = [TeamMember(**member) for member in data.get("members", [])]
        return Team(**data)

    def _load(self) -> None:
        path = self.directory / "teams.json"
        try:
            if path.exists():
                items = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(items, list):
                    raise TypeError("teams.json must contain a list")
                for item in items:
                    team = self._team_from_dict(item)
                    self._teams[team.id] = team
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.error("[teams] Could not load teams: %s", exc)

        for path in self.runs_directory.glob("*.json"):
            try:
                run = TeamRun(**json.loads(path.read_text(encoding="utf-8")))
                if not run.finished:
                    run.status = "failed"
                    run.error = "Server restarted before this run finished."
                    run.completed_at = time.time()
                self._runs[run.id] = run
            except (OSError, ValueError, TypeError, KeyError) as exc:
                # One damaged run must not hide other valid runs.
                logger.error("[teams] Could not load run %s: %s", path.name, exc)

    @staticmethod
    def _atomic_write(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def save_teams(self) -> None:
        self._atomic_write(self.directory / "teams.json", [asdict(t) for t in self._teams.values()])

    def save_run(self, run: TeamRun) -> None:
        self._atomic_write(self.runs_directory / f"{run.id}.json", asdict(run))

    def list_teams(self) -> list[Team]:
        return sorted(self._teams.values(), key=lambda team: team.updated_at, reverse=True)

    def get_team(self, team_id: str) -> Team | None:
        return self._teams.get(team_id)

    def put_team(self, team: Team) -> None:
        self._teams[team.id] = team
        self.save_teams()

    def delete_team(self, team_id: str) -> list[str]:
        self._teams.pop(team_id, None)
        self.save_teams()
        run_ids = [run.id for run in self._runs.values() if run.team_id == team_id]
        for run_id in run_ids:
            self._runs.pop(run_id, None)
            (self.runs_directory / f"{run_id}.json").unlink(missing_ok=True)
        return run_ids

    def get_run(self, run_id: str) -> TeamRun | None:
        return self._runs.get(run_id)

    def list_runs(self, team_id: str) -> list[TeamRun]:
        return sorted(
            (run for run in self._runs.values() if run.team_id == team_id),
            key=lambda run: run.created_at,
            reverse=True,
        )

    def put_run(self, run: TeamRun) -> None:
        self._runs[run.id] = run
        self.save_run(run)


class TeamOrchestrator:
    """Runs specialists concurrently, then asks the selected lead to synthesize."""

    def __init__(self, orchestrator: Orchestrator, store: TeamStore) -> None:
        self.orchestrator = orchestrator
        self.store = store
        self.events = TeamEventHub()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    @property
    def run_tasks(self) -> dict[str, asyncio.Task[None]]:
        return self._tasks

    def create_team(self, name: str, description: str, members: list[dict[str, Any]], lead_id: str | None) -> Team:
        if not 2 <= len(members) <= MAX_TEAM_MEMBERS:
            raise ValueError(f"A team needs 2–{MAX_TEAM_MEMBERS} members.")
        seen: set[str] = set()
        parsed: list[TeamMember] = []
        for member in members:
            member_id = member.get("id") or uuid.uuid4().hex[:10]
            if member_id in seen:
                raise ValueError("Member IDs must be unique.")
            seen.add(member_id)
            model_id = member["model_id"]
            if self.orchestrator.registry.get(model_id) is None:
                raise ValueError(f"Unknown model: {model_id}")
            parsed.append(TeamMember(
                id=member_id,
                name=str(member["name"]).strip(),
                role=str(member["role"]).strip(),
                model_id=model_id,
                instructions=str(member.get("instructions", "")).strip(),
            ))
        if any(not member.name or not member.role for member in parsed):
            raise ValueError("Every member needs a name and role.")
        if lead_id and lead_id not in seen:
            raise ValueError("The lead must be a team member.")
        team = Team(
            id=f"team-{uuid.uuid4().hex[:12]}",
            name=name.strip(),
            description=description.strip(),
            members=parsed,
            lead_id=lead_id or parsed[0].id,
        )
        if not team.name:
            raise ValueError("Team name cannot be empty.")
        self.store.put_team(team)
        return team

    def _validate_members(
        self, members: list[dict[str, Any]], lead_id: str | None
    ) -> list[TeamMember]:
        if not 2 <= len(members) <= MAX_TEAM_MEMBERS:
            raise ValueError(f"A team needs 2–{MAX_TEAM_MEMBERS} members.")
        seen: set[str] = set()
        parsed: list[TeamMember] = []
        for member in members:
            member_id = member.get("id") or uuid.uuid4().hex[:10]
            if member_id in seen:
                raise ValueError("Member IDs must be unique.")
            seen.add(member_id)
            model_id = member["model_id"]
            if self.orchestrator.registry.get(model_id) is None:
                raise ValueError(f"Unknown model: {model_id}")
            parsed.append(TeamMember(
                id=member_id,
                name=str(member["name"]).strip(),
                role=str(member["role"]).strip(),
                model_id=model_id,
                instructions=str(member.get("instructions", "")).strip(),
            ))
        if any(not member.name or not member.role for member in parsed):
            raise ValueError("Every member needs a name and role.")
        if lead_id and lead_id not in seen:
            raise ValueError("The lead must be a team member.")
        return parsed

    async def update_team(self, team_id: str, name: str, description: str, members: list[dict[str, Any]], lead_id: str | None) -> Team:
        if any(not run.finished for run in self.store.list_runs(team_id)):
            raise ValueError("Wait for active team runs to finish before editing.")
        existing = self.store.get_team(team_id)
        if existing is None:
            raise KeyError(team_id)
        parsed = self._validate_members(members, lead_id)
        if not name.strip():
            raise ValueError("Team name cannot be empty.")
        replacement = Team(
            id=team_id,
            name=name.strip(),
            description=description.strip(),
            members=parsed,
            lead_id=lead_id or parsed[0].id,
            created_at=existing.created_at,
            updated_at=time.time(),
        )
        self.store.put_team(replacement)
        return replacement

    async def delete_team(self, team_id: str) -> bool:
        if self.store.get_team(team_id) is None:
            return False
        for run_id in self.store.delete_team(team_id):
            task = self._tasks.pop(run_id, None)
            if task:
                task.cancel()
        return True

    async def start_run(self, team_id: str, task: str) -> TeamRun:
        async with self._lock:
            team = self.store.get_team(team_id)
            if team is None:
                raise KeyError(team_id)
            if any(not run.finished for run in self.store.list_runs(team_id)):
                raise ValueError("This team already has an active run.")
            now = time.time()
            run = TeamRun(
                id=f"run-{uuid.uuid4().hex[:12]}", team_id=team_id, task=task.strip(),
                agents={member.id: {
                    "member_id": member.id, "name": member.name, "role": member.role,
                    "model_id": member.model_id, "status": "queued", "content": "",
                    "error": "", "tokens": 0, "latency_ms": 0,
                    "started_at": None, "completed_at": None,
                } for member in team.members},
            )
            run.created_at = now
            self.store.put_run(run)
            runner = asyncio.create_task(self._execute_run(run.id), name=f"team-{run.id}")
            self._tasks[run.id] = runner
            runner.add_done_callback(lambda _: self._tasks.pop(run.id, None))
            return run

    async def _execute_run(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        team = self.store.get_team(run.team_id)
        if team is None:
            return
        run.status = "running"
        run.started_at = time.time()
        self._checkpoint(run, "run_started")
        try:
            workers = [
                self._run_member(run, team, member, is_lead=False)
                for member in team.members
            ]
            await asyncio.gather(*workers)
            successful = [a for a in run.agents.values() if a["status"] == "completed"]
            if not successful:
                raise RuntimeError("All team members failed. Check their individual errors.")
            run.status = "synthesizing"
            self._checkpoint(run, "synthesis_started")
            lead = next(member for member in team.members if member.id == team.lead_id)
            await self._run_member(run, team, lead, is_lead=True)
            lead_agent = run.agents[lead.id]
            if lead_agent["status"] != "completed":
                raise RuntimeError(lead_agent["error"] or "Lead synthesis failed.")
            run.result = lead_agent["content"]
            run.status = "completed"
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.error = "Run cancelled by user."
            for agent in run.agents.values():
                if agent["status"] in {"queued", "running"}:
                    agent["status"] = "cancelled"
            raise
        except Exception as exc:
            logger.exception("[teams] Run %s failed", run_id)
            run.status = "failed"
            run.error = str(exc)
        finally:
            run.completed_at = time.time()
            self._checkpoint(run, "run_finished")

    @staticmethod
    def _member_prompt(member: TeamMember, task: str, is_lead: bool) -> str:
        instruction = f"\nAdditional instructions: {member.instructions}" if member.instructions else ""
        if is_lead:
            return (
                f"You are {member.name}, the lead of an AI team. Role: {member.role}.{instruction}\n"
                "The specialists have independently worked on the user's task below. "
                "Read their reports critically, resolve conflicts, and produce one complete final answer. "
                "Do not mention internal orchestration or invent work not present in the reports. "
                "If specialists failed, use the useful available reports and note important limitations."
            )
        return (
            f"You are {member.name}, a specialist in an AI team. Role: {member.role}.{instruction}\n"
            "Independently analyze and complete your part of the user's task. Give concrete, "
            "actionable findings for the lead. Stay within your role, avoid repeating the whole task, "
            "and explicitly identify risks or missing information. Return only your report."
        )

    async def _run_member(self, run: TeamRun, team: Team, member: TeamMember, is_lead: bool) -> None:
        agent = run.agents[member.id]
        agent["status"] = "running"
        agent["started_at"] = time.time()
        self._checkpoint(run, "agent_started", member.id)
        prompt = self._member_prompt(member, run.task, is_lead)
        workspace_context = f"[TEAM TASK]\n{run.task}"
        if is_lead:
            reports = "\n\n".join(
                f"## {state['name']} — {state['role']}\n"
                + (state["content"] if state["status"] == "completed" else f"FAILED: {state['error']}")
                for state in run.agents.values()
            )
            workspace_context += f"\n\n[SPECIALIST REPORTS]\n{reports[-MAX_CONTEXT_CHARS:]}"
        try:
            response: AgentResponse = await self.orchestrator.process(
                user_input=prompt,
                session_id=f"{run.id}-{member.id}-{'lead' if is_lead else 'worker'}",
                override_model=member.model_id,
                stream=False,
                workspace_context=workspace_context,
            )
            if response.status not in {ResponseStatus.SUCCESS, ResponseStatus.FALLBACK_USED}:
                raise RuntimeError(response.error_message or response.content or "Model request failed")
            agent.update(
                status="completed", content=response.content, model_id=response.model_used,
                tokens=response.total_tokens, latency_ms=round(response.latency_ms, 1),
            )
            run.total_tokens += response.total_tokens
        except asyncio.CancelledError:
            agent["status"] = "cancelled"
            raise
        except Exception as exc:
            logger.warning("[teams] %s failed in %s: %s", member.name, run.id, exc)
            agent.update(status="failed", error=str(exc))
            if is_lead:
                raise
        finally:
            agent["completed_at"] = time.time()
            self._checkpoint(run, "agent_finished", member.id)

    async def cancel_run(self, run_id: str) -> TeamRun:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if not run.finished:
            task = self._tasks.get(run_id)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            else:
                run.status = "cancelled"
                run.completed_at = time.time()
                self._checkpoint(run, "run_finished")
        return run

    def _checkpoint(self, run: TeamRun, event: str, member_id: str | None = None) -> None:
        self.store.save_run(run)
        payload: dict[str, Any] = {"type": event, "run": asdict(run)}
        if member_id:
            payload["member_id"] = member_id
        self.events.publish(run.id, payload)

    async def stream_events(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        queue, history = self.events.subscribe(run_id)
        try:
            for event in history:
                yield event
                if event.get("type") == "run_finished":
                    return
            run = self.store.get_run(run_id)
            if run and run.finished:
                yield {"type": "run_finished", "run": asdict(run)}
                return
            while True:
                event = await queue.get()
                yield event
                if event.get("type") == "run_finished":
                    break
        finally:
            self.events.unsubscribe(run_id, queue)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
