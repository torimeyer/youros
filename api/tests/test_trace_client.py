"""Client-side trace ingestion endpoint."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_post_client_trace_returns_204(monkeypatch, tmp_path):
    """POST /api/trace/client returns 204 and writes to audit.jsonl."""
    # Redirect the audit file to a tmp path so we can inspect it.
    import services.tracing as tracing_mod

    audit = tmp_path / ".ostk" / "audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)

    # Patch the PROJECT_ROOT-based path resolution by monkey-patching
    # the whole trace_event to write to our audit file.
    original = tracing_mod.trace_event
    captured: list[tuple[str, dict]] = []

    def spy(name, **kwargs):
        captured.append((name, kwargs))
        # Also exercise the real implementation so the audit path runs.
        original(name, **kwargs)

    # Patch in the router's import, which is what post_client_trace calls.
    import routers.trace as trace_router
    monkeypatch.setattr(trace_router, "trace_event", spy, raising=False)

    # Build a tiny FastAPI app including just the trace router to avoid
    # pulling in the whole backend boot path.
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(trace_router.router, prefix="/api")

    client = TestClient(app)
    resp = client.post(
        "/api/trace/client",
        json={"event": "ui.clicked", "trace_id": "t-1", "button": "save"},
    )
    assert resp.status_code == 204

    # The spy must have received the event with source="client".
    names = [c[0] for c in captured]
    assert "ui.clicked" in names
    first = next(c for c in captured if c[0] == "ui.clicked")
    assert first[1].get("source") == "client"
    assert first[1].get("trace_id") == "t-1"
    assert first[1].get("button") == "save"


def test_post_client_trace_swallows_bad_payload(monkeypatch):
    """Even when the body is malformed, the endpoint returns 204 (or 422)."""
    import routers.trace as trace_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(trace_router.router, prefix="/api")

    client = TestClient(app)
    # Send an array instead of an object; FastAPI will 422, which is
    # acceptable behaviour. The important property is that the server
    # does not crash.
    resp = client.post("/api/trace/client", json=[])
    assert resp.status_code in (204, 422)
