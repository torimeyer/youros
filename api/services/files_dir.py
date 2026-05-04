"""Resolve the user-configured "files" directory.

User-facing artifacts (roadmap.md, automation outputs, fleet output .md
files) live in a single directory that the Files tab scans. Historically
this was hardcoded to ``~/.myos/files``. The ``files_dir`` setting on
the Settings page now lets the user point this at any absolute path.

Callers use :func:`get_files_dir`. It reads the configured value from
the settings store (falling back to the default) and caches the result
for the process lifetime until :func:`invalidate_files_dir_cache` is
called. The settings router invalidates on every PUT so a change takes
effect immediately without a server restart.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_DEFAULT_FILES_DIR = Path.home() / ".myos" / "files"

# Cached resolved path so repeated calls in a single request are cheap.
# ``None`` means "not cached yet".
_cached: Optional[Path] = None


def _resolve_from_settings() -> Path:
    """Read ``files_dir`` from the settings store, falling back to default."""
    try:
        from services.settings_store import settings_store
        value = settings_store.get("files_dir")
    except Exception:
        value = None
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    return _DEFAULT_FILES_DIR


def get_files_dir() -> Path:
    """Return the directory where user-facing files live.

    Resolves from the settings store once per process (or until
    :func:`invalidate_files_dir_cache` is called) and falls back to
    ``~/.myos/files`` when the setting is unset.
    """
    global _cached
    if _cached is None:
        _cached = _resolve_from_settings()
    return _cached


def invalidate_files_dir_cache() -> None:
    """Drop the cached value so the next ``get_files_dir`` re-reads settings.

    The settings router calls this after every successful PUT so a user
    change to ``files_dir`` is picked up immediately.
    """
    global _cached
    _cached = None


def default_files_dir() -> Path:
    """The hardcoded default, for callers that need to show it in the UI."""
    return _DEFAULT_FILES_DIR
