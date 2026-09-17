"""ASGI authentication for the Lilith web server.

The middleware deliberately handles both HTTP and WebSocket scopes. Starlette's
``BaseHTTPMiddleware`` never sees a WebSocket handshake.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from collections.abc import Iterable
from urllib.parse import urlsplit

EXEMPT_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}


def _headers(scope: dict) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _is_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    host = value.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_client(scope: dict) -> bool:
    client = scope.get("client")
    return bool(client and _is_loopback_host(str(client[0])))


def _trusted_websocket_origin(scope: dict, allowed_origins: set[str]) -> bool:
    origin = _headers(scope).get("origin")
    if not origin:
        return True
    normalized = origin.rstrip("/").lower()
    if normalized in allowed_origins:
        return True
    try:
        return _is_loopback_host(urlsplit(origin).hostname)
    except ValueError:
        return False


class TokenAuthMiddleware:
    """Fail-closed bearer authentication for HTTP and WebSocket requests."""

    def __init__(
        self,
        app,
        token: str | None = None,
        allowed_origins: Iterable[str] | None = None,
    ) -> None:
        self.app = app
        self.token = token or os.environ.get("LILITH_AUTH_TOKEN")
        self.allowed_origins = {
            value.rstrip("/").lower() for value in (allowed_origins or [])
        }

    def _authorized(self, scope: dict) -> bool:
        if not self.token:
            return _is_loopback_client(scope)
        headers = _headers(scope)
        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[7:].strip()
            if secrets.compare_digest(provided, self.token):
                return True
        if scope.get("type") == "websocket":
            protocols = [
                item.strip()
                for item in headers.get("sec-websocket-protocol", "").split(",")
                if item.strip()
            ]
            if len(protocols) >= 2 and protocols[0] == "lilith-auth":
                return secrets.compare_digest(protocols[1], self.token)
        return False

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path = (scope.get("path") or "/").rstrip("/") or "/"
        if scope_type == "http" and (
            path in EXEMPT_PATHS or path.startswith("/assets") or path == "/favicon.ico"
        ):
            await self.app(scope, receive, send)
            return

        if not self._authorized(scope):
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 4401, "reason": "Unauthorized"})
            else:
                body = b'{"error":"Unauthorized","message":"Missing or invalid bearer token."}'
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
            return

        if scope_type == "websocket" and not _trusted_websocket_origin(
            scope, self.allowed_origins
        ):
            await send({"type": "websocket.close", "code": 4403, "reason": "Origin denied"})
            return

        await self.app(scope, receive, send)


def generate_token() -> str:
    return f"lilith-{secrets.token_urlsafe(24)}"


def mask_token(token: str) -> str:
    """Return a non-secret status marker; never echo token fragments."""
    return "configured" if token else "disabled"
