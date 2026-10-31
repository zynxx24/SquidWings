"""Persistent multi-agent teams with parallel workers and a lead synthesis step."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent import session_stack
from agent.harness import RunLimits, Tier, tier_for
from agent.orchestrator import AgentResponse, Orchestrator
from agent.team_flow import (
    BRANCHES,
    BRANCHING_KINDS,
    NODE_KINDS,
    check_condition,
    clock_values,
    next_run_after,
    normalize_node_config,
    normalize_schedule,
    parse_decision,
    render_template,
    telegram_task,
    uses_ai,
)
from agent.team_outputs import (
    DATABASE_IDS,
    OutputError,
    fetch_telegram_updates,
    http_request,
    save_to_database,
    send_telegram,
)
from config.constants import ResponseStatus
from config.settings import get_settings
from utils.logger import get_logger
from utils.markdown_compact import fit_markdown
from utils.workspace_context import SESSIONS_ROOT

logger = get_logger(__name__)
MAX_TEAM_MEMBERS = 12
MAX_AGENTS = 6
SCHEDULE_CHECK_SECONDS = 30
TELEGRAM_IDLE_SECONDS = 30
# A schedule this late was missed, say the server was off. Running it now would surprise.
SCHEDULE_LATE_SECONDS = 300
GATE_TOKENS = 8_000
MAX_CONTEXT_CHARS = 24_000
MAX_DRAFT_CHARS = 40_000
MAX_REPORT_CHARS = 8_000
MAX_REVIEW_ROUNDS = 1
# Workers stop here, so the lead always has time to write the answer.
WORKER_SHARE_OF_RUN = 0.7
MIN_FINISH_SECONDS = 120
MIN_REVIEW_SECONDS = 90
# A model call can hang past the soft limit, which is only checked between rounds.
HARD_TIMEOUT_GRACE_SECONDS = 90
FINISHING_ROUNDS = {"lead": 4, "revision": 3, "reviewer": 3}
OUTPUT_KINDS = {"telegram", "database"}
# A node on the branch not taken has nothing to report, not even a failure.
REPORTABLE = {"completed", "failed"}
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
    kind: str = "agent"
    # Which way out of a gate this node belongs to: "A" (condition met) or "B".
    branch: str = ""
    config: dict[str, Any] = field(default_factory=dict)


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
    schedule: dict[str, Any] = field(default_factory=lambda: {"mode": "off"})
    last_scheduled_at: float | None = None


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
    # Time spent in delay nodes, which does not count against the run's budget.
    waited_seconds: float = 0.0
    # What started the run: {"source": "telegram", "chat_id": ...} or the schedule.
    trigger: dict[str, Any] = field(default_factory=dict)

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

    def node(id_: str, name: str, kind: str, config: dict[str, Any], *after: str, model: str = "") -> dict[str, Any]:
        return {"id": id_, "name": name, "role": name, "model_id": model, "kind": kind, "config": config, "depends_on": list(after)}

    real_answers = (
        "Jawab hanya dari yang benar-benar kamu cek. Pakai time_now untuk tanggal dan jam, dan web_research "
        "untuk fakta yang bisa berubah, seperti cuaca, berita, harga, atau jadwal. Sebutkan sumbernya. "
        "Kalau tidak bisa memastikan sesuatu, katakan begitu, jangan mengarang."
    )
    direct_style = "Tulis untuk dibaca di HP: singkat, langsung ke intinya, tanpa basa-basi."
    agenda = (
        "Sekarang {{day}}, {{date}} jam {{time}}.\n\n"
        "Jadwal tetap (ubah sesuai jadwalmu):\n"
        "- Senin-Jumat 05:00 bangun, 05:15 olahraga ringan 20 menit\n"
        "- Senin-Jumat 07:00 berangkat, 08:00-16:00 kerja atau kuliah\n"
        "- Selasa dan Kamis 19:30 belajar bahasa Inggris\n"
        "- Rabu 18:30 futsal\n"
        "- Sabtu 09:00 belanja mingguan, 13:00 bersih-bersih kamar\n"
        "- Minggu 06:00 lari pagi, 19:00 rencanakan minggu depan\n"
        "- Setiap hari 12:00 makan siang, 21:30 siapkan barang untuk besok, 22:30 tidur\n\n"
        "Catatan hari ini: {{task}}"
    )
    from_agenda = (
        "Ambil jadwal hanya dari [AGENDA] di laporan sebelumnya, jangan mengarang kegiatan. "
        "Pakai time_now untuk memastikan hari dan jam. "
    )

    return [
        {
            "key": "reminder",
            "name": "Alarm bangun dan pengingat jadwal",
            "description": (
                "Pagi: membangunkan, lalu alarm kedua 10 menit kemudian. Siang dan sore: mengingatkan "
                "jadwal berikutnya. Semua lewat Telegram dan tercatat di database."
            ),
            "members": [
                node("agenda", "Agenda", "set", {"text": f"[AGENDA]\n{agenda}"}),
                node(
                    "pagi", "Pagi dan bukan hari libur?", "gate",
                    {"combine": "and", "conditions": [
                        {"mode": "time", "start": "04:00", "end": "09:59", "days": [0, 1, 2, 3, 4, 5, 6]},
                        {"mode": "not_contains", "value": "libur, cuti, sakit"},
                    ]},
                    "agenda",
                ),
                {
                    **member("bangun", "Pembangun pagi", "Membangunkan dengan semangat dan menyebut jadwal hari ini", lite, "pagi"),
                    "branch": "A",
                    "instructions": (
                        f"{from_agenda}{direct_style} Buka dengan sapaan bangun yang hangat dan satu kalimat penyemangat. "
                        "Lalu daftar kegiatan hari ini berurutan jam, tandai yang pertama. Maksimal 8 baris."
                    ),
                },
                node("kirim_bangun", "Kirim alarm", "telegram", {"chat_id": ""}, "bangun"),
                node("tunda", "Tunda 10 menit", "delay", {"seconds": 600}, "kirim_bangun"),
                node(
                    "alarm2", "Alarm kedua", "set",
                    {"text": "Alarm kedua, jam {{time}}. Sudah bangun? Kegiatan pertama hari ini ada di pesan sebelumnya. Ayo mulai!"},
                    "tunda",
                ),
                node("kirim_alarm2", "Kirim alarm kedua", "telegram", {"chat_id": ""}, "alarm2"),
                {
                    **member("ingat", "Pengingat", "Mengingatkan kegiatan yang akan datang", lite, "pagi"),
                    "branch": "B",
                    "instructions": (
                        f"{from_agenda}{direct_style} Sebutkan kegiatan dalam 4 jam ke depan beserta jamnya, "
                        "dan apa yang perlu disiapkan. Kalau tidak ada, bilang waktunya bebas dan sebut kegiatan berikutnya."
                    ),
                },
                node("kirim_ingat", "Kirim pengingat", "telegram", {"chat_id": ""}, "ingat"),
                node("gabung", "Gabung jalur", "merge", {"mode": "any"}, "bangun", "ingat"),
                node("catat", "Catat ke database", "database", {"database": "sqlite", "table": "pengingat"}, "gabung"),
            ],
            "lead_id": None,
            "reviewer_id": None,
            "schedule": {
                "mode": "daily", "time": "05:00", "times": ["05:00", "12:00", "17:00", "21:00"],
                "days": [0, 1, 2, 3, 4, 5, 6], "task": "Tidak ada catatan khusus.",
            },
        },
        {
            "key": "telegram-bot",
            "name": "Bot Telegram serba bisa",
            "description": (
                "Berjalan saat kamu mengirim pesan ke bot. /jadwal, /cuaca, dan /catat masing-masing punya "
                "jalurnya, pesan lain dijawab biasa. Balasan dikirim ke chat yang sama."
            ),
            "members": [
                node("pilah", "Pilah perintah", "switch", {"rules": [
                    {"mode": "regex", "value": r"^/jadwal\b"},
                    {"mode": "regex", "value": r"^/cuaca\b"},
                    {"mode": "regex", "value": r"^/catat\b"},
                ]}),
                {
                    **member("jadwal", "Asisten jadwal", "Menyusun jadwal dari permintaan", lite, "pilah"),
                    "branch": "A", "instructions": f"Pakai time_now untuk tanggal dan jam. {direct_style}",
                },
                {
                    **member("cuaca", "Asisten cuaca", "Mencari cuaca terkini", mid, "pilah"),
                    "branch": "B", "instructions": f"{real_answers} {direct_style}",
                },
                {**node("simpan", "Simpan catatan", "database", {"database": "sqlite", "table": "catatan"}, "pilah"), "branch": "C"},
                node("oke", "Konfirmasi catatan", "set", {"text": "Dicatat ({{date}} {{time}}):\n{{input}}"}, "simpan"),
                {
                    **member("ngobrol", "Asisten umum", "Menjawab pesan lain", lite, "pilah"),
                    "branch": "D", "instructions": f"{real_answers} {direct_style}",
                },
                node("balas", "Balas di Telegram", "telegram", {"chat_id": ""}, "jadwal", "cuaca", "oke", "ngobrol"),
            ],
            "lead_id": None,
            "reviewer_id": None,
            "schedule": {"mode": "telegram", "keyword": "", "task": ""},
        },
        {
            "key": "assistant",
            "name": "Asisten sehari-hari",
            "description": "Mencari fakta nyata, memilih jalur, lalu mengirim ke Telegram dan menyimpan ke database.",
            "members": [
                {**member("facts", "Pencari fakta", "Mencari fakta nyata untuk permintaan ini", mid), "instructions": real_answers},
                node("wait", "Jeda", "delay", {"seconds": 5}, "facts"),
                node(
                    "urgent", "Ada yang mendesak?", "gate",
                    {"mode": "ai", "value": "Apakah ada hal mendesak yang perlu ditindak hari ini?"},
                    "wait", model=lite,
                ),
                {
                    **member("alert", "Asisten: mendesak", "Menulis peringatan dan tindakan yang perlu diambil", mid, "urgent"),
                    "branch": "A", "instructions": f"{real_answers} {direct_style} Mulai dari tindakan yang perlu diambil.",
                },
                {
                    **member("brief", "Asisten: ringkasan", "Menulis ringkasan yang ringan", mid, "urgent"),
                    "branch": "B", "instructions": f"{real_answers} {direct_style}",
                },
                node("send", "Kirim ke Telegram", "telegram", {"chat_id": ""}, "alert", "brief"),
                node("store", "Simpan ke database", "database", {"database": "sqlite", "table": "asisten"}, "send"),
            ],
            "lead_id": None,
            "reviewer_id": None,
            "schedule": {"mode": "off"},
        },
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
        self._scheduler: asyncio.Task[None] | None = None
        self._telegram: asyncio.Task[None] | None = None
        self._telegram_offset: int | None = None

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
        schedule: dict[str, Any] | None = None,
    ) -> Team:
        parsed = self._validate_members(members, lead_id, reviewer_id)
        team = Team(
            id=f"team-{uuid.uuid4().hex[:12]}",
            name=name.strip(),
            description=description.strip(),
            members=parsed,
            lead_id=self._lead_for(parsed, lead_id),
            reviewer_id=reviewer_id or None,
            schedule=normalize_schedule(schedule),
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
            raise ValueError(f"A team needs 2–{MAX_TEAM_MEMBERS} nodes.")
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
            kind = str(member.get("kind") or "agent")
            if kind not in NODE_KINDS:
                raise ValueError(f"Jenis node tidak dikenal: {kind}.")
            config = normalize_node_config(kind, member.get("config"), DATABASE_IDS)
            model_id = str(member.get("model_id") or "")
            needs_model = kind == "agent" or uses_ai(config)
            if needs_model and self.orchestrator.registry.get(model_id) is None:
                raise ValueError(f"Unknown model: {model_id}")
            branch = str(member.get("branch") or "")
            if branch and branch not in BRANCHES:
                raise ValueError("Jalur gate hanya A, B, C, atau D.")
            name = str(member["name"]).strip()
            parsed.append(TeamMember(
                id=member_id,
                name=name,
                role=str(member.get("role") or (name if kind != "agent" else "")).strip(),
                model_id=model_id,
                instructions=str(member.get("instructions", "")).strip(),
                depends_on=[str(d) for d in member.get("depends_on") or []],
                kind=kind,
                branch=branch,
                config=config,
            ))
        if any(not item.name or not item.role for item in parsed):
            raise ValueError("Every member needs a name and role.")
        agents = {item.id for item in parsed if item.kind == "agent"}
        if not agents:
            raise ValueError("Alur butuh minimal satu agent.")
        if len(agents) > MAX_AGENTS:
            raise ValueError(f"Maksimal {MAX_AGENTS} agent dalam satu tim.")
        if lead_id and lead_id not in seen:
            raise ValueError("The lead must be a team member.")
        if lead_id and lead_id not in agents:
            raise ValueError("Lead harus berupa agent.")
        if reviewer_id and reviewer_id in seen and reviewer_id not in agents:
            raise ValueError("Reviewer harus berupa agent.")
        if reviewer_id:
            if reviewer_id not in seen:
                raise ValueError("The reviewer must be a team member.")
            if reviewer_id == self._lead_for(parsed, lead_id):
                raise ValueError("The lead cannot review its own work.")
            if not self._lead_for(parsed, lead_id):
                raise ValueError("Reviewer butuh lead untuk diperiksa.")
        for item in parsed:
            if item.id in item.depends_on:
                raise ValueError(f"{item.name} cannot depend on itself.")
            if any(d not in seen for d in item.depends_on):
                raise ValueError(f"{item.name} depends on an unknown member.")
        execution_layers(parsed)
        return parsed

    @staticmethod
    def _lead_for(members: list[TeamMember], lead_id: str | None) -> str:
        """An all-agent team always has a lead. A flow can end without one."""
        if lead_id:
            return lead_id
        return members[0].id if all(m.kind == "agent" for m in members) else ""

    async def update_team(
        self,
        team_id: str,
        name: str,
        description: str,
        members: list[dict[str, Any]],
        lead_id: str | None,
        reviewer_id: str | None = None,
        schedule: dict[str, Any] | None = None,
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
            lead_id=self._lead_for(parsed, lead_id),
            created_at=existing.created_at,
            updated_at=time.time(),
            reviewer_id=reviewer_id or None,
            schedule=normalize_schedule(schedule),
            last_scheduled_at=existing.last_scheduled_at,
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
            shutil.rmtree(SESSIONS_ROOT / run_id, ignore_errors=True)
        return True

    async def start_run(self, team_id: str, task: str, trigger: dict[str, Any] | None = None) -> TeamRun:
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
                trigger=dict(trigger or {}),
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
            lead = next((member for member in team.members if member.id == team.lead_id), None)
            after = self._after_answer(team)
            workers = [
                m for m in team.members
                if m.id not in {team.lead_id, team.reviewer_id} and m.id not in after
            ]
            for layer in execution_layers(workers):
                if time.time() >= self._workers_until(run):
                    for skipped in layer:
                        run.agents[skipped.id].update(status="skipped", error="Waktu tim habis")
                    self._checkpoint(run, "agents_skipped")
                    continue
                await asyncio.gather(*(self._run_node(run, team, m) for m in layer))
            if workers and not any(run.agents[m.id]["status"] == "completed" for m in workers):
                raise RuntimeError("All team members failed. Check their individual errors.")

            lead_error: Exception | None = None
            if lead and self._is_active(run, team, lead):
                run.status = "synthesizing"
                self._checkpoint(run, "synthesis_started")
                try:
                    await self._run_member(run, team, lead, mode="lead")
                except Exception as exc:  # noqa: BLE001 - the workers' reports must survive this
                    lead_error = exc
                await self._review_loop(run, team, lead)
            elif lead:
                run.agents[lead.id]["status"] = "skipped"

            run.result = self._final_answer(run, team, workers, lead_error)
            run.status = "delivering" if after else run.status
            for layer in execution_layers([m for m in team.members if m.id in after]):
                await asyncio.gather(*(self._run_node(run, team, m, mode="after") for m in layer))
            self._finish_result(run, team)
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
    def _after_answer(team: Team) -> set[str]:
        """Nodes that run once the answer exists.

        That is anything following the lead or reviewer, plus any output node left
        unconnected: with no input of its own, the final answer is what it delivers.
        """
        after = {m.id for m in team.members if m.kind in OUTPUT_KINDS and not m.depends_on}
        anchors = {team.lead_id, team.reviewer_id} - {None, ""}
        changed = True
        while changed:
            changed = False
            for member in team.members:
                if member.id in after or member.id in anchors:
                    continue
                if any(d in after or d in anchors for d in member.depends_on):
                    after.add(member.id)
                    changed = True
        return after

    @staticmethod
    def _reached(run: TeamRun, kinds: dict[str, str], member: TeamMember, dependency: str) -> bool:
        """Whether a path from one dependency arrives here. A gate sends on one branch only."""
        state = run.agents[dependency]
        if state["status"] == "skipped":
            return False
        if kinds[dependency] in BRANCHING_KINDS:
            taken = state.get("branch")
            return bool(taken) and (not member.branch or member.branch == taken)
        return True

    @classmethod
    def _is_active(cls, run: TeamRun, team: Team, member: TeamMember) -> bool:
        """Whether a path reached this node. A merge set to "all" waits for every path."""
        if not member.depends_on:
            return True
        kinds = {m.id: m.kind for m in team.members}
        reached = [cls._reached(run, kinds, member, d) for d in member.depends_on]
        if member.kind == "merge" and member.config.get("mode") == "all":
            return all(reached)
        return any(reached)

    @staticmethod
    def _node_input(run: TeamRun, member: TeamMember) -> str:
        """What arrives at a node: its dependencies' text, else the answer.

        A failed Telegram still hands on what it was given, so the database after it
        saves the result. A failed agent has no content, so nothing false passes on.
        Only a node that starts the flow reads the task, and never an output: the task
        must not be sent on as if it were an answer.
        """
        parts = [
            run.agents[d]["content"] for d in member.depends_on
            if run.agents[d]["status"] in REPORTABLE and run.agents[d]["content"]
        ]
        text = "\n\n".join(dict.fromkeys(parts)) or run.result
        if member.kind in OUTPUT_KINDS or member.depends_on:
            return text
        return text or run.task

    def _final_answer(
        self, run: TeamRun, team: Team, workers: list[TeamMember], lead_error: Exception | None
    ) -> str:
        lead_state = run.agents.get(team.lead_id)
        if lead_state and lead_state["status"] == "completed":
            return str(lead_state["content"])
        agents = [m for m in workers if m.kind == "agent" and run.agents[m.id]["status"] == "completed"]
        if lead_state and lead_state["status"] == "failed" and agents:
            reason = lead_state["error"] or "kehabisan waktu"
            reports = "\n\n".join(self._report(run.agents[m.id]) for m in agents)
            return f"Lead tidak sempat menulis jawaban akhir ({reason}). Ini laporan para agent:\n\n{reports}"
        if lead_error:
            raise lead_error
        if lead_state and lead_state["status"] == "failed":
            raise RuntimeError(lead_state["error"] or "Lead synthesis failed.")
        # A flow without a lead ends on the last agent that ran: on an OR gate, the branch taken.
        return str(run.agents[agents[-1].id]["content"]) if agents else ""

    def _finish_result(self, run: TeamRun, team: Team) -> None:
        """An agent placed after the answer has the last word."""
        kinds = {m.id: m.kind for m in team.members}
        after = [
            m for m in team.members
            if m.id in self._after_answer(team) and kinds[m.id] == "agent" and run.agents[m.id]["status"] == "completed"
        ]
        if after:
            run.result = str(run.agents[after[-1].id]["content"])

    async def _ask_model(self, run: TeamRun, member: TeamMember, question: str, text: str, tag: str) -> bool | None:
        """Put a yes-or-no question about a text to the node's model."""
        response = await asyncio.wait_for(
            self.orchestrator.process(
                user_input=(
                    f"Pertanyaan: {question}\n"
                    "Baca teks di konteks, lalu jawab. Kata pertama harus YA atau TIDAK, lalu alasan satu kalimat."
                ),
                session_id=f"{run.id}-{member.id}-{tag}",
                override_model=member.model_id,
                stream=False,
                workspace_context=f"[TEKS]\n{fit_markdown(text, MAX_REPORT_CHARS)}",
                task_hint=question,
                persist=False,
                limits=RunLimits(max_rounds=1, tokens=GATE_TOKENS, deadline=time.time() + MIN_FINISH_SECONDS, max_tools=0),
            ),
            timeout=MIN_FINISH_SECONDS + HARD_TIMEOUT_GRACE_SECONDS,
        )
        if response.status not in {ResponseStatus.SUCCESS, ResponseStatus.FALLBACK_USED}:
            raise RuntimeError(response.error_message or "Model gate gagal menjawab")
        run.total_tokens += response.total_tokens
        run.agents[member.id]["tokens"] = run.agents[member.id].get("tokens", 0) + response.total_tokens
        return parse_decision(response.content)

    async def _holds(self, run: TeamRun, member: TeamMember, condition: dict[str, Any], text: str, tag: str) -> bool:
        result = check_condition(condition, text, datetime.now())
        if result is None:
            # A vague model answer counts as "no", the safe way out of a gate.
            result = bool(await self._ask_model(run, member, condition["value"], text, tag))
        return result

    async def _decide_gate(self, run: TeamRun, member: TeamMember, text: str) -> tuple[str, str]:
        """Which way out of a gate to take, and why. A means the conditions held.

        Conditions that need no model are checked first, so AND and OR can often
        decide without spending a model call.
        """
        config = member.config
        conditions = config.get("conditions") or [config]
        combine = config.get("combine", "and")
        ordered = sorted(enumerate(conditions), key=lambda item: item[1]["mode"] == "ai")
        results: dict[int, bool] = {}
        for index, condition in ordered:
            results[index] = await self._holds(run, member, condition, text, f"gate{index}")
            if combine == "and" and not results[index]:
                break
            if combine == "or" and results[index]:
                break
        met = all(results.values()) if combine == "and" else any(results.values())
        checked = ", ".join(f"{i + 1}:{'ya' if v else 'tidak'}" for i, v in sorted(results.items()))
        return ("A" if met else "B"), f"{combine.upper()} {'terpenuhi' if met else 'tidak terpenuhi'} ({checked})"

    async def _decide_switch(self, run: TeamRun, member: TeamMember, text: str) -> tuple[str, str]:
        """The first rule that holds picks its branch. D catches everything else."""
        for index, rule in enumerate(member.config["rules"]):
            if await self._holds(run, member, rule, text, f"rule{index}"):
                return BRANCHES[index], f"Aturan {BRANCHES[index]} cocok"
        return "D", "Tidak ada aturan yang cocok, memakai jalur D"

    @staticmethod
    def _template_values(run: TeamRun, team: Team, text: str) -> dict[str, str]:
        values = {"input": text, "task": run.task, "team": team.name, **clock_values(datetime.now())}
        for member_id, state in run.agents.items():
            values[f"node.{member_id}"] = str(state.get("content") or "")
        return values

    async def _run_node(self, run: TeamRun, team: Team, member: TeamMember, mode: str = "worker") -> None:
        """Run one node of any kind. A failed output must not cost the user the answer."""
        state = run.agents[member.id]
        if not self._is_active(run, team, member):
            state["status"] = "skipped"
            self._checkpoint(run, "agent_skipped", member.id)
            return
        if member.kind == "agent":
            await self._run_member(run, team, member, mode=mode)
            return
        state.update(status="running", error="", started_at=time.time())
        self._checkpoint(run, "agent_started", member.id)
        text = self._node_input(run, member)
        state["content"] = text
        try:
            # Outputs need a real answer: they must never send the task back as if it were one.
            if member.kind in OUTPUT_KINDS and not text.strip():
                raise OutputError("Tidak ada hasil untuk dikirim, karena agent sebelumnya gagal")
            if member.kind == "delay":
                seconds = int(member.config["seconds"])
                await asyncio.sleep(seconds)
                run.waited_seconds += seconds
            elif member.kind == "gate":
                state["branch"], state["note"] = await self._decide_gate(run, member, text)
            elif member.kind == "switch":
                state["branch"], state["note"] = await self._decide_switch(run, member, text)
            elif member.kind == "merge":
                state["note"] = f"{sum(1 for d in member.depends_on if run.agents[d]['status'] in REPORTABLE)} jalur bergabung"
            elif member.kind == "set":
                state["content"] = render_template(member.config["text"], self._template_values(run, team, text))
            elif member.kind == "http":
                body = render_template(member.config.get("body", ""), self._template_values(run, team, text))
                state["content"] = await http_request(member.config["method"], member.config["url"], body)
                state["note"] = f"{member.config['method']} berhasil"
            elif member.kind == "telegram":
                # A run started from a Telegram message answers in that chat.
                chat_id = str(member.config.get("chat_id") or run.trigger.get("chat_id") or "")
                sent = await send_telegram(text, chat_id)
                state["note"] = f"{sent} pesan terkirim"
            elif member.kind == "database":
                await save_to_database(str(member.config["database"]), str(member.config["table"]), {
                    "run_id": run.id,
                    "team_id": team.id,
                    "team_name": team.name,
                    "node_id": member.id,
                    "task": run.task,
                    "content": text,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                })
                state["note"] = f"Tersimpan di {member.config['database']}, tabel {member.config['table']}"
            state["status"] = "completed"
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            raise
        except Exception as exc:
            logger.warning("[teams] Node %s failed in %s: %s", member.name, run.id, type(exc).__name__)
            state.update(status="failed", error=str(exc) if isinstance(exc, OutputError) else f"{exc}"[:300])
        finally:
            state["completed_at"] = time.time()
            self._checkpoint(run, "agent_finished", member.id)

    async def _review_loop(self, run: TeamRun, team: Team, lead: TeamMember) -> None:
        """Let the reviewer send the draft back until it passes or rounds run out."""
        reviewer = next((m for m in team.members if m.id == team.reviewer_id), None)
        if reviewer is None or run.agents[lead.id]["status"] != "completed":
            return
        for round_number in range(1, MAX_REVIEW_ROUNDS + 2):
            if time.time() + MIN_REVIEW_SECONDS >= self._run_ends(run):
                run.reviews.append({"round": round_number, "verdict": "skipped", "notes": "Waktu tim habis"})
                return
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
        if state["status"] == "completed":
            body = fit_markdown(state["content"], MAX_REPORT_CHARS)
        else:
            body = f"FAILED: {state['error']}"
        return f"## {state['name']} — {state['role']}\n{body}"

    def _member_context(
        self, run: TeamRun, team: Team, member: TeamMember, mode: str, feedback: str
    ) -> str:
        """The task plus only the reports this member should see."""
        context = f"[TEAM TASK]\n{run.task}"
        if mode in {"lead", "revision"}:
            excluded = {team.lead_id, team.reviewer_id}
            agents = {m.id for m in team.members if m.kind == "agent"}
            reports = "\n\n".join(
                self._report(state) for member_id, state in run.agents.items()
                if member_id not in excluded and member_id in agents and state["status"] in REPORTABLE
            )
            if reports:
                context += f"\n\n[SPECIALIST REPORTS]\n{reports[-MAX_CONTEXT_CHARS:]}"
        if mode == "revision":
            draft = run.agents[team.lead_id]["content"]
            context += f"\n\n[YOUR DRAFT]\n{draft[-MAX_DRAFT_CHARS:]}\n\n[REVIEW]\n{feedback[-MAX_CONTEXT_CHARS:]}"
        if mode == "reviewer":
            context += f"\n\n[DRAFT TO REVIEW]\n{run.agents[team.lead_id]['content'][-MAX_DRAFT_CHARS:]}"
        if mode in {"worker", "after"} and member.depends_on:
            reports = "\n\n".join(
                self._report(run.agents[d]) for d in member.depends_on
                if d in run.agents and run.agents[d]["status"] in REPORTABLE
            )
            context += f"\n\n[REPORTS FROM EARLIER STEPS]\n{reports[-MAX_CONTEXT_CHARS:]}"
        return context

    @staticmethod
    def _run_ends(run: TeamRun) -> float:
        return (run.started_at or time.time()) + get_settings().team_run_seconds + run.waited_seconds

    @staticmethod
    def _workers_until(run: TeamRun) -> float:
        start = run.started_at or time.time()
        return start + get_settings().team_run_seconds * WORKER_SHARE_OF_RUN + run.waited_seconds

    def _limits(self, run: TeamRun, mode: str) -> RunLimits:
        settings = get_settings()
        now = time.time()
        rounds = FINISHING_ROUNDS.get(mode, settings.team_member_rounds)
        tokens = settings.team_member_tokens
        if mode == "worker":
            end = self._workers_until(run)
        else:
            # The lead writes from reports, so it needs far less than a worker.
            tokens //= 2
            end = max(self._run_ends(run), now + MIN_FINISH_SECONDS)
        return RunLimits(max_rounds=rounds, tokens=tokens, deadline=min(now + settings.team_member_seconds, end))

    def _track_activity(self, run: TeamRun, member_id: str) -> Callable[[dict[str, Any]], None]:
        """Show what a member is doing right now on its node."""

        def on_event(event: dict[str, Any]) -> None:
            if event.get("type") == "tool" and event.get("phase") == "start":
                run.agents[member_id]["activity"] = {"name": event.get("name"), "detail": event.get("detail")}
                self.events.publish(run.id, {"type": "agent_activity", "run": asdict(run), "member_id": member_id})

        return on_event

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
        limits = self._limits(run, mode)
        try:
            response: AgentResponse = await asyncio.wait_for(
                self.orchestrator.process(
                    user_input=self._member_prompt(member, run.task, mode),
                    session_id=f"{run.id}-{member.id}-{suffix}",
                    override_model=member.model_id,
                    stream=False,
                    workspace_context=self._member_context(run, team, member, mode, feedback),
                    task_hint=run.task,
                    persist=False,
                    limits=limits,
                    on_event=self._track_activity(run, member.id),
                ),
                timeout=max(1.0, limits.deadline - time.time()) + HARD_TIMEOUT_GRACE_SECONDS,
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
            reason = "Model tidak menjawab sebelum batas waktu" if isinstance(exc, TimeoutError) else str(exc)
            logger.warning("[teams] %s failed in %s: %s", member.name, run.id, reason)
            agent.update(status="failed", error=reason)
            if mode == "lead":
                raise
        finally:
            agent.pop("activity", None)
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

    def start_scheduler(self) -> None:
        """Start firing scheduled teams and listening to Telegram. Needs a running event loop."""
        if self._scheduler is None:
            self._scheduler = asyncio.create_task(self._schedule_loop(), name="team-scheduler")
        if self._telegram is None:
            self._telegram = asyncio.create_task(self._telegram_loop(), name="team-telegram")

    async def _schedule_loop(self) -> None:
        while True:
            await asyncio.sleep(SCHEDULE_CHECK_SECONDS)
            try:
                await self.run_due_schedules()
            except Exception:
                logger.exception("[teams] Schedule check failed")

    async def run_due_schedules(self, now: float | None = None) -> list[str]:
        """Start every team whose time has come. Returns the runs started."""
        now = time.time() if now is None else now
        started: list[str] = []
        for team in self.store.list_teams():
            due = next_run_after(team.schedule, team.last_scheduled_at or team.updated_at)
            if due is None or now < due:
                continue
            team.last_scheduled_at = now
            self.store.put_team(team)
            if now - due > SCHEDULE_LATE_SECONDS and team.schedule.get("mode") in {"daily", "cron", "once"}:
                logger.info("[teams] Skipped a missed schedule for %s", team.name)
                continue
            try:
                run = await self.start_run(team.id, str(team.schedule["task"]), {"source": team.schedule["mode"]})
                started.append(run.id)
            except ValueError as exc:
                logger.info("[teams] Schedule for %s did not start: %s", team.name, exc)
        return started

    def _telegram_teams(self) -> list[Team]:
        return [team for team in self.store.list_teams() if team.schedule.get("mode") == "telegram"]

    async def _telegram_loop(self) -> None:
        while True:
            if not self._telegram_teams() or not get_settings().telegram_bot_token:
                await asyncio.sleep(TELEGRAM_IDLE_SECONDS)
                continue
            try:
                await self.poll_telegram()
            except OutputError as exc:
                logger.warning("[teams] Telegram polling: %s", exc)
                await asyncio.sleep(TELEGRAM_IDLE_SECONDS)
            except Exception:
                logger.exception("[teams] Telegram polling failed")
                await asyncio.sleep(TELEGRAM_IDLE_SECONDS)

    async def poll_telegram(self, wait: int = 25) -> list[str]:
        """Start the teams that new Telegram messages are meant for. Returns the runs started.

        Only the chat in TELEGRAM_CHAT_ID can start a flow: anyone can message a bot,
        and a flow can spend money and reach the network. Messages that were waiting
        before the server started are dropped rather than run all at once.
        """
        if self._telegram_offset is None:
            pending = await fetch_telegram_updates(-1, wait=0)
            self._telegram_offset = pending[-1]["update_id"] + 1 if pending else 0
        updates = await fetch_telegram_updates(self._telegram_offset, wait=wait)
        allowed = str(get_settings().telegram_chat_id or "")
        started: list[str] = []
        for update in updates:
            self._telegram_offset = max(self._telegram_offset, int(update["update_id"]) + 1)
            message = update.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id") or "")
            text = str(message.get("text") or "")
            if not allowed or chat_id != allowed:
                if chat_id:
                    logger.info("[teams] Ignored a Telegram message from chat %s, not TELEGRAM_CHAT_ID", chat_id)
                continue
            for team in self._telegram_teams():
                task = telegram_task(team.schedule, text)
                if task is None:
                    continue
                try:
                    run = await self.start_run(team.id, task, {"source": "telegram", "chat_id": chat_id})
                    started.append(run.id)
                except ValueError as exc:
                    logger.info("[teams] Telegram message for %s did not start: %s", team.name, exc)
                    with contextlib.suppress(OutputError):
                        await send_telegram(f"{team.name} masih berjalan. Coba lagi sebentar lagi.", chat_id)
        return started

    async def close(self) -> None:
        for loop_task in (self._scheduler, self._telegram):
            if loop_task:
                loop_task.cancel()
                await asyncio.gather(loop_task, return_exceptions=True)
        self._scheduler = None
        self._telegram = None
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
