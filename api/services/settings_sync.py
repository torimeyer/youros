"""Settings sync service.

Syncs ~/.youros/ user state files to and from a private git repo the user owns.
This keeps settings, onboarding state, and templates the same across devices.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

MYOS_DIR = Path.home() / ".youros"
SYNC_CONFIG_PATH = MYOS_DIR / "sync_config.json"
SYNC_REPO_PATH = MYOS_DIR / "sync_repo"
CONFLICT_LOG_PATH = MYOS_DIR / "sync_conflicts.log"

# Files to sync into the repo (relative to MYOS_DIR)
SYNC_FILES = [
    "settings.json",
    "onboarding_complete",
    "templates.json",
]

# Files to never sync
NEVER_SYNC = {
    "notifications.json",
    "google_token.json",
    "sync_config.json",
}


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a subprocess and return the result. Raises on non-zero exit."""
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _run_quiet(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a subprocess, returning result without raising."""
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _load_config() -> dict:
    if not SYNC_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(SYNC_CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_config(data: dict) -> None:
    atomic_write_json(SYNC_CONFIG_PATH, data)


def is_configured() -> bool:
    """Return True if a sync repo URL has been configured."""
    config = _load_config()
    return bool(config.get("repo_url"))


def status() -> dict:
    """Return the current sync status."""
    config = _load_config()
    return {
        "configured": bool(config.get("repo_url")),
        "last_synced": config.get("last_synced"),
        "remote_url": config.get("repo_url"),
    }


def configure(repo_url: str) -> None:
    """Store the remote git repo URL and initialize the local sync repo."""
    MYOS_DIR.mkdir(parents=True, exist_ok=True)

    config = _load_config()
    config["repo_url"] = repo_url
    _save_config(config)

    # Initialize or re-configure the local sync repo
    if not SYNC_REPO_PATH.exists():
        SYNC_REPO_PATH.mkdir(parents=True, exist_ok=True)
        _run(["git", "init"], cwd=SYNC_REPO_PATH)

    # Set (or update) the remote origin
    result = _run_quiet(["git", "remote"], cwd=SYNC_REPO_PATH)
    if "origin" in result.stdout:
        _run(["git", "remote", "set-url", "origin", repo_url], cwd=SYNC_REPO_PATH)
    else:
        _run(["git", "remote", "add", "origin", repo_url], cwd=SYNC_REPO_PATH)

    # Try to fetch existing remote content so the first push does not diverge
    _run_quiet(["git", "fetch", "origin"], cwd=SYNC_REPO_PATH)

    logger.info("Sync configured with repo: %s", repo_url)


def push() -> dict:
    """Copy user state files into the sync repo, commit, and push."""
    if not is_configured():
        raise RuntimeError("Sync is not configured. Call configure() first.")

    config = _load_config()
    repo_url = config["repo_url"]

    SYNC_REPO_PATH.mkdir(parents=True, exist_ok=True)

    # Make sure the repo has git initialized and the remote set
    if not (SYNC_REPO_PATH / ".git").exists():
        _run(["git", "init"], cwd=SYNC_REPO_PATH)
        _run(["git", "remote", "add", "origin", repo_url], cwd=SYNC_REPO_PATH)

    # Copy files into the sync repo
    copied = []
    for filename in SYNC_FILES:
        src = MYOS_DIR / filename
        if src.exists():
            dest = SYNC_REPO_PATH / filename
            shutil.copy2(src, dest)
            copied.append(filename)

    if not copied:
        return {"result": "nothing to sync", "files": []}

    # Stage and commit
    _run(["git", "add", "--all"], cwd=SYNC_REPO_PATH)

    # Check if there's actually anything staged
    diff = _run_quiet(["git", "diff", "--cached", "--quiet"], cwd=SYNC_REPO_PATH)
    if diff.returncode == 0:
        # Nothing changed
        _update_last_synced(config)
        return {"result": "already up to date", "files": copied}

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    _run(
        ["git", "commit", "-m", f"myOS sync {timestamp}"],
        cwd=SYNC_REPO_PATH,
    )

    # Push (try main then master as default branch name)
    push_result = _run_quiet(["git", "push", "-u", "origin", "main"], cwd=SYNC_REPO_PATH)
    if push_result.returncode != 0:
        push_result2 = _run_quiet(["git", "push", "-u", "origin", "HEAD:main"], cwd=SYNC_REPO_PATH)
        if push_result2.returncode != 0:
            raise RuntimeError(
                f"Push failed: {push_result.stderr or push_result2.stderr}"
            )

    _update_last_synced(config)
    logger.info("Pushed sync: %s", copied)
    return {"result": "pushed", "files": copied}


def pull() -> dict:
    """Fetch the remote, merge, and copy files back to ~/.youros/."""
    if not is_configured():
        raise RuntimeError("Sync is not configured. Call configure() first.")

    config = _load_config()
    repo_url = config["repo_url"]

    SYNC_REPO_PATH.mkdir(parents=True, exist_ok=True)

    if not (SYNC_REPO_PATH / ".git").exists():
        _run(["git", "init"], cwd=SYNC_REPO_PATH)
        result = _run_quiet(["git", "remote"], cwd=SYNC_REPO_PATH)
        if "origin" not in result.stdout:
            _run(["git", "remote", "add", "origin", repo_url], cwd=SYNC_REPO_PATH)

    # Fetch from remote
    fetch_result = _run_quiet(["git", "fetch", "origin"], cwd=SYNC_REPO_PATH)
    if fetch_result.returncode != 0:
        raise RuntimeError(f"Fetch failed: {fetch_result.stderr}")

    # Check if FETCH_HEAD exists (i.e., remote had content)
    fetch_head = SYNC_REPO_PATH / ".git" / "FETCH_HEAD"
    if not fetch_head.exists():
        return {"result": "remote is empty", "files": []}

    # Check if we have a local HEAD (any commits yet)
    head_result = _run_quiet(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=SYNC_REPO_PATH
    )

    if head_result.returncode != 0:
        # No local commits yet, just checkout remote
        checkout_result = _run_quiet(
            ["git", "checkout", "-b", "main", "origin/main"], cwd=SYNC_REPO_PATH
        )
        if checkout_result.returncode != 0:
            # Try HEAD
            _run_quiet(
                ["git", "checkout", "-b", "main", "FETCH_HEAD"], cwd=SYNC_REPO_PATH
            )
    else:
        # Merge with remote, prefer remote on conflict
        merge_result = _run_quiet(
            ["git", "merge", "-X", "theirs", "origin/main", "--allow-unrelated-histories"],
            cwd=SYNC_REPO_PATH,
        )
        if merge_result.returncode != 0:
            # Log conflict and force checkout of remote files
            _log_conflict(merge_result.stderr)
            _run_quiet(
                ["git", "checkout", "--theirs", "."], cwd=SYNC_REPO_PATH
            )
            _run_quiet(["git", "add", "--all"], cwd=SYNC_REPO_PATH)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            _run_quiet(
                ["git", "commit", "-m", f"myOS sync conflict resolved {timestamp}"],
                cwd=SYNC_REPO_PATH,
            )

    # Copy files back to ~/.youros/
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    restored = []
    for filename in SYNC_FILES:
        src = SYNC_REPO_PATH / filename
        if src.exists():
            dest = MYOS_DIR / filename
            shutil.copy2(src, dest)
            restored.append(filename)

    _update_last_synced(config)
    logger.info("Pulled sync: %s", restored)
    return {"result": "pulled", "files": restored}


def disconnect() -> None:
    """Remove sync configuration. Does not delete the remote repo."""
    if SYNC_CONFIG_PATH.exists():
        SYNC_CONFIG_PATH.unlink()
    logger.info("Sync disconnected")


def _update_last_synced(config: Optional[dict] = None) -> None:
    if config is None:
        config = _load_config()
    config["last_synced"] = datetime.now(timezone.utc).isoformat()
    _save_config(config)


def _log_conflict(message: str) -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(CONFLICT_LOG_PATH, "a") as f:
        f.write(f"[{timestamp}] Merge conflict resolved by preferring remote:\n{message}\n\n")
