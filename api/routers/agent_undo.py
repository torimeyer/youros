"""Agent undo router.

Lets the user undo the last commit made by a specific agent:
  GET  /api/agents/{name}/last-change  →  {commit_sha, message, files_changed, diff}
  POST /api/agents/{name}/undo         ← {commit_sha, confirm: true} → revert commit
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["agent-undo"])

# api/routers/agent_undo.py → api/routers → api → repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


def _validate_sha(sha: str) -> None:
    if not _SHA_RE.match(sha):
        raise HTTPException(status_code=400, detail="Invalid commit SHA format.")


def _run_git(args: list[str], cwd: Path, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd),
    )


async def _find_last_agent_commit(name: str, repo: Path) -> Optional[str]:
    """Return the SHA of the most recent commit attributed to *name*.

    Tries --author first (matches git user name), then --grep (searches
    commit message body) so agent commits that carry the name as a
    Co-Authored-By tag or in the message body are also found.
    """
    for flag, pattern in [("--author", name), ("--grep", name)]:
        result = await asyncio.to_thread(
            _run_git,
            ["log", "--all", "-1", "--format=%H", flag, pattern],
            repo,
        )
        sha = result.stdout.strip()
        if sha and _SHA_RE.match(sha):
            return sha
    return None


@router.get("/agents/{name}/last-change")
async def get_last_change(name: str):
    """Return the most recent commit made by *name*, with its diff."""
    sha = await _find_last_agent_commit(name, _REPO_ROOT)
    if not sha:
        raise HTTPException(
            status_code=404,
            detail=f"No commits found for agent '{name}'.",
        )

    # Commit metadata
    meta = await asyncio.to_thread(
        _run_git,
        ["show", sha, "--no-patch", "--format=%s"],
        _REPO_ROOT,
    )
    message = meta.stdout.strip().splitlines()[0] if meta.stdout.strip() else ""

    # Changed file list (--name-only, skip the empty first line)
    files_result = await asyncio.to_thread(
        _run_git,
        ["show", sha, "--name-only", "--format="],
        _REPO_ROOT,
    )
    files_changed = [f for f in files_result.stdout.strip().splitlines() if f]

    # Full patch
    diff_result = await asyncio.to_thread(
        _run_git,
        ["show", sha, "--format=", "-p"],
        _REPO_ROOT,
    )
    diff = diff_result.stdout

    return {
        "commit_sha": sha,
        "message": message,
        "files_changed": files_changed,
        "diff": diff,
    }


class UndoRequest(BaseModel):
    commit_sha: str
    confirm: bool = False


@router.post("/agents/{name}/undo")
async def undo_last_change(name: str, body: UndoRequest):
    """Revert the specified commit, creating a new undo commit.

    Requires confirm=true so the UI cannot trigger this without the
    user explicitly pressing Confirm in the modal.
    """
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to apply undo.")

    _validate_sha(body.commit_sha)

    result = await asyncio.to_thread(
        _run_git,
        ["revert", "--no-edit", body.commit_sha],
        _REPO_ROOT,
        30,
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=result.stderr.strip() or "git revert failed.",
        )

    # Return the new revert commit SHA so the UI can confirm it landed.
    head = await asyncio.to_thread(
        _run_git, ["rev-parse", "HEAD"], _REPO_ROOT
    )
    return {
        "reverted": body.commit_sha,
        "revert_commit": head.stdout.strip(),
        "message": f"Change by agent '{name}' has been undone.",
    }
