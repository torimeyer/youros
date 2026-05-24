"""Tests for the agent undo endpoints (P6).

CRITICAL: All git operations use an isolated tmp_path fixture repo.
The user's main branch is never touched.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import routers.agent_undo as undo_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


@pytest.fixture()
def fixture_repo(tmp_path: Path):
    """Bare git repo with one commit attributed to 'test-agent'."""
    _git(["init", "-b", "main"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "test-agent"], tmp_path)

    readme = tmp_path / "README.md"
    readme.write_text("hello")
    _git(["add", "."], tmp_path)
    _git(["commit", "-m", "feat: initial commit by test-agent"], tmp_path)

    return tmp_path


@pytest.fixture(autouse=True)
def _patch_repo_root(fixture_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(undo_module, "_REPO_ROOT", fixture_repo)


# ---------------------------------------------------------------------------
# GET /api/agents/{name}/last-change
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_last_change_returns_expected_shape():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agents/test-agent/last-change")

    assert resp.status_code == 200
    body = resp.json()
    assert "commit_sha" in body
    assert len(body["commit_sha"]) == 40
    assert "message" in body
    assert "files_changed" in body
    assert isinstance(body["files_changed"], list)
    assert "diff" in body
    assert isinstance(body["diff"], str)


@pytest.mark.asyncio
async def test_last_change_returns_404_for_unknown_agent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agents/nobody-has-this-name-ever/last-change")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_last_change_message_matches_commit(fixture_repo: Path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/agents/test-agent/last-change")

    body = resp.json()
    assert "initial commit by test-agent" in body["message"]
    assert "README.md" in body["files_changed"]


# ---------------------------------------------------------------------------
# POST /api/agents/{name}/undo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_undo_requires_confirm(fixture_repo: Path):
    # First grab the SHA
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sha_resp = await client.get("/api/agents/test-agent/last-change")
        sha = sha_resp.json()["commit_sha"]

        resp = await client.post(
            "/api/agents/test-agent/undo",
            json={"commit_sha": sha, "confirm": False},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_undo_rejects_invalid_sha():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/test-agent/undo",
            json={"commit_sha": "not-a-sha; rm -rf /", "confirm": True},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_undo_creates_revert_commit(fixture_repo: Path):
    # Add a second commit so HEAD moves past the initial one.
    (fixture_repo / "newfile.txt").write_text("added by agent")
    _git(["add", "."], fixture_repo)
    _git(["commit", "-m", "add newfile by test-agent"], fixture_repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sha_resp = await client.get("/api/agents/test-agent/last-change")
        sha = sha_resp.json()["commit_sha"]

        resp = await client.post(
            "/api/agents/test-agent/undo",
            json={"commit_sha": sha, "confirm": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reverted"] == sha
    assert len(body["revert_commit"]) == 40
    assert "message" in body

    # Verify the revert commit exists in git log.
    log = _git(["log", "--oneline", "-2"], fixture_repo)
    assert "Revert" in log.stdout
