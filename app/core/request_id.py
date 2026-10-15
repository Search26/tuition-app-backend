"""Request id: taken from X-Request-ID (set by Caddy, or nginx in dev) or made here, put on every
log line of the request, and returned in the response. Also logs one access line per request."""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

logger = logging.getLogger("app.access")

HEADER = "X-Request-ID"
_VALID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# Health is polled every few seconds by Docker, and nginx calls the media check once per video
# file (and logs that request itself): keep both out of the INFO log.
_QUIET_PATHS = {"/health", "/internal/media-auth"}


def pick_request_id(incoming: str | None) -> str:
    if incoming and _VALID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


def request_id_of(scope: Scope) -> str | None:
    state = scope.get("state")
    return state.get("request_id") if isinstance(state, dict) else None


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(HEADER.lower().encode())
        request_id = pick_request_id(incoming.decode("latin-1") if incoming else None)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((HEADER.lower().encode(), request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            client = scope.get("client")
            logger.log(
                logging.DEBUG if scope["path"] in _QUIET_PATHS else logging.INFO,
                "%s %s %s",
                scope["method"],
                scope["path"],
                status,
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client_ip": client[0] if client else None,
                },
            )
            request_id_var.reset(token)
