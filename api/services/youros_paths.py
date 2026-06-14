"""Single source of truth for yourOS on-disk paths.

Every backend module and script resolves user-data locations through this module
instead of hardcoding ``~/.youros``. Setting ``YOUROS_HOME`` redirects the ENTIRE
profile (settings, tasks, chats, agent state, caches) to one directory, which is
what makes a throwaway test profile possible:

    YOUROS_HOME=/tmp/youros-test ./start.sh

Resolution is lazy: ``youros_home()`` reads the environment on every call, so a
test can set ``YOUROS_HOME`` and immediately see the change (no import-time
caching here). Modules that bind a derived path to a module-level constant at
import time still pick up ``YOUROS_HOME`` as long as it is set before the process
starts (the normal case for start.sh) or the module is reloaded (the test case).

Back-compat: the older per-area overrides still win for their specific area when
set, so existing setups keep working:
  - ``MYOS_DIR``               -> whole data root (legacy alias for YOUROS_HOME)
  - ``MYOS_USER_SPECS_DIR`` / ``YOUROS_USER_SPECS_DIR``   -> specs dir
  - ``MYOS_USER_DRAFTS_DIR`` / ``YOUROS_USER_DRAFTS_DIR`` -> drafts dir
"""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DIRNAME = ".youros"


def youros_home() -> Path:
    """Return the yourOS data root.

    Precedence: ``YOUROS_HOME`` -> ``MYOS_DIR`` (legacy) -> ``~/.youros``.
    """
    env = os.environ.get("YOUROS_HOME") or os.environ.get("MYOS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / _DEFAULT_DIRNAME


def data_path(*parts: str) -> Path:
    """Return a path under the data root, e.g. ``data_path("tasks.json")``."""
    return youros_home().joinpath(*parts)


def specs_dir() -> Path:
    """User specs directory. Honors MYOS_USER_SPECS_DIR / YOUROS_USER_SPECS_DIR."""
    env = os.environ.get("MYOS_USER_SPECS_DIR") or os.environ.get("YOUROS_USER_SPECS_DIR")
    return Path(env).expanduser() if env else youros_home() / "specs"


def drafts_dir() -> Path:
    """User drafts directory. Honors MYOS_USER_DRAFTS_DIR / YOUROS_USER_DRAFTS_DIR."""
    env = os.environ.get("MYOS_USER_DRAFTS_DIR") or os.environ.get("YOUROS_USER_DRAFTS_DIR")
    return Path(env).expanduser() if env else youros_home() / "drafts"


def files_dir() -> Path:
    """User files directory (uploads, imports, project files)."""
    return youros_home() / "files"


def logs_dir() -> Path:
    """Log directory under the data root."""
    return youros_home() / "logs"
