"""2972: a task's code save point (commit_ref) can be corrected via the API.

Background: on 2026-07-19 the board auto-closed →2970 while its agent was
still working and stamped the row's commit reference with a commit that
belongs to →2956. PATCH /api/tasks/{task_id} had no commit_ref support,
so a wrong stamp could never be fixed afterwards. These tests lock in the
correction path:

  1. Correcting a CLOSED task's commit reference succeeds, resolves a
     short hash to the full one, persists to the active store, and never
     reopens the task.
  2. Bare-number task ids work (the post-restart fix call uses one).
  3. A hex-shaped hash that does not exist in the repo is rejected with a
     plain-language 400 and nothing is written.
  4. Junk that is not even hash-shaped is rejected the same way.
  5. A valid hash for an unknown task returns 404.
  6. Normal updates (priority via mocked ostk, title via the real store)
     still behave exactly as before.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.ostk import ostk

WRONG_HASH = "e8ca1367710f6f2539836b8d762b0d246af7edd9"


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


@pytest.fixture
def repo_with_commit():
    """Turn the per-test ostk cwd into a real git repo with one commit.

    The autouse _isolate_tasks_ostk fixture already points ostk.cwd at a
    per-test tmp dir with an empty .ostk/needles/issues.jsonl. The commit
    created here is the real save point the tests correct rows to.
    Returns (issues_path, full_hash).
    """
    root = Path(ostk.cwd)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("2972 test repo")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "the real fix commit", "--no-verify")
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root), check=True, capture_output=True, text=True,
    )
    full_hash = out.stdout.strip()
    issues_path = root / ".ostk" / "needles" / "issues.jsonl"
    return issues_path, full_hash


def _seed_task(issues_path, task_id="→900", status="closed", commit_ref=WRONG_HASH):
    row = {
        "id": task_id,
        "title": "Declutter the Settings page",
        "status": status,
        "priority": "P1",
        "commit_ref": commit_ref,
    }
    if status == "closed":
        row["closed_at"] = "2026-07-19T18:26:23Z"
        row["closed_reason"] = "completed"
    issues_path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _rows(issues_path):
    return [
        json.loads(line)
        for line in issues_path.read_text().splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_correct_closed_task_commit_ref_persists(client, repo_with_commit):
    """Correcting a closed task's save point succeeds and lands on disk."""
    issues_path, full_hash = repo_with_commit
    _seed_task(issues_path)

    resp = await client.patch(
        "/api/tasks/→900", json={"commit_ref": full_hash[:8]}
    )
    assert resp.status_code == 200, resp.text
    assert "commit reference" in resp.json()["result"]

    row = next(r for r in _rows(issues_path) if r["id"] == "→900")
    assert row["commit_ref"] == full_hash
    assert row["status"] == "closed"  # the correction never reopens the task


@pytest.mark.asyncio
async def test_bare_number_task_id_works(client, repo_with_commit):
    """PATCH /api/tasks/900 (no arrow) targets the arrow-prefixed row."""
    issues_path, full_hash = repo_with_commit
    _seed_task(issues_path)

    resp = await client.patch("/api/tasks/900", json={"commit_ref": full_hash})
    assert resp.status_code == 200, resp.text

    row = next(r for r in _rows(issues_path) if r["id"] == "→900")
    assert row["commit_ref"] == full_hash


@pytest.mark.asyncio
async def test_nonexistent_hash_rejected_plain_language(client, repo_with_commit):
    """A hex-shaped hash that is not a real save is rejected, nothing written."""
    issues_path, _ = repo_with_commit
    _seed_task(issues_path)

    resp = await client.patch(
        "/api/tasks/→900", json={"commit_ref": "abcdef1234abcdef1234"}
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "No code save" in detail

    row = next(r for r in _rows(issues_path) if r["id"] == "→900")
    assert row["commit_ref"] == WRONG_HASH  # untouched


@pytest.mark.asyncio
async def test_non_hash_junk_rejected(client, repo_with_commit):
    issues_path, _ = repo_with_commit
    _seed_task(issues_path)

    resp = await client.patch(
        "/api/tasks/→900", json={"commit_ref": "not-a-hash!"}
    )
    assert resp.status_code == 400
    assert "does not look like a code save point" in resp.json()["detail"]

    row = next(r for r in _rows(issues_path) if r["id"] == "→900")
    assert row["commit_ref"] == WRONG_HASH


@pytest.mark.asyncio
async def test_unknown_task_returns_404(client, repo_with_commit):
    issues_path, full_hash = repo_with_commit
    _seed_task(issues_path)

    resp = await client.patch(
        "/api/tasks/→999", json={"commit_ref": full_hash}
    )
    assert resp.status_code == 404

    row = next(r for r in _rows(issues_path) if r["id"] == "→900")
    assert row["commit_ref"] == WRONG_HASH


@pytest.mark.asyncio
async def test_normal_priority_update_still_behaves(client):
    """The existing PATCH priority path is unchanged by the new field."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(
            return_value="updated t-1 priority to P0"
        )
        resp = await client.patch("/api/tasks/t-1", json={"priority": "P0"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated t-1 priority to P0"
    mock_ostk.update_task_priority.assert_called_once_with("t-1", "P0", reason=None)


@pytest.mark.asyncio
async def test_normal_title_update_still_writes_the_store(client, repo_with_commit):
    """A title change through the real store path works and leaves commit_ref alone."""
    issues_path, _ = repo_with_commit
    _seed_task(issues_path, status="open")

    resp = await client.patch(
        "/api/tasks/→900", json={"title": "A clearer title"}
    )
    assert resp.status_code == 200, resp.text

    row = next(r for r in _rows(issues_path) if r["id"] == "→900")
    assert row["title"] == "A clearer title"
    assert row["commit_ref"] == WRONG_HASH


@pytest.mark.asyncio
async def test_empty_body_still_400(client):
    resp = await client.patch("/api/tasks/→900", json={})
    assert resp.status_code == 400
