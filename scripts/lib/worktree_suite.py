"""Pre-merge gate helpers for worktree subagents.

Two jobs:

1. `infer_suite(changed_files)` decides which test suites a worktree's
   diff needs: "backend", "frontend", "both", or "none".
2. `write_status(...)` writes `.ostk/worktree_status/<branch>.json`
   with the gate result.

Both are small and pure so they are unit-testable without needing a
real worktree on disk.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

Suite = Literal["backend", "frontend", "both", "none"]
Status = Literal["ready", "parked"]


# File-path prefixes that mean a given suite must run. Kept
# deliberately narrow: changes to docs, scripts, configs, etc. do not
# trigger tests on their own. If a worktree ONLY touches docs, the
# gate marks it "ready" with suite="none".
_BACKEND_PREFIXES = ("api/",)
_FRONTEND_PREFIXES = ("app/src/", "app/public/", "app/index.html")

# Extensions that are code-like enough to want a test run if they
# land under a watched prefix.
_BACKEND_EXTS = (".py",)
_FRONTEND_EXTS = (".ts", ".tsx", ".js", ".jsx", ".css", ".html")


def _matches(path: str, prefixes: tuple[str, ...], exts: tuple[str, ...]) -> bool:
    if not any(path.startswith(p) for p in prefixes):
        return False
    return any(path.endswith(e) for e in exts)


def infer_suite(changed_files: Iterable[str]) -> Suite:
    """Pick the minimum test suite that covers these changes.

    Rules:
    - any `api/**/*.py`   → backend needed
    - any `app/src/**` or `app/public/**` code file → frontend needed
    - both kinds         → "both"
    - neither            → "none"
    """
    need_backend = False
    need_frontend = False
    for raw in changed_files:
        path = raw.strip()
        if not path:
            continue
        if _matches(path, _BACKEND_PREFIXES, _BACKEND_EXTS):
            need_backend = True
        elif _matches(path, _FRONTEND_PREFIXES, _FRONTEND_EXTS):
            need_frontend = True
        if need_backend and need_frontend:
            return "both"
    if need_backend:
        return "backend"
    if need_frontend:
        return "frontend"
    return "none"


def status_path(repo_root: str | os.PathLike[str], branch: str) -> Path:
    """Where the status file for a given branch lives."""
    safe = branch.replace("/", "_")
    return Path(repo_root) / ".ostk" / "worktree_status" / f"{safe}.json"


def write_status(
    *,
    repo_root: str | os.PathLike[str],
    branch: str,
    path: str,
    status: Status,
    suite: Suite,
    failing_tests: list[str] | None = None,
    ran_at: datetime | None = None,
) -> Path:
    """Write a worktree gate status file and return its path."""
    if status not in ("ready", "parked"):
        raise ValueError(f"bad status {status!r}")
    if suite not in ("backend", "frontend", "both", "none"):
        raise ValueError(f"bad suite {suite!r}")

    ts = (ran_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Drop microseconds, keep Z suffix.
    iso = ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    payload = {
        "branch": branch,
        "path": str(path),
        "status": status,
        "suite": suite,
        "failing_tests": failing_tests or [],
        "ran_at": iso,
    }

    out = status_path(repo_root, branch)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return out
