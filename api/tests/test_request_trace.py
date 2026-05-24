"""Tests for TraceMiddleware (X-Trace-Id propagation)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.request_trace import TraceMiddleware, HEADER
from services.tracing import get_trace_id


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceMiddleware)

    @app.get("/ping")
    def ping():
        return {"trace_id": get_trace_id()}

    return app


def test_middleware_generates_trace_id_when_absent():
    client = TestClient(_make_app(), raise_server_exceptions=True)
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert HEADER in resp.headers
    tid = resp.headers[HEADER]
    assert len(tid) == 36  # UUID4 format
    assert resp.json()["trace_id"] == tid


def test_middleware_reuses_incoming_trace_id():
    client = TestClient(_make_app(), raise_server_exceptions=True)
    supplied = "my-custom-trace-abc"
    resp = client.get("/ping", headers={HEADER: supplied})
    assert resp.status_code == 200
    assert resp.headers[HEADER] == supplied
    assert resp.json()["trace_id"] == supplied


def test_middleware_exposes_trace_id_inside_handler():
    """The handler can read get_trace_id() and it matches the response header."""
    client = TestClient(_make_app(), raise_server_exceptions=True)
    resp = client.get("/ping")
    assert resp.json()["trace_id"] == resp.headers[HEADER]
