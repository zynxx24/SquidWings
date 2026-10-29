"""HTTP and Server-Sent Events API for collaborative AI teams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/teams", tags=["AI teams"])


class MemberInput(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    role: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=160)
    instructions: str = Field(default="", max_length=4000)


class TeamInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    members: list[MemberInput] = Field(min_length=2, max_length=6)
    lead_id: str | None = None


class RunInput(BaseModel):
    task: str = Field(min_length=1, max_length=50_000)


def service(request: Request) -> Any:
    return request.app.state.team_orchestrator


def member_data(member: MemberInput) -> dict[str, Any]:
    return member.model_dump()


@router.get("")
async def list_teams(request: Request) -> dict[str, Any]:
    manager = service(request)
    return {"teams": [asdict(team) for team in manager.store.list_teams()]}


@router.post("", status_code=201)
async def create_team(payload: TeamInput, request: Request) -> dict[str, Any]:
    try:
        team = service(request).create_team(
            payload.name, payload.description, [member_data(m) for m in payload.members], payload.lead_id
        )
        return asdict(team)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{team_id}")
async def update_team(team_id: str, payload: TeamInput, request: Request) -> dict[str, Any]:
    try:
        team = await service(request).update_team(
            team_id, payload.name, payload.description, [member_data(m) for m in payload.members], payload.lead_id
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
