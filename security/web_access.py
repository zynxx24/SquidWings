"""Web-edge protection for the GUI. There is no login.

This ASGI middleware protects the HTTP and WebSocket surface before a request can
reach the application:

* a Host check, so a malicious site cannot reach a local server by DNS rebinding,
* same-origin checks, so a site you visit cannot drive the agent from your browser,
* bounded concurrent web users with a useful retry response, and
* a conservative per-client request rate limit.

Public mode (``--tunnel`` or a non-local ``--host``) drops the Host check, because
the public hostname is not known in advance. The origin checks stay on. Anyone who
has the URL can then use the agent, which is why the server warns when it starts.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import cast
from urllib.parse import urlsplit

from security.rate_guard import RateGuard, RateLimitExceeded

Scope = dict[str, object]
Message = dict[str, object]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

USER_COOKIE = "squidwings_user"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_public_mode = False


def set_public_mode(enabled: bool) -> None:
    """Accept requests for any hostname. Used for tunnels and non-local binds."""
    global _public_mode
    _public_mode = enabled


def is_loopback_host(host: str) -> bool:
    return host.strip("[]").lower() in LOOPBACK_HOSTS


class CapacityFullError(RuntimeError):
    """Raised when all web-user slots are occupied."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__("The web service is busy; please wait for a session to become free.")


# Backwards-compatible public name used by existing callers.
CapacityFull = CapacityFullError


@dataclass
class _UserRecord:
    last_seen: float
    websocket_count: int = 0


class UserCapacity:
    """Bounded, idle-expiring set of browser identities."""

    def __init__(self, maximum: int = 10, idle_timeout: int = 900) -> None:
        if maximum < 1:
            raise ValueError("maximum must be positive")
        if idle_timeout < 1:
            raise ValueError("idle_timeout must be positive")
        self.maximum = maximum
        self.idle_timeout = idle_timeout
        self._users: dict[str, _UserRecord] = {}
        self._lock = asyncio.Lock()

    async def touch_http(self, user_id: str) -> None:
        async with self._lock:
            now = time.monotonic()
            self._prune(now)
            if user_id not in self._users and len(self._users) >= self.maximum:
                raise CapacityFull(self._retry_after(now))
            record = self._users.setdefault(user_id, _UserRecord(now))
            record.last_seen = now

    async def acquire_websocket(self, user_id: str) -> None:
        async with self._lock:
            now = time.monotonic()
            self._prune(now)
            if user_id not in self._users and len(self._users) >= self.maximum:
                raise CapacityFull(self._retry_after(now))
            record = self._users.setdefault(user_id, _UserRecord(now))
            record.last_seen = now
            record.websocket_count += 1

    async def release_websocket(self, user_id: str) -> None:
        async with self._lock:
            record = self._users.get(user_id)
            if record is None:
                return
            record.websocket_count = max(0, record.websocket_count - 1)
            record.last_seen = time.monotonic()
            # A disconnected browser no longer occupies a live session slot.
            if record.websocket_count == 0:
                self._users.pop(user_id, None)

    async def snapshot(self) -> dict[str, int]:
        async with self._lock:
            self._prune(time.monotonic())
            return {
                "active_users": len(self._users),
                "max_users": self.maximum,
                "available_slots": max(0, self.maximum - len(self._users)),
            }

    def _prune(self, now: float) -> None:
        expired = [
            user_id
            for user_id, record in self._users.items()
            if record.websocket_count == 0 and now - record.last_seen >= self.idle_timeout
        ]
        for user_id in expired:
            self._users.pop(user_id, None)

    def _retry_after(self, now: float) -> int:
        if not self._users:
            return 1
        oldest = min(record.last_seen for record in self._users.values())
        return max(1, int(self.idle_timeout - (now - oldest)))


class WebSecurityMiddleware:
    """Protect the public web edge and enforce bounded browser capacity."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_users: int = 10,
        idle_timeout: int = 900,
        allowed_origins: tuple[str, ...] = (),
        allowed_hosts: tuple[str, ...] = (),
        requests_per_minute: int = 120,
    ) -> None:
        self.app = app
        self.allowed_hosts = frozenset(x.lower() for x in allowed_hosts if x)
        self.allowed_origins = frozenset(x.rstrip("/") for x in allowed_origins if x)
        self.capacity = UserCapacity(max_users, idle_timeout)
        self.rate_guard = RateGuard(
            requests=__import__("security.rate_guard", fromlist=["Limit"]).Limit(
                capacity=max(10, requests_per_minute), window=60.0
            ),
            max_principals=4096,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = self._headers(scope)
        user_id, new_user_cookie = self._identity(scope, headers)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["web_user_id"] = user_id

        # A foreign Host means DNS rebinding, or a tunnel the server was not told about.
        if not _public_mode and not self._host_allowed(headers):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 4403, "reason": "Host not allowed"})
            else:
                await self._http_error(send, 403, "This host is not allowed. Add it to WEB_ALLOWED_HOSTS.")
            return

        if not self._origin_allowed(scope, headers):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 4403, "reason": "Origin not allowed"})
            else:
                await self._http_error(send, 403, "Origin is not allowed.")
            return

        try:
            self.rate_guard.check(f"ip:{self._client_ip(scope)}")
        except RateLimitExceeded as exc:
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1013, "reason": "Too many requests; please retry later"})
            else:
                await self._http_error(send, 429, "Too many requests; please retry later.", retry_after=exc.retry_after)
            return

        try:
            if kind == "websocket" and scope.get("path") == "/ws/chat":
                await self.capacity.acquire_websocket(user_id)
            elif not new_user_cookie:
                # Only returning browsers hold a slot. A client that sends no
                # cookie would otherwise fill the table with one identity per call.
                await self.capacity.touch_http(user_id)
        except CapacityFull as exc:
            if kind == "websocket":
                await send({
                    "type": "websocket.close",
                    "code": 1013,
                    "reason": f"All {self.capacity.maximum} web sessions are busy. Please wait {exc.retry_after}s.",
                })
            else:
                await self._http_error(
                    send,
                    503,
                    f"All {self.capacity.maximum} web sessions are busy. Please wait for a session to become free.",
                    retry_after=exc.retry_after,
                )
            return

        async def send_wrapper(message: Message) -> None:
            message_type = message.get("type")
            if message_type in {"http.response.start", "websocket.accept"}:
                raw_headers = list(
                    cast("list[tuple[bytes, bytes]]", message.get("headers") or [])
                )
                if new_user_cookie:
                    raw_headers.append((b"set-cookie", self._cookie(USER_COOKIE, user_id, scope)))
                if message_type == "http.response.start":
                    raw_headers.append((b"cache-control", b"no-store"))
                await send({**message, "headers": raw_headers})
            else:
                await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if kind == "websocket" and scope.get("path") == "/ws/chat":
                await self.capacity.release_websocket(user_id)

    def _host_allowed(self, headers: Mapping[str, str]) -> bool:
        host = urlsplit(f"//{headers.get('host', '')}").hostname or ""
        return is_loopback_host(host) or host.lower() in self.allowed_hosts

    @staticmethod
    def _headers(scope: Scope) -> dict[str, str]:
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in cast(
                "list[tuple[bytes, bytes]]", scope.get("headers") or []
            )
        }

    @staticmethod
    def _client_ip(scope: Scope) -> str:
        client = scope.get("client")
        return str(client[0]) if isinstance(client, tuple) and client else "unknown"

    def _identity(self, scope: Scope, headers: Mapping[str, str]) -> tuple[str, bool]:
        cookies = self._cookies(headers.get("cookie", ""))
        identity = cookies.get(USER_COOKIE)
        if identity and 20 <= len(identity) <= 128:
            return identity, False
        return secrets.token_urlsafe(24), True

    @staticmethod
    def _cookies(value: str) -> dict[str, str]:
        jar = SimpleCookie()
        try:
            jar.load(value)
        except (ValueError, UnicodeError):
            return {}
        return {key: morsel.value for key, morsel in jar.items()}

    def _origin_allowed(self, scope: Scope, headers: Mapping[str, str]) -> bool:
        method = str(scope.get("method", "GET")).upper()
        origin = headers.get("origin", "").rstrip("/")
        if not origin or method in {"GET", "HEAD", "OPTIONS"} and scope.get("type") == "http":
            return True
        if origin in self.allowed_origins:
            return True
        parsed = urlsplit(origin)
        request_host = headers.get("host", "").split(",", 1)[0].strip()
        forwarded_host = headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
        return bool(parsed.netloc and parsed.netloc in {request_host, forwarded_host})

    @staticmethod
    def _cookie(name: str, value: str, scope: Scope) -> bytes:
        secure = str(scope.get("scheme", "http")) == "https"
        suffix = "; Secure" if secure else ""
        return f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000{suffix}".encode("latin-1")

    @staticmethod
    async def _http_error(send: Send, status: int, detail: str, retry_after: float | None = None) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        raw = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]
        if retry_after is not None:
            raw.append((b"retry-after", str(max(1, int(retry_after))).encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": raw})
        await send({"type": "http.response.body", "body": body})


def get_web_user_id(scope_or_request: object) -> str:
    """Return the identity assigned by :class:`WebSecurityMiddleware`."""
    state = getattr(scope_or_request, "state", None)
    value = getattr(state, "web_user_id", "") if state is not None else ""
    return str(value or "")
