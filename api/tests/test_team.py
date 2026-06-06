"""Tests for team mode backend: federated sync, visibility model, search/feed."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


# ---------------------------------------------------------------------------
# test_configure_and_load
# ---------------------------------------------------------------------------

def test_configure_and_load(tmp_path, monkeypatch):
    config_path = tmp_path / "team.json"
    monkeypatch.setattr("services.team_sync.TEAM_CONFIG_PATH", config_path)
    monkeypatch.setattr("services.team_sync.TEAM_CACHE_PATH", tmp_path / "team_cache.json")

    payload = {
        "team_repo": str(tmp_path / "repo"),
        "member_id": "alice@example.com",
        "display_name": "Alice",
        "category_defaults": {"decisions": "shared", "knowledge": "private"},
        "auto_sync": False,
    }
    resp = client.post("/api/team/configure", json=payload)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    assert config_path.exists()
    saved = json.loads(config_path.read_text())
    assert saved["member_id"] == "alice@example.com"
    assert saved["display_name"] == "Alice"

    resp2 = client.get("/api/team")
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["configured"] is True
    assert body["config"]["member_id"] == "alice@example.com"


# ---------------------------------------------------------------------------
# test_sync_outbound_respects_visibility
# ---------------------------------------------------------------------------

def test_sync_outbound_respects_visibility(tmp_path, monkeypatch):
    from models.team_schemas import TeamConfig
    from services import team_sync

    team_repo = tmp_path / "repo"
    team_repo.mkdir(exist_ok=True)

    cfg = TeamConfig(
        team_repo=str(team_repo),
        member_id="alice@example.com",
        display_name="Alice",
        category_defaults={"decisions": "shared", "knowledge": "private"},
        auto_sync=False,
    )

    shared_decision = {"key": "theme", "value": "dark", "reason": "readability", "ts": "2026-01-01T00:00:00Z", "visibility": "shared"}
    private_decision = {"key": "secret", "value": "yes", "reason": "private stuff", "ts": "2026-01-02T00:00:00Z", "visibility": "private"}

    decisions_jsonl = tmp_path / "decisions.jsonl"
    decisions_jsonl.write_text(
        json.dumps(shared_decision) + "\n" + json.dumps(private_decision) + "\n"
    )

    monkeypatch.setattr(team_sync, "_local_decisions", lambda: [shared_decision, private_decision])
    monkeypatch.setattr(team_sync, "_local_knowledge", lambda: [])
    monkeypatch.setattr(team_sync, "_local_needles", lambda: [])

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        result = team_sync.sync_outbound(cfg)

    assert "pushed" in result
    assert result["pushed"] > 0

    out_path = team_repo / "alice@example.com" / "decisions.jsonl"
    assert out_path.exists()
    lines = [l for l in out_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["key"] == "theme"
    assert entry["visibility"] == "shared"


# ---------------------------------------------------------------------------
# test_search_team
# ---------------------------------------------------------------------------

def test_search_team(tmp_path, monkeypatch):
    from services import team_sync

    cache = {
        "bob@example.com": {
            "display_name": "Bob",
            "decisions": [
                {"key": "framework", "value": "fastapi", "reason": "async support", "ts": "2026-01-10T00:00:00Z"},
            ],
            "knowledge": [],
            "needles": [],
        },
        "carol@example.com": {
            "display_name": "Carol",
            "decisions": [
                {"key": "database", "value": "postgres", "reason": "reliable", "ts": "2026-01-05T00:00:00Z"},
            ],
            "knowledge": [],
            "needles": [],
        },
    }

    cache_path = tmp_path / "team_cache.json"
    cache_path.write_text(json.dumps(cache))
    monkeypatch.setattr("services.team_sync.TEAM_CACHE_PATH", cache_path)
    monkeypatch.setattr("services.team_sync.TEAM_CONFIG_PATH", tmp_path / "team.json")

    config_path = tmp_path / "team.json"
    config_path.write_text(json.dumps({
        "team_repo": str(tmp_path / "repo"),
        "member_id": "alice@example.com",
        "display_name": "Alice",
        "auto_sync": False,
    }))

    resp = client.get("/api/team/search?q=fastapi")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    assert any(r["item"]["value"] == "fastapi" for r in body["results"])
    assert all(r["item"]["value"] != "postgres" for r in body["results"])


# ---------------------------------------------------------------------------
# test_decision_visibility_persisted
# ---------------------------------------------------------------------------

def test_decision_visibility_persisted(tmp_path, monkeypatch):
    decisions_jsonl = tmp_path / "decisions.jsonl"

    async def fake_log(key, value, reason="", visibility=None):
        entry = {"key": key, "value": value, "reason": reason, "ts": "2026-01-01T00:00:00Z"}
        if visibility is not None:
            entry["visibility"] = visibility
        decisions_jsonl.write_text(json.dumps(entry) + "\n")
        return entry

    monkeypatch.setattr("routers.decisions.ostk.log_decision", fake_log)
    monkeypatch.setattr("routers.decisions.team_sync.load_config", lambda: None)

    resp = client.post(
        "/api/decisions",
        json={"key": "auth", "value": "jwt", "reason": "stateless", "visibility": "private"},
    )
    assert resp.status_code == 200

    assert decisions_jsonl.exists()
    entry = json.loads(decisions_jsonl.read_text().strip())
    assert entry["visibility"] == "private"
