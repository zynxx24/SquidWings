"""HTTP and Server-Sent Events API for collaborative AI teams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.team_orchestrator import team_templates
from agent.team_outputs import OutputError, database_options, send_telegram
from config.settings import get_settings

router = APIRouter(prefix="/api/teams", tags=["AI teams"])


class MemberInput(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    role: str = Field(default="", max_length=120)
    model_id: str = Field(default="", max_length=160)
    instructions: str = Field(default="", max_length=4000)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    kind: str = Field(default="agent", max_length=20)
    branch: str = Field(default="", max_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class TeamInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    members: list[MemberInput] = Field(min_length=2, max_length=12)
    lead_id: str | None = None
    reviewer_id: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    mode: str = Field(default="standard", max_length=20)


class RunInput(BaseModel):
    task: str = Field(min_length=1, max_length=50_000)


def service(request: Request) -> Any:
    manager = getattr(request.app.state, "team_orchestrator", None)
    if manager is None:
        raise HTTPException(503, "Team service is not available")
    return manager


def member_data(member: MemberInput) -> dict[str, Any]:
    return member.model_dump()


@router.get("")
async def list_teams(request: Request) -> dict[str, Any]:
    manager = service(request)
    return {"teams": [asdict(team) for team in manager.store.list_teams()]}


@router.get("/templates")
async def list_templates(request: Request) -> dict[str, Any]:
    manager = service(request)
    models = [m.id for m in manager.orchestrator.registry.get_all() if m.supports_tools]
    return {"templates": team_templates(models)}


@router.get("/integrations")
async def integrations(request: Request) -> dict[str, Any]:
    """What a flow can deliver to. Says what is set up, never the secrets themselves."""
    service(request)
    settings = get_settings()
    return {
        "telegram": {"ready": bool(settings.telegram_bot_token), "default_chat": bool(settings.telegram_chat_id)},
        "databases": database_options(),
    }


class TelegramTest(BaseModel):
    chat_id: str = Field(default="", max_length=70)


@router.post("/telegram/test")
async def test_telegram(payload: TelegramTest, request: Request) -> dict[str, bool]:
    service(request)
    try:
        await send_telegram("Tes dari SquidWings: Telegram sudah tersambung.", payload.chat_id.strip())
    except OutputError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"sent": True}


@router.post("", status_code=201)
async def create_team(payload: TeamInput, request: Request) -> dict[str, Any]:
    try:
        team = service(request).create_team(
            payload.name, payload.description, [member_data(m) for m in payload.members], payload.lead_id,
            payload.reviewer_id, payload.schedule, payload.mode,
        )
        return asdict(team)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{team_id}")
async def update_team(team_id: str, payload: TeamInput, request: Request) -> dict[str, Any]:
    try:
        team = await service(request).update_team(
            team_id, payload.name, payload.description, [member_data(m) for m in payload.members], payload.lead_id,
            payload.reviewer_id, payload.schedule, payload.mode,
        )
        return asdict(team)
    except KeyError as exc:
        raise HTTPException(404, "Team not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/{team_id}")
async def delete_team(team_id: str, request: Request) -> dict[str, bool]:
    if not await service(request).delete_team(team_id):
        raise HTTPException(404, "Team not found")
    return {"deleted": True}


@router.get("/{team_id}/runs")
async def list_runs(team_id: str, request: Request) -> dict[str, Any]:
    manager = service(request)
    if manager.store.get_team(team_id) is None:
        raise HTTPException(404, "Team not found")
    return {"runs": [asdict(run) for run in manager.store.list_runs(team_id)]}


@router.post("/{team_id}/runs", status_code=202)
async def start_run(team_id: str, payload: RunInput, request: Request) -> dict[str, Any]:
    try:
        run = await service(request).start_run(team_id, payload.task)
        return asdict(run)
    except KeyError as exc:
        raise HTTPException(404, "Team not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    run = service(request).store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return asdict(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    try:
        return asdict(await service(request).cancel_run(run_id))
    except KeyError as exc:
        raise HTTPException(404, "Run not found") from exc


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    manager = service(request)
    if manager.store.get_run(run_id) is None:
        raise HTTPException(404, "Run not found")

    async def generate() -> AsyncIterator[str]:
        async for event in manager.stream_events(run_id):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
