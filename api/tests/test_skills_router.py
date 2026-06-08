"""
Tests for POST /api/skills/run  (FR-009 — →1394)

Verifies:
  - 200 + status="started" for known skill_ids
  - 422 for unknown skill_ids
  - The background task is created to invoke the skill on the provider
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
    def _close_coro(coro):
        coro.close()
        
    with patch("asyncio.create_task", side_effect=_close_coro):
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
async def test_invoke_skill_calls_subprocess_with_slash_command(tmp_path):
    """ClaudeCodeRuntimeProvider.invoke_skill spawns claude --print /handoff."""
    from services.claude_code_provider import ClaudeCodeRuntimeProvider

    captured_cmd: list | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        return _make_proc()

    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        provider = ClaudeCodeRuntimeProvider()
        await provider.invoke_skill("handoff")

    assert captured_cmd is not None
    assert "/handoff" in captured_cmd


@pytest.mark.anyio
async def test_run_skill_strips_api_key_from_env(tmp_path):
    """invoke_skill must not forward ANTHROPIC_API_KEY to subprocess."""
    import os
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-key"

    captured_env: dict | None = None

    async def fake_exec(*cmd, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get("env", {})
        return _make_proc()

    try:
        from services.claude_code_provider import ClaudeCodeRuntimeProvider
        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            provider = ClaudeCodeRuntimeProvider()
            await provider.invoke_skill("handoff")
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    assert captured_env is not None
    assert "ANTHROPIC_API_KEY" not in captured_env


@pytest.mark.anyio
async def test_run_all_allowed_skills_return_started(app_client):
    """All three allowed skills (handoff, review, init) return status=started."""
    def _close_coro(coro):
        coro.close()
        
    with patch("asyncio.create_task", side_effect=_close_coro):
        async with app_client as c:
            for skill_id in ("handoff", "review", "init"):
                resp = await c.post(
                    "/api/skills/run", json={"skill_id": skill_id, "args": {}}
                )
                assert resp.status_code == 200, f"skill_id={skill_id}"
                assert resp.json()["status"] == "started", f"skill_id={skill_id}"
