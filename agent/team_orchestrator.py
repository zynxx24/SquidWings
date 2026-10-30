"""Persistent multi-agent teams with parallel workers and a lead synthesis step."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent import session_stack
from agent.harness import Tier, tier_for
from agent.orchestrator import AgentResponse, Orchestrator
from config.constants import ResponseStatus
from utils.logger import get_logger

logger = get_logger(__name__)
MAX_TEAM_MEMBERS = 6
MAX_CONTEXT_CHARS = 60_000
MAX_REVIEW_ROUNDS = 2
_MEMBER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|REVISE)", re.IGNORECASE)


@dataclass
class TeamMember:
    id: str
    name: str
    role: str
    model_id: str
    instructions: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Team:
    id: str
    name: str
    description: str
    members: list[TeamMember]
    lead_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    reviewer_id: str | None = None


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
    reviews: list[dict[str, Any]] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}


def execution_layers(members: list[TeamMember]) -> list[list[TeamMember]]:
    """Group members into layers that can run in parallel, in dependency order.

    Dependencies on members outside the list (the lead, the reviewer) are
    ignored, because those always run after the workers.

    Raises:
        ValueError: If the dependencies contain a cycle.
    """
    ids = {member.id for member in members}
    pending = {m.id: {d for d in m.depends_on if d in ids} for m in members}
    by_id = {member.id: member for member in members}
    layers: list[list[TeamMember]] = []
    while pending:
        ready = [member_id for member_id, deps in pending.items() if not deps]
        if not ready:
            names = ", ".join(by_id[member_id].name for member_id in pending)
            raise ValueError(f"The flow has a cycle between: {names}.")
        layers.append([by_id[member_id] for member_id in ready])
        for member_id in ready:
            del pending[member_id]
        for deps in pending.values():
            deps.difference_update(ready)
    return layers


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


def team_templates(model_ids: list[str]) -> list[dict[str, Any]]:
    """Ready-made teams, with models chosen by tier from what is registered.

    The pattern throughout: cheap models do the volume of work, and the one
    strong model is spent where it pays most, on judging the result.
    """
    if not model_ids:
        return []

    def pick(*preferred: Tier) -> str:
        for tier in preferred:
            for model_id in model_ids:
                if tier_for(model_id) == tier:
                    return model_id
        return model_ids[0]

    lite = pick(Tier.LITE, Tier.STANDARD, Tier.FRONTIER)
    mid = pick(Tier.STANDARD, Tier.LITE, Tier.FRONTIER)
    top = pick(Tier.FRONTIER, Tier.STANDARD, Tier.LITE)

    def member(id_: str, name: str, role: str, model: str, *after: str) -> dict[str, Any]:
        return {"id": id_, "name": name, "role": role, "model_id": model, "instructions": "", "depends_on": list(after)}

    return [
        {
            "key": "build",
            "name": "Bangun dan verifikasi",
            "description": "Model murah merencanakan, membangun, dan menguji. Model kuat menilai hasilnya.",
            "members": [
                member("planner", "Perencana", "Memecah tugas menjadi langkah kecil yang bisa dicek", mid),
                member("builder", "Pembangun", "Menulis kode dan file sesuai rencana", lite, "planner"),
                member("tester", "Penguji", "Menjalankan hasilnya dan melaporkan yang benar-benar rusak", lite, "builder"),
                member("lead", "Lead", "Memperbaiki temuan penguji dan menulis jawaban akhir", mid),
                member("reviewer", "Reviewer", "Memeriksa hasil terhadap tugas secara independen", top),
            ],
            "lead_id": "lead",
            "reviewer_id": "reviewer",
        },
        {
            "key": "research",
            "name": "Riset dan tulis",
            "description": "Dua peneliti bekerja paralel, penulis menyatukan, pemeriksa fakta memverifikasi.",
            "members": [
                member("sources", "Pencari sumber", "Mencari sumber utama dan fakta penting", lite),
                member("analyst", "Analis", "Mencari argumen tandingan dan celah", lite),
                member("writer", "Penulis", "Menyatukan riset menjadi satu jawaban yang jelas", mid),
                member("checker", "Pemeriksa fakta", "Mencocokkan klaim dengan sumbernya", top),
            ],
            "lead_id": "writer",
            "reviewer_id": "checker",
        },
        {
            "key": "second-opinion",
            "name": "Pendapat kedua",
            "description": "Model murah menjawab, model kuat memeriksa. Cara termurah menangkap kesalahan.",
            "members": [
                member("worker", "Pekerja", "Mengerjakan tugas dan menunjukkan alasannya", lite),
                member("reviewer", "Reviewer", "Mencari kesalahan dan meminta perbaikan", top),
            ],
            "lead_id": "worker",
            "reviewer_id": "reviewer",
        },
    ]


class TeamOrchestrator:
    """Runs a team's flow: workers in dependency order, the lead, then an optional review."""

    def __init__(self, orchestrator: Orchestrator, store: TeamStore) -> None:
        self.orchestrator = orchestrator
        self.store = store
        self.events = TeamEventHub()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    @property
    def run_tasks(self) -> dict[str, asyncio.Task[None]]:
        return self._tasks

    def create_team(
        self,
        name: str,
        description: str,
        members: list[dict[str, Any]],
        lead_id: str | None,
        reviewer_id: str | None = None,
    ) -> Team:
        parsed = self._validate_members(members, lead_id, reviewer_id)
        team = Team(
            id=f"team-{uuid.uuid4().hex[:12]}",
            name=name.strip(),
            description=description.strip(),
            members=parsed,
            lead_id=lead_id or parsed[0].id,
            reviewer_id=reviewer_id or None,
        )
        if not team.name:
            raise ValueError("Team name cannot be empty.")
        self.store.put_team(team)
        return team

    def _validate_members(
        self,
        members: list[dict[str, Any]],
        lead_id: str | None,
        reviewer_id: str | None = None,
    ) -> list[TeamMember]:
        if not 2 <= len(members) <= MAX_TEAM_MEMBERS:
            raise ValueError(f"A team needs 2–{MAX_TEAM_MEMBERS} members.")
        seen: set[str] = set()
        parsed: list[TeamMember] = []
        for member in members:
            member_id = member.get("id") or uuid.uuid4().hex[:10]
            # Ends up in a session id, and from there in a file name.
            if not _MEMBER_ID_RE.fullmatch(member_id):
                raise ValueError("Member IDs may only contain letters, digits, _ and -.")
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
                depends_on=[str(d) for d in member.get("depends_on") or []],
            ))
        if any(not item.name or not item.role for item in parsed):
            raise ValueError("Every member needs a name and role.")
        if lead_id and lead_id not in seen:
            raise ValueError("The lead must be a team member.")
        if reviewer_id:
            if reviewer_id not in seen:
                raise ValueError("The reviewer must be a team member.")
            if reviewer_id == (lead_id or parsed[0].id):
                raise ValueError("The lead cannot review its own work.")
        for item in parsed:
            if item.id in item.depends_on:
                raise ValueError(f"{item.name} cannot depend on itself.")
            if any(d not in seen for d in item.depends_on):
                raise ValueError(f"{item.name} depends on an unknown member.")
        execution_layers(parsed)
        return parsed

    async def update_team(
        self,
        team_id: str,
        name: str,
        description: str,
        members: list[dict[str, Any]],
        lead_id: str | None,
        reviewer_id: str | None = None,
    ) -> Team:
        if any(not run.finished for run in self.store.list_runs(team_id)):
            raise ValueError("Wait for active team runs to finish before editing.")
        existing = self.store.get_team(team_id)
        if existing is None:
            raise KeyError(team_id)
        parsed = self._validate_members(members, lead_id, reviewer_id)
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
            reviewer_id=reviewer_id or None,
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
        # Everyone in a run shares one folder, so one agent can read another's files.
        session_stack.enter(run.id)
        try:
            lead = next(member for member in team.members if member.id == team.lead_id)
            workers = [m for m in team.members if m.id not in {team.lead_id, team.reviewer_id}]
            for layer in execution_layers(workers):
                await asyncio.gather(*(self._run_member(run, team, m) for m in layer))
            if workers and not any(run.agents[m.id]["status"] == "completed" for m in workers):
                raise RuntimeError("All team members failed. Check their individual errors.")

            run.status = "synthesizing"
            self._checkpoint(run, "synthesis_started")
            await self._run_member(run, team, lead, mode="lead")
            await self._review_loop(run, team, lead)

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

    async def _review_loop(self, run: TeamRun, team: Team, lead: TeamMember) -> None:
        """Let the reviewer send the draft back until it passes or rounds run out."""
        reviewer = next((m for m in team.members if m.id == team.reviewer_id), None)
        if reviewer is None or run.agents[lead.id]["status"] != "completed":
            return
        for round_number in range(1, MAX_REVIEW_ROUNDS + 2):
            run.status = "reviewing"
            await self._run_member(run, team, reviewer, mode="reviewer", round_number=round_number)
            state = run.agents[reviewer.id]
            if state["status"] != "completed":
                # A broken reviewer must not lose the lead's finished answer.
                run.reviews.append({"round": round_number, "verdict": "skipped", "notes": state["error"]})
                return
            feedback = state["content"]
            match = _VERDICT_RE.search(feedback)
            verdict = match.group(1).upper() if match else "PASS"
            run.reviews.append({"round": round_number, "verdict": verdict, "notes": feedback[:4000]})
            self._checkpoint(run, "review_finished", reviewer.id)
            if verdict == "PASS" or round_number > MAX_REVIEW_ROUNDS:
                return
            run.status = "revising"
            await self._run_member(run, team, lead, mode="revision", feedback=feedback)
            if run.agents[lead.id]["status"] != "completed":
                return

    @staticmethod
    def _member_prompt(member: TeamMember, task: str, mode: str = "worker") -> str:
        instruction = f"\nAdditional instructions: {member.instructions}" if member.instructions else ""
        if mode == "lead":
            return (
                f"You are {member.name}, the lead of an AI team. Role: {member.role}.{instruction}\n"
                "The specialists have independently worked on the user's task below. "
                "Read their reports critically, resolve conflicts, and produce one complete final answer. "
                "Do not mention internal orchestration or invent work not present in the reports. "
                "If specialists failed, use the useful available reports and note important limitations."
            )
        if mode == "revision":
            return (
                f"You are {member.name}, the lead of an AI team. Role: {member.role}.{instruction}\n"
                "A reviewer rejected your draft. Fix every problem they list and return the complete "
                "corrected final answer, not just the changes. Do not mention the review."
            )
        if mode == "reviewer":
            return (
                f"You are {member.name}, an independent reviewer. Role: {member.role}.{instruction}\n"
                "Check the draft below against the user's task. If the team wrote files, open them "
                "and check them yourself. Look for wrong facts, missing requirements, and claims the "
                "work does not support. The first line of your reply must be exactly VERDICT: PASS or "
                "VERDICT: REVISE. If REVISE, list each concrete problem and how to fix it."
            )
        return (
            f"You are {member.name}, a specialist in an AI team. Role: {member.role}.{instruction}\n"
            "Independently analyze and complete your part of the user's task. Give concrete, "
            "actionable findings for the lead. Stay within your role, avoid repeating the whole task, "
            "and explicitly identify risks or missing information. Return only your report."
        )

    @staticmethod
    def _report(state: dict[str, Any]) -> str:
        body = state["content"] if state["status"] == "completed" else f"FAILED: {state['error']}"
        return f"## {state['name']} — {state['role']}\n{body}"

    def _member_context(
        self, run: TeamRun, team: Team, member: TeamMember, mode: str, feedback: str
    ) -> str:
        """The task plus only the reports this member should see."""
        context = f"[TEAM TASK]\n{run.task}"
        if mode in {"lead", "revision"}:
            excluded = {team.lead_id, team.reviewer_id}
            reports = "\n\n".join(
                self._report(state) for member_id, state in run.agents.items() if member_id not in excluded
            )
            if reports:
                context += f"\n\n[SPECIALIST REPORTS]\n{reports[-MAX_CONTEXT_CHARS:]}"
        if mode == "revision":
            draft = run.agents[team.lead_id]["content"]
            context += f"\n\n[YOUR DRAFT]\n{draft[-MAX_CONTEXT_CHARS:]}\n\n[REVIEW]\n{feedback[-MAX_CONTEXT_CHARS:]}"
        if mode == "reviewer":
            context += f"\n\n[DRAFT TO REVIEW]\n{run.agents[team.lead_id]['content'][-MAX_CONTEXT_CHARS:]}"
        if mode == "worker" and member.depends_on:
            reports = "\n\n".join(
                self._report(run.agents[d]) for d in member.depends_on if d in run.agents
            )
            context += f"\n\n[REPORTS FROM EARLIER STEPS]\n{reports[-MAX_CONTEXT_CHARS:]}"
        return context

    async def _run_member(
        self,
        run: TeamRun,
        team: Team,
        member: TeamMember,
        mode: str = "worker",
        feedback: str = "",
        round_number: int = 0,
    ) -> None:
        agent = run.agents[member.id]
        agent["status"] = "running"
        agent["error"] = ""
        agent["started_at"] = time.time()
        self._checkpoint(run, "agent_started", member.id)
        suffix = f"{mode}{round_number}" if round_number else mode
        try:
            response: AgentResponse = await self.orchestrator.process(
                user_input=self._member_prompt(member, run.task, mode),
                session_id=f"{run.id}-{member.id}-{suffix}",
                override_model=member.model_id,
                stream=False,
                workspace_context=self._member_context(run, team, member, mode, feedback),
                task_hint=run.task,
                persist=False,
            )
            if response.status not in {ResponseStatus.SUCCESS, ResponseStatus.FALLBACK_USED}:
                raise RuntimeError(response.error_message or response.content or "Model request failed")
            agent.update(
                status="completed", content=response.content, model_id=response.model_used,
                tokens=agent.get("tokens", 0) + response.total_tokens,
                latency_ms=round(response.latency_ms, 1),
                tools=len(response.tool_calls),
            )
            run.total_tokens += response.total_tokens
        except asyncio.CancelledError:
            agent["status"] = "cancelled"
            raise
        except Exception as exc:
            logger.warning("[teams] %s failed in %s: %s", member.name, run.id, exc)
            agent.update(status="failed", error=str(exc))
            if mode == "lead":
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
