"""
Tests for StaticFilesWSGuard (→1203).

Verifies that a WebSocket connection to the StaticFiles mount closes cleanly
instead of crashing with AssertionError, and that HTTP file serving still works.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.staticfiles_ws_guard import StaticFilesWSGuard, SPAStaticFiles


@pytest.fixture
def spa_app(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "asset.js").write_text("console.log('x')")
    mini = FastAPI()
    mini.mount("/", SPAStaticFiles(directory=str(tmp_path), html=True), name="frontend")
    return mini


def test_spa_fallback_serves_index_for_client_route(spa_app):
    """An unknown non-API path (a deep-linked client route) falls back to index.html."""
    with TestClient(spa_app) as client:
        resp = client.get("/specs")
        assert resp.status_code == 200
        assert "<title>app</title>" in resp.text


def test_spa_fallback_serves_real_asset(spa_app):
    """A real static asset is served directly, not replaced by index.html."""
    with TestClient(spa_app) as client:
        resp = client.get("/asset.js")
        assert resp.status_code == 200
        assert "console.log" in resp.text


def test_spa_fallback_excludes_api_paths(spa_app):
    """An unmatched /api/* path must 404, not serve the SPA, so a removed or
    mistyped API endpoint surfaces as an error instead of a 200 HTML page."""
    with TestClient(spa_app) as client:
        resp = client.get("/api/does/not/exist")
        assert resp.status_code == 404


@pytest.fixture
def static_app(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")
    mini = FastAPI()
    mini.mount(
        "/static",
        StaticFilesWSGuard(StaticFiles(directory=str(tmp_path))),
        name="static",
    )
    return mini


def test_websocket_closes_cleanly_no_assertion_error(static_app):
    """WebSocket to the static mount must not raise AssertionError."""
    with TestClient(static_app, raise_server_exceptions=True) as client:
        raised = None
        try:
            with client.websocket_connect("/static/anything"):
                pass
        except Exception as exc:
            raised = exc
        assert not isinstance(raised, AssertionError), (
            f"StaticFiles AssertionError still raised: {raised}"
        )
        # A clean disconnect (WebSocketDisconnect) is acceptable — it means
        # the guard closed the connection gracefully instead of crashing.
        if raised is not None:
            assert isinstance(raised, WebSocketDisconnect), (
                f"Unexpected exception type: {type(raised).__name__}: {raised}"
            )


def test_http_get_static_file_still_works(static_app):
    """HTTP GET to a real static file must still return 200 with correct body."""
    with TestClient(static_app) as client:
        resp = client.get("/static/hello.txt")
        assert resp.status_code == 200
        assert resp.text == "hello world"
