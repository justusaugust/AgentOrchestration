"""API middleware components."""

import time
import logging
import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Optional
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)
CORRELATION_ID_HEADER = "x-correlation-id"
WORKSPACE_ID_HEADER = "x-workspace-id"
WORKSPACE_ROLE_HEADER = "x-workspace-role"
FALLBACK_ROLE_HEADER = "x-role"
CORRELATION_SCOPE_HEADER = "X-Correlation-Scope"
REQUEST_CONTEXT_VALIDATION_HEADER = "X-Request-Context-Validation"


@dataclass(frozen=True)
class RequestContext:
    workspace_id: str
    role: str
    correlation_id: str
    scope_digest: str


_request_context: ContextVar[Optional[RequestContext]] = ContextVar(
    "request_context",
    default=None,
)


def get_request_context() -> Optional[RequestContext]:
    return _request_context.get()


def _header_value(scope: Scope, name: str) -> Optional[str]:
    needle = name.lower().encode("latin1")
    for header_name, value in scope.get("headers", []):
        if header_name.lower() == needle:
            decoded = value.decode("latin1").strip()
            return decoded or None
    return None


def _scope_digest(
    workspace_id: str,
    role: str,
    correlation_id: str,
) -> str:
    scoped = "\0".join((workspace_id, role, correlation_id))
    return hashlib.sha256(scoped.encode("utf-8")).hexdigest()[:24]


def _context_from_scope(scope: Scope) -> Optional[RequestContext]:
    correlation_id = _header_value(scope, CORRELATION_ID_HEADER)
    if correlation_id is None:
        return None

    workspace_id = _header_value(scope, WORKSPACE_ID_HEADER)
    role = (
        _header_value(scope, WORKSPACE_ROLE_HEADER)
        or _header_value(scope, FALLBACK_ROLE_HEADER)
    )
    if workspace_id is None or role is None:
        return None

    return RequestContext(
        workspace_id=workspace_id,
        role=role,
        correlation_id=correlation_id,
        scope_digest=_scope_digest(workspace_id, role, correlation_id),
    )


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = _request_context.set(None)
        context = _context_from_scope(scope)

        try:
            if _header_value(scope, CORRELATION_ID_HEADER) and context is None:
                await self._reject(
                    scope,
                    receive,
                    send,
                    "Correlation ID requires workspace and role scope",
                )
                return

            if context is not None:
                supplied_scope = _header_value(scope, CORRELATION_SCOPE_HEADER)
                if (
                    supplied_scope is not None
                    and supplied_scope != context.scope_digest
                ):
                    logger.warning("Rejected cross-tenant correlation scope")
                    await self._reject(
                        scope,
                        receive,
                        send,
                        "Correlation scope does not match caller",
                    )
                    return

                _request_context.set(context)

            async def send_with_context_headers(message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    if context is not None:
                        headers[CORRELATION_SCOPE_HEADER] = (
                            context.scope_digest
                        )
                    headers[REQUEST_CONTEXT_VALIDATION_HEADER] = "accepted"
                await send(message)

            await self.app(scope, receive, send_with_context_headers)
        finally:
            _request_context.reset(token)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        message: str,
    ) -> None:
        response = PlainTextResponse(
            message,
            status_code=400,
            headers={REQUEST_CONTEXT_VALIDATION_HEADER: "rejected"},
        )
        await response(scope, receive, send)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        if (
            request.url.path.startswith("/api/v2")
            and request.url.path != "/api/v2/auth/token"
        ):
            token = request.headers.get("Authorization", "")
            if not token.startswith("Bearer "):
                return Response(status_code=401, content="Unauthorized")
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._requests = {}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        if client_ip not in self._requests:
            self._requests[client_ip] = []

        self._requests[client_ip] = [
            t for t in self._requests[client_ip]
            if now - t < self.window
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            return Response(status_code=429, content="Too many requests")

        self._requests[client_ip].append(now)
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        logger.info(
            "%s %s %s %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            duration,
        )
        return response

# 2019-03-01T18:35:19 update

# 2019-04-03T13:22:05 update

# 2019-04-30T17:18:49 update

# 2019-08-20T09:29:03 update

# 2019-08-30T15:52:06 update

# 2019-11-23T16:58:42 update

# 2020-02-18T10:04:07 update

# 2020-04-21T17:35:30 update

# 2020-05-22T11:10:34 update

# 2020-07-02T12:31:26 update

# 2020-07-05T13:52:59 update

# 2020-08-21T20:36:45 update

# 2021-01-19T09:17:15 update

# 2021-01-29T11:34:24 update

# 2021-02-04T15:21:21 update

# 2021-04-19T19:23:15 update

# 2021-05-20T16:50:15 update

# 2021-06-22T19:23:44 update

# 2021-09-09T13:44:55 update

# 2021-09-16T09:30:20 update

# 2021-10-14T20:42:33 update

# 2021-12-28T16:39:14 update

# 2022-01-26T19:07:27 update

# 2022-01-28T08:03:41 update

# 2022-03-23T12:17:02 update

# 2022-04-06T12:12:27 update

# 2022-04-21T14:53:01 update

# 2022-06-30T08:37:32 update

# 2022-07-06T10:44:45 update

# 2022-11-02T11:12:47 update

# 2022-11-15T20:54:21 update

# 2022-11-23T14:13:34 update

# 2023-01-26T10:03:44 update

# 2023-02-09T17:08:10 update

# 2023-02-16T10:04:00 update

# 2023-03-14T11:52:03 update

# 2023-04-10T12:42:07 update

# 2023-04-26T10:43:39 update

# 2023-06-27T08:18:07 update

# 2023-08-30T15:30:40 update

# 2023-08-30T14:10:05 update

# 2023-10-09T18:32:46 update

# 2023-11-21T20:35:55 update

# 2024-03-07T19:17:39 update

# 2024-04-01T18:06:19 update

# 2024-07-18T15:37:34 update

# 2024-07-25T09:21:53 update

# 2024-08-12T14:24:22 update

# 2024-11-18T08:50:54 update

# 2025-04-08T12:43:05 update

# 2025-06-03T08:10:47 update

# 2025-06-12T08:37:52 update

# 2025-06-17T08:36:56 update

# 2025-07-02T18:09:42 update

# 2025-07-22T12:39:21 update

# 2025-10-13T12:13:46 update

# 2025-12-05T09:44:22 update

# 2025-12-22T18:34:47 update

# 2026-01-26T15:36:23 update

# 2026-02-13T12:36:40 update

# 2026-02-26T11:07:15 update

# 2026-03-19T11:00:17 update

# 2026-03-27T12:58:53 update

# 2026-05-12T17:19:36 update
