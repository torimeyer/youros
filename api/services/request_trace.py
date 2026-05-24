"""TraceMiddleware: generates or propagates X-Trace-Id per HTTP request.

Binds the trace ID into the `_trace_id_var` ContextVar so that
`trace_event(...)` calls made during the request automatically include it
without any argument threading.

The trace ID is also set on the response as the `X-Trace-Id` header so
callers can correlate logs.
"""
from __future__ import annotations

import uuid
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from services.tracing import _trace_id_var

HEADER = "X-Trace-Id"


class TraceMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that assigns a trace ID to every request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Reuse caller-supplied trace ID (e.g. from a parent service) or mint a new one.
        incoming = request.headers.get(HEADER, "").strip()
        trace_id = incoming if incoming else str(uuid.uuid4())

        token = _trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            _trace_id_var.reset(token)

        response.headers[HEADER] = trace_id
        return response
