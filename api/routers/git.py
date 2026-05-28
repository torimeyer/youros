"""Git integration endpoints."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["git"])

_MAX_LIMIT = 50


def _sanitize(s: str) -> str:
    """Replace control chars (except tab/newline/CR) with \\uXXXX escapes."""
    if not s:
        return s
    return "".join(
        c if ord(c) >= 0x20 or c in "\t\n\r" else f"\\u{ord(c):04x}"
        for c in s
    )


@router.get("/git/recent")
async def get_recent_commits(
    limit: Annotated[int, Query(ge=0, le=_MAX_LIMIT)] = 5,
):
    """Return the last N commits from the repo.

    Caps at 50. Returns an empty list when limit=0.
    """
    if limit == 0:
        return {"commits": []}

    try:
        root_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if root_result.returncode != 0:
            raise HTTPException(status_code=500, detail="Not a git repository")
        repo_root = root_result.stdout.strip()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="git executable not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="git timed out resolving repo root")

    try:
        log_result = await asyncio.to_thread(
            subprocess.run,
            [
                "git", "log",
                f"-n{limit}",
                "--pretty=format:%H|%s|%an|%aI",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=repo_root,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="git log timed out")

    if log_result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"git log failed: {log_result.stderr.strip()[:200]}",
        )

    commits = []
    for line in log_result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        hash_, subject, author, timestamp = parts
        commits.append(
            {
                "hash": _sanitize(hash_.strip()),
                "subject": _sanitize(subject.strip()),
                "author": _sanitize(author.strip()),
                "timestamp": _sanitize(timestamp.strip()),
            }
        )

    return {"commits": commits}
