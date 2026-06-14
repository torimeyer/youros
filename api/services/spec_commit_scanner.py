"""Passive git-commit watcher for spec auto-status (→1427, Phase 3).

Scans recent commits every 60 s. For each commit that mentions a spec slug
(file stem under ~/.youros/specs/*.md) or a task ID (→NNNN owned by a spec),
records a source=passive claim so the Specs page flips to Building without
any explicit agent self-registration.

This is the 60-second cron fallback for environments where the
ostk_post_commit_hook.sh post-commit hook is not installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional
from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

_TASK_ID_RE = re.compile(r"(?:→|->)(\d+)")

# Commit hashes already processed this process lifetime (survives across ticks).
_seen_commits: set[str] = set()

_SPECS_DIR_ENV = os.environ.get("MYOS_USER_SPECS_DIR", str(youros_home() / "specs"))
SPECS_DIR = Path(_SPECS_DIR_ENV)


def _get_slug_map(specs_dir: Optional[Path] = None) -> dict[str, str]:
    """Return {slug: absolute_path_str} for all specs under SPECS_DIR."""
    d = specs_dir if specs_dir is not None else SPECS_DIR
    if not d.is_dir():
        return {}
    return {md.stem: str(md.resolve()) for md in d.glob("*.md")}


def _recent_commits(repo_path: Path, n: int = 5) -> list[dict]:
    """Return the last N commits as list of {hash, author, message}."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", f"-{n}", "--pretty=%H|%an|%s"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        commits = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"hash": parts[0], "author": parts[1], "message": parts[2]})
        return commits
    except Exception:
        logger.debug("spec_commit_scanner: git log failed", exc_info=True)
        return []


def _slugs_in_message(message: str, slug_map: dict[str, str]) -> list[str]:
    """Return absolute spec paths whose slug appears as a whole word in message."""
    found: list[str] = []
    for slug, abs_path in slug_map.items():
        if re.search(r"\b" + re.escape(slug) + r"\b", message, re.IGNORECASE):
            found.append(abs_path)
    return found


def _task_ids_in_message(message: str) -> list[str]:
    """Return task IDs (digit strings) mentioned via →NNNN or ->NNNN in message."""
    return [m.group(1) for m in _TASK_ID_RE.finditer(message)]


def _spec_path_for_task(task_id: str) -> Optional[str]:
    """Look up which spec owns task_id via the in-memory _spec_task_origin map."""
    try:
        from routers.specs import _spec_task_origin  # type: ignore[import]
        return _spec_task_origin.get(task_id)
    except Exception:
        return None


async def _record_passive_claim(spec_path: str, author: str) -> None:
    """Record a passive claim directly into _spec_claims (no HTTP round-trip).

    Calls _ensure_decomposed so the spec gets tasks if it hasn't been built
    yet, then appends a {source: passive} claim entry.
    """
    try:
        from routers.specs import _spec_claims, _ensure_decomposed  # type: ignore[import]
    except Exception:
        logger.debug("spec_commit_scanner: could not import spec claim state")
        return

    tasks = await _ensure_decomposed(spec_path)
    task_ids = [str(t.get("id", "")).lstrip("→") for t in tasks]

    claim: dict = {
        "agent": f"passive/{author}",
        "source": "passive",
        "started_at": datetime.now(_tz.utc).isoformat(),
        "task_ids": task_ids,
    }
    if spec_path not in _spec_claims:
        _spec_claims[spec_path] = []
    _spec_claims[spec_path].append(claim)
    logger.info("spec_commit_scanner: passive claim recorded for %s (author=%s)", spec_path, author)


async def scan_and_claim(
    repo_path: Optional[Path] = None,
    specs_dir: Optional[Path] = None,
    seen_commits: Optional[set] = None,
) -> dict:
    """Scan recent commits and record passive claims for spec matches.

    Args:
        repo_path: git repo to scan.  Defaults to PROJECT_ROOT.
        specs_dir: directory of spec files.  Defaults to SPECS_DIR (~/.youros/specs).
        seen_commits: mutable set of already-processed commit hashes.  Defaults
            to the module-level ``_seen_commits`` (persistent across ticks).

    Returns a summary dict: {commits_scanned, claims_recorded}.
    """
    if repo_path is None:
        from config import PROJECT_ROOT  # type: ignore[import]
        repo_path = Path(PROJECT_ROOT)

    if seen_commits is None:
        seen_commits = _seen_commits

    slug_map = _get_slug_map(specs_dir)
    # ->1806: _recent_commits runs a blocking `git log` subprocess. Offload it
    # to a worker thread so the 60s scanner tick never blocks the event loop
    # (forking git on the loop stalls TLS and re-wedges a healthy backend).
    commits = await asyncio.to_thread(_recent_commits, repo_path)

    commits_scanned = 0
    claims_recorded = 0

    for commit in commits:
        h = commit["hash"]
        if h in seen_commits:
            continue
        seen_commits.add(h)
        commits_scanned += 1

        msg = commit["message"]
        author = commit["author"]

        spec_paths: list[str] = []

        for abs_path in _slugs_in_message(msg, slug_map):
            if abs_path not in spec_paths:
                spec_paths.append(abs_path)

        for task_id in _task_ids_in_message(msg):
            sp = _spec_path_for_task(task_id)
            if sp and sp not in spec_paths:
                spec_paths.append(sp)

        for sp in spec_paths:
            try:
                await _record_passive_claim(sp, author)
                claims_recorded += 1
            except Exception:
                logger.exception("spec_commit_scanner: claim failed for %s commit=%s", sp, h)

    return {"commits_scanned": commits_scanned, "claims_recorded": claims_recorded}
