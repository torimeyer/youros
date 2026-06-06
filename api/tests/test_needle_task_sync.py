# Tests for →1069: needle-close task sync hook
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_CHAT_NOTIFICATIONS", "1")
os.environ.setdefault("MYOS_DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("MYOS_REAPER_ENABLED", "0")

from main import app

HOOK_PATH = Path(__file__).parent.parent.parent / ".githooks" / "post-commit"


def _hook_source() -> str:
    return HOOK_PATH.read_text()


def test_post_commit_hook_curl_format():
    """The hook must contain a curl call with --connect-timeout and /api/tasks/."""
    src = _hook_source()
    assert "--connect-timeout" in src, "hook must use --connect-timeout to avoid hanging commits"
    assert "/api/tasks/" in src, "hook must POST to /api/tasks/<needle>/close"
    # Must also suppress errors so a dead backend never blocks a commit
    assert ">/dev/null 2>&1 || true" in src or "2>&1 || true" in src, (
        "hook must silence errors and always succeed"
    )


def test_post_commit_hook_uses_by_needle_lookup():
    """The hook must look up the task via by-needle before calling close."""
    src = _hook_source()
    assert "by-needle" in src, (
        "hook must call GET /api/tasks/by-needle/<id> to resolve the real task_id "
        "before POSTing to /close — passing → directly in a URL path 404s"
    )
    assert "_bare=" in src or '_bare="${needle#' in src, (
        "hook must strip the → arrow and use the bare numeric ID in URLs"
    )


def test_post_commit_hook_exits_zero_on_backend_down(tmp_path):
    """Running the hook with no backend should still exit 0."""
    # Build a tiny throwaway git repo so git log works
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feat: →9999 close this needle"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )
    # Copy the hook into the repo
    hook_dst = repo / ".git" / "hooks" / "post-commit"
    hook_dst.write_text(_hook_source())
    hook_dst.chmod(hook_dst.stat().st_mode | stat.S_IEXEC)

    # Stub out ostk so the hook thinks the close succeeded
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir(exist_ok=True)
    ostk_stub = stub_bin / "ostk"
    ostk_stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        # stub: echo "closed" so the hook's grep -q "closed" passes
        echo "closed →9999"
        exit 0
    """))
    ostk_stub.chmod(ostk_stub.stat().st_mode | stat.S_IEXEC)

    env = {**os.environ, "PATH": f"{stub_bin}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(hook_dst)],
        cwd=repo,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"hook must exit 0 even when backend is down; got {result.returncode}\n"
        f"stdout: {result.stdout.decode()}\nstderr: {result.stderr.decode()}"
    )


# ---------------------------------------------------------------------------
# API route tests for GET /api/tasks/by-needle/{needle_id}
# ---------------------------------------------------------------------------

def _make_client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_by_needle_found_by_id():
    """by-needle returns a task when the arrow-ID matches exactly."""
    sample = {"id": "→1067", "title": "Fix something", "description": "", "status": "open"}
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[sample])
        async with _make_client() as client:
            resp = await client.get("/api/tasks/by-needle/1067")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "1067"
    assert data["needle"] == "→1067"


@pytest.mark.asyncio
async def test_by_needle_arrow_prefix_accepted():
    """by-needle accepts →1067 (arrow-prefixed) as input, not just bare numbers."""
    sample = {"id": "→1067", "title": "Fix something", "description": "", "status": "open"}
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[sample])
        async with _make_client() as client:
            resp = await client.get("/api/tasks/by-needle/%E2%86%921067")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "1067"


@pytest.mark.asyncio
async def test_by_needle_found_in_title():
    """by-needle finds a task whose title contains the needle reference."""
    sample = {
        "id": "→500",
        "title": "Fix post-commit hook →1067",
        "description": "",
        "status": "open",
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[sample])
        async with _make_client() as client:
            resp = await client.get("/api/tasks/by-needle/1067")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "500"


@pytest.mark.asyncio
async def test_by_needle_found_in_description():
    """by-needle finds a task whose description contains the needle reference."""
    sample = {
        "id": "→501",
        "title": "Some other task",
        "description": "Tracks progress on →1067",
        "status": "open",
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[sample])
        async with _make_client() as client:
            resp = await client.get("/api/tasks/by-needle/1067")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "501"


@pytest.mark.asyncio
async def test_by_needle_not_found():
    """by-needle returns 404 when no matching task exists."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with _make_client() as client:
            resp = await client.get("/api/tasks/by-needle/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_by_needle_bad_id():
    """by-needle returns 400 when the needle_id is not numeric."""
    async with _make_client() as client:
        resp = await client.get("/api/tasks/by-needle/notanumber")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_close_task_normalizes_bare_id():
    """POST /tasks/{task_id}/close accepts a bare number and normalizes to →NNN."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.close_task = AsyncMock(return_value="closed →1067")
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with _make_client() as client:
            resp = await client.post(
                "/api/tasks/1067/close",
                json={"reason": "completed"},
            )
    assert resp.status_code == 200
    # Verify ostk was called with the arrow-prefixed ID
    mock_ostk.close_task.assert_called_once_with("→1067", closed_reason="completed")
