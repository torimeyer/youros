"""Resolve the user-facing files directory.

A single source of truth so callers do not hard-code
``Path.home() / ".myos" / "files"``. If the user has set
``settings.files_dir`` via the Settings page, that wins; otherwise we
fall back to the default under the user's home directory.
"""

from pathlib import Path

from services.settings_store import settings_store


DEFAULT_FILES_DIR = Path.home() / ".myos" / "files"


def get_files_dir() -> Path:
    """Return the directory where user-visible files live.

    Reads ``settings.files_dir`` at call-time so changes via the
    Settings page take effect without a restart.
    """
    configured = settings_store.get("files_dir")
    if configured:
        try:
            return Path(str(configured)).expanduser()
        except Exception:
            return DEFAULT_FILES_DIR
    return DEFAULT_FILES_DIR
