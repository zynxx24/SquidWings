"""Graphify local memory client.

Sends structured events to a local Graphify endpoint and queries them back.
The endpoint is expected to accept ``POST /ingest`` and ``POST /query`` with
JSON bodies. If the endpoint is unreachable, calls fail gracefully.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)


class GraphifyStore:
    def __init__(self, endpoint: str, timeout: float = 5.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.endpoint}/{path.lstrip('/')}"

    async def ingest(
        self,
        kind: str,
        payload: dict[str, Any],
        workspace: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.endpoint:
            return {"ok": False, "error": "graphify endpoint not configured"}
        body = {
            "kind": kind,
            "workspace": workspace,
            "tags": tags or [],
            "payload": payload,
            "timestamp": time.time(),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self._url("ingest"), json=body)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"graphify ingest HTTP {resp.status_code}",
                    "body": resp.text[:500],
                }
            try:
                return {"ok": True, "response": resp.json()}
            except ValueError:
                return {"ok": True, "response": resp.text[:500]}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"graphify ingest failed: {exc}"}

    async def query(
        self,
        kind: str | None = None,
        workspace: str | None = None,
        limit: int = 20,
        text: str | None = None,
    ) -> dict[str, Any]:
        if not self.endpoint:
            return {"ok": False, "error": "graphify endpoint not configured"}
        body = {
            "kind": kind,
            "workspace": workspace,
            "limit": max(1, min(200, limit)),
            "text": text,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self._url("query"), json=body)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"graphify query HTTP {resp.status_code}",
                    "body": resp.text[:500],
                }
            try:
                return {"ok": True, "response": resp.json()}
            except ValueError:
                return {"ok": True, "response": resp.text[:500]}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"graphify query failed: {exc}"}


__all__ = ["GraphifyStore"]
