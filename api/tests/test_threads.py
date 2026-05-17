"""Smoke tests for api/routers/threads.py.

Threads (groups) were removed. All endpoints return 410 GONE with a migration
message pointing users toward Labels with a project: prefix.
"""
import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_list_threads_returns_gone():
    resp = client.get("/api/threads")
    assert resp.status_code == 410


def test_create_thread_returns_gone():
    resp = client.post("/api/threads")
    assert resp.status_code == 410


def test_get_thread_returns_gone():
    resp = client.get("/api/threads/abc123")
    assert resp.status_code == 410


def test_delete_thread_returns_gone():
    resp = client.delete("/api/threads/abc123")
    assert resp.status_code == 410
