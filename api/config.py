"""Central configuration for the yourOS API.

All path references derive from PROJECT_ROOT, which is computed from this
file's location so the backend works regardless of where the repo lives.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OSTK_DIR = PROJECT_ROOT / ".ostk"
AGENTS_DIR = PROJECT_ROOT / "agents"

FRONTEND_URL_DEFAULT = "https://localhost:8000"


def _has_git_marker(path: Path) -> bool:
    """Return True when path contains a .git directory or a worktree .git file."""
    git_entry = path / ".git"
    return git_entry.is_dir() or git_entry.is_file()


def get_active_repo_path() -> Path:
    """Return the active repository path for repo-scoped backend operations.

    Priority:
      1. OSTK_PROJECT_ROOT or OSTK_ROOT env var when set and existing.
      2. active_repo_path from settings_store.load_settings() when non-empty,
         existing as a directory, and containing a .git dir or worktree file.
      3. Fallback to PROJECT_ROOT.
    """
    env_root = os.environ.get("OSTK_PROJECT_ROOT") or os.environ.get("OSTK_ROOT")
    if env_root:
        env_path = Path(env_root)
        if env_path.exists():
            return env_path

    try:
        from services import settings_store as _settings_store

        configured = _settings_store.load_settings().get("active_repo_path")
        if isinstance(configured, str) and configured.strip():
            candidate = Path(configured.strip()).expanduser().resolve()
            if candidate.is_dir() and _has_git_marker(candidate):
                return candidate
    except Exception:
        pass

    return Path(PROJECT_ROOT)
