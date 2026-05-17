"""
Tests for POST /api/skills/run  (FR-009 — →1394)

Verifies:
  - 200 + status="started" for known skill_ids
  - 422 for unknown skill_ids
  - status="unavailable" when claude CLI is not installed
  - The background subprocess is spawned with the right command
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# sys.path is set up by conftest.py (api/ on path) so `from main import app`
# works. We import app lazily here to avoid the real lifespan running.
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client():
    from main import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(returncode: int = 0, stdout: bytes = b"done", stderr: bytes = b"") -> MagicMock:
    """Return a mock asyncio subprocess that finishes instantly."""
    proc = MagicMock()
    proc.returncode = returncode

    async def _communicate():
        return stdout, stderr

    proc.communicate = _communicate
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_known_skill_returns_started(app_client):
    """POST /api/skills/run with skill_id=handoff returns 200 + started."""
    with (
        patch("routers.skills._find_claude_bin", return_value="/usr/local/bin/claude"),
        patch("routers.skills._invoke_skill", new_callable=AsyncMock) as mock_invoke,
        patch("asyncio.create_task"),
    ):
        async with app_client as c:
            resp = await c.post("/api/skills/run", json={"skill_id": "handoff", "args": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "started"
    assert "job_id" in body


@pytest.mark.anyio
async def test_run_unknown_skill_returns_422(app_client):
    """POST /api/skills/run with an unknown skill_id returns 422."""
    async with app_client as c:
        resp = await c.post("/api/skills/run", json={"skill_id": "nonexistent", "args": {}})
    assert resp.status_code == 422
    assert "nonexistent" in resp.json()["detail"]


@pytest.mark.anyio
async def test_run_skill_unavailable_when_claude_missing(app_client):
    """Returns status=unavailable when the claude CLI is not installed."""
    with patch("routers.skills._find_claude_bin", return_value=None):
        async with app_client as c:
            resp = await c.post("/api/skills/run", json={"skill_id": "handoff", "args": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unavailable"
    assert "claude" in body["message"].lower()


@pytest.mark.anyio
async def test_invoke_skill_calls_subprocess_with_slash_command(tmp_path):
    """_invoke_skill spawns claude --print /handoff."""
    from routers.skills import _invoke_skill

    captured_cmd: list | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        return _make_proc()

    with (
        patch("routers.skills._find_claude_bin", return_value="/usr/local/bin/claude"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        await _invoke_skill("handoff", "/handoff", "abc123")

    assert captured_cmd is not None
    assert "/handoff" in captured_cmd


@pytest.mark.anyio
async def test_run_skill_strips_api_key_from_env(tmp_path):
    """_invoke_skill must not forward ANTHROPIC_API_KEY to subprocess."""
    import os
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-key"

    captured_env: dict | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get("env", {})
        return _make_proc()

    try:
        from routers.skills import _invoke_skill
        with (
            patch("routers.skills._find_claude_bin", return_value="/usr/local/bin/claude"),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            await _invoke_skill("handoff", "/handoff", "testjob")
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    assert captured_env is not None
    assert "ANTHROPIC_API_KEY" not in captured_env


@pytest.mark.anyio
async def test_run_all_allowed_skills_return_started(app_client):
    """All three allowed skills (handoff, review, init) return status=started."""
    with (
        patch("routers.skills._find_claude_bin", return_value="/usr/local/bin/claude"),
        patch("asyncio.create_task"),
    ):
        async with app_client as c:
            for skill_id in ("handoff", "review", "init"):
                resp = await c.post(
                    "/api/skills/run", json={"skill_id": skill_id, "args": {}}
                )
                assert resp.status_code == 200, f"skill_id={skill_id}"
                assert resp.json()["status"] == "started", f"skill_id={skill_id}"
