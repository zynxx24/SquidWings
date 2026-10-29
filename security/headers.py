"""
security.headers — HTTP security headers + CSP for the web UI.

The web server serves a local-first chat UI. Even offline it must defend
against:
    * XSS via model output rendered into the DOM
    * Clickjacking of the control surface
    * MIME sniffing of uploaded/generated content
    * Referrer leakage to third-party CDNs
    * Cross-origin WebSocket hijacking

`SecurityHeadersMiddleware` is a pure ASGI middleware: no Starlette
dependency, no per-request object allocation beyond one dict copy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

# Starlette passes its own ``Scope`` TypedDict, which is NOT assignable to
# ``dict[str, Any]``. Typing the scope as ``Any`` keeps this middleware
# structurally compatible with ``ASGIApp`` so ``app.add_middleware()``
# type-checks, while runtime behaviour is unchanged.
Scope = Any
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Default CSP: local-first. `unsafe-inline` for styles is required by the
# existing stylesheet injection; scripts are same-origin only.
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "media-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

DEFAULT_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",  # modern browsers: rely on CSP
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": DEFAULT_CSP,
}


class SecurityHeadersMiddleware:
    """Inject hardening headers into every HTTP response.

    Args:
        app: Wrapped ASGI application.
        headers: Extra/override headers.
        hsts: Add Strict-Transport-Security (only enable behind TLS).
        hsts_max_age: HSTS max-age in seconds.
        csp: Override the Content-Security-Policy value.
    """

    def __init__(
        self,
        app: ASGIApp,
        headers: Mapping[str, str] | None = None,
        hsts: bool = False,
        hsts_max_age: int = 31_536_000,
        csp: str | None = None,
    ) -> None:
        self.app = app
        merged = dict(DEFAULT_HEADERS)
        if csp is not None:
            merged["Content-Security-Policy"] = csp
        if headers:
            merged.update(headers)
        if hsts:
            merged["Strict-Transport-Security"] = (
                f"max-age={hsts_max_age}; includeSubDomains"
            )
        self._headers: tuple[tuple[bytes, bytes], ...] = tuple(
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in merged.items()
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers") or [])
                existing = {k.lower() for k, _ in raw}
                for key, value in self._headers:
                    if key not in existing:
                        raw.append((key, value))
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def apply_headers(response: Any, headers: Mapping[str, str] | None = None) -> Any:
    """Apply default headers to a single response object (Starlette helper)."""
    for key, value in (headers or DEFAULT_HEADERS).items():
        response.headers.setdefault(key, value)
    return response


def build_csp(
    script_src: Iterable[str] = ("'self'",),
    style_src: Iterable[str] = ("'self'", "'unsafe-inline'"),
    connect_src: Iterable[str] = ("'self'", "ws:", "wss:"),
    report_uri: str | None = None,
) -> str:
    """Compose a CSP string from directive fragments."""
    parts = [
        "default-src 'self'",
        f"script-src {' '.join(script_src)}",
        f"style-src {' '.join(style_src)}",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        f"connect-src {' '.join(connect_src)}",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    if report_uri:
        parts.append(f"report-uri {report_uri}")
    return "; ".join(parts)
