"""Tests for api/routers/trace.py (client-side trace ingestion).

The router has one endpoint, POST /api/trace/client, that records a
browser-emitted trace event into the same audit stream the backend
uses. The handler must never raise and must always return 204.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_post_client_trace_returns_204(client):
    """Happy path: a well-formed body is recorded and the response is 204."""
    payload = {
        "event": "ui.button.click",
        "trace_id": "abc123",
        "page": "dashboard",
    }
    with patch("routers.trace.trace_event") as mock_trace:
        resp = await client.post("/api/trace/client", json=payload)
    assert resp.status_code == 204
    assert resp.content == b""
    mock_trace.assert_called_once()
    args, kwargs = mock_trace.call_args
    # First positional is the event name.
    assert args[0] == "ui.button.click"
    assert kwargs.get("source") == "client"
    assert kwargs.get("trace_id") == "abc123"
    # Extra fields are forwarded as kwargs, not nested.
    assert kwargs.get("page") == "dashboard"


@pytest.mark.asyncio
async def test_post_client_trace_missing_event_defaults(client):
    """An empty body still returns 204; the event defaults to client.unknown."""
    with patch("routers.trace.trace_event") as mock_trace:
        resp = await client.post("/api/trace/client", json={})
    assert resp.status_code == 204
    mock_trace.assert_called_once()
    args, _ = mock_trace.call_args
    assert args[0] == "client.unknown"


@pytest.mark.asyncio
async def test_post_client_trace_missing_trace_id_defaults(client):
    """Missing trace_id defaults to empty string, not None."""
    with patch("routers.trace.trace_event") as mock_trace:
        resp = await client.post("/api/trace/client", json={"event": "x"})
    assert resp.status_code == 204
    _, kwargs = mock_trace.call_args
    assert kwargs.get("trace_id") == ""


@pytest.mark.asyncio
async def test_post_client_trace_swallows_internal_errors(client):
    """If trace_event itself raises, the handler still returns 204."""
    with patch("routers.trace.trace_event", side_effect=RuntimeError("boom")):
        resp = await client.post(
            "/api/trace/client",
            json={"event": "ui.error", "trace_id": "t1"},
        )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_post_client_trace_extra_fields_forwarded(client):
    """Extra body fields flow through as kwargs so downstream filters can use them."""
    payload = {
        "event": "nav.click",
        "trace_id": "t2",
        "from": "/tasks",
        "to": "/agents",
        "duration_ms": 12,
    }
    with patch("routers.trace.trace_event") as mock_trace:
        resp = await client.post("/api/trace/client", json=payload)
    assert resp.status_code == 204
    _, kwargs = mock_trace.call_args
    assert kwargs.get("from") == "/tasks"
    assert kwargs.get("to") == "/agents"
    assert kwargs.get("duration_ms") == 12
    # And the reserved keys are NOT passed twice.
    assert "event" not in kwargs
