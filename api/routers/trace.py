"""Frontend trace event ingestion.

The browser posts client-side trace events here so they join the same
audit stream as backend events. Event names and field shapes are
defined by the frontend. We trust them minimally and hand them to
trace_event() which is guaranteed to never raise.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request, Response

from services.tracing import trace_event

router = APIRouter()


@router.post("/trace/client", status_code=204)
async def post_client_trace(body: Dict[str, Any], request: Request) -> Response:
    """Record a trace event emitted by the browser.

    Expected body: {"event": str, "trace_id": str, ...extra fields}.
    Always returns 204, even on malformed input. Never raises.
    """
    try:
        event = str(body.get("event") or "client.unknown")
        trace_id = str(body.get("trace_id") or "")
        extra = {k: v for k, v in body.items() if k not in ("event", "trace_id")}
        trace_event(event, source="client", trace_id=trace_id, **extra)
    except Exception:
        # trace_event already swallows errors, but guard the whole
        # handler so a bad payload still returns 204.
        pass
    return Response(status_code=204)
