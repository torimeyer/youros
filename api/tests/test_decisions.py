"""Smoke tests for the /decisions router.

These keep test_retro_prevention happy and exercise the thin wrapper
around ostk.list_decisions / ostk.log_decision so a broken contract
shows up in CI rather than at demo time.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_list_decisions_returns_shape(monkeypatch):
    sample = [{"key": "theme", "value": "dark", "reason": "readability", "ts": "2026-04-15T00:00:00Z"}]

    def fake_list():
        return sample

    monkeypatch.setattr("routers.decisions.ostk.list_decisions", fake_list)

    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["decisions"] == sample


def test_list_decisions_handles_empty(monkeypatch):
    monkeypatch.setattr("routers.decisions.ostk.list_decisions", lambda: [])
    resp = client.get("/api/decisions")
    assert resp.status_code == 200
    assert resp.json() == {"decisions": [], "count": 0}


def test_create_decision_calls_ostk(monkeypatch):
    called = {}

    async def fake_log(key, value, reason):
        called["args"] = (key, value, reason)
        return {"logged": True}

    monkeypatch.setattr("routers.decisions.ostk.log_decision", fake_log)

    resp = client.post(
        "/api/decisions",
        json={"key": "theme", "value": "dark", "reason": "readability"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == {"logged": True}
    assert called["args"] == ("theme", "dark", "readability")


def test_create_decision_reason_defaults_to_empty(monkeypatch):
    captured = {}

    async def fake_log(key, value, reason):
        captured["reason"] = reason
        return {}

    monkeypatch.setattr("routers.decisions.ostk.log_decision", fake_log)

    resp = client.post("/api/decisions", json={"key": "theme", "value": "light"})
    assert resp.status_code == 200
    assert captured["reason"] == ""
