"""Tests for POST /api/skills/run."""
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_run_unknown_skill_returns_422(client):
    resp = await client.post("/api/skills/run", json={"skill_id": "does-not-exist"})
    assert resp.status_code == 422
    assert "Unknown skill" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_run_skill_without_claude_cli_returns_unavailable(client):
    with patch("routers.skills._find_claude_bin", return_value=None):
        resp = await client.post("/api/skills/run", json={"skill_id": "handoff"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["job_id"] == ""


@pytest.mark.asyncio
async def test_run_skill_with_claude_cli_returns_started(client):
    def _close_coro(coro):
        coro.close()  # prevent "coroutine was never awaited" warning

    with patch("routers.skills._find_claude_bin", return_value="/usr/local/bin/claude"), \
         patch("asyncio.create_task", side_effect=_close_coro):
        resp = await client.post("/api/skills/run", json={"skill_id": "handoff"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert body["job_id"] != ""
    assert "/handoff" in body["message"]


@pytest.mark.asyncio
async def test_run_skill_strips_leading_slash(client):
    """Skill IDs may be passed with or without the leading slash."""
    def _close_coro(coro):
        coro.close()

    with patch("routers.skills._find_claude_bin", return_value="/usr/local/bin/claude"), \
         patch("asyncio.create_task", side_effect=_close_coro):
        resp = await client.post("/api/skills/run", json={"skill_id": "/review"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


@pytest.mark.asyncio
async def test_run_skill_allowed_set_includes_handoff_review_init(client):
    """All three documented skills must be reachable (not 422)."""
    for skill_id in ("handoff", "review", "init"):
        # Return None for the bin so the handler returns 'unavailable' immediately
        # without spawning a background coroutine (avoids unawaited-coroutine warning).
        with patch("routers.skills._find_claude_bin", return_value=None):
            resp = await client.post("/api/skills/run", json={"skill_id": skill_id})
        assert resp.status_code == 200, f"{skill_id} should not return 422"
        assert resp.json()["status"] == "unavailable"
