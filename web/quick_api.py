"""Menu cepat for the web chat: the action list, the auto menu for a message, the instruction an agent action
sends, and a video link's qualities. The direct download itself runs as a chat turn over the chat WebSocket
(``{"type": "quick"}`` in ``web/server.py``), so it streams and stays in the chat's history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from brain.utils import quick_actions as qa

router = APIRouter(prefix="/api/quick", tags=["Menu cepat"])


class SuggestInput(BaseModel):
    text: str = Field(default="", max_length=20_000)
    files: list[str] = Field(default_factory=list, max_length=20)


class PromptInput(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    inputs: dict[str, str] = Field(default_factory=dict)


class InfoInput(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


@router.get("/actions")
async def actions() -> dict[str, Any]:
    return {"actions": qa.catalog()}


@router.post("/suggest")
async def suggest(payload: SuggestInput) -> dict[str, Any]:
    return {"suggestions": [vars(s) for s in qa.suggest(payload.text, payload.files)]}


@router.post("/prompt")
async def prompt(payload: PromptInput) -> dict[str, Any]:
    if payload.action not in qa.BY_ID:
        raise HTTPException(404, "Aksi tidak ada.")
    inputs = {k: v[:2000] for k, v in payload.inputs.items() if k in {"file", "url", "note", "choice", "folder"}}
    try:
        return {"message": qa.prompt(payload.action, inputs), "label": qa.label(payload.action, inputs)}
    except qa.QuickActionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/video-info")
async def video_info(payload: InfoInput) -> dict[str, Any]:
    try:
        info = await qa.video_info(payload.url.strip())
    except qa.QuickActionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"title": info.get("title") or "", "channel": info.get("channel") or "", "duration_s": info.get("duration_s"),
            "site": info.get("site") or "", "choices": [vars(c) for c in qa.video_choices(info)]}
