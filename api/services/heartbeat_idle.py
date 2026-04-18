"""
Transcript-idle detection for the subagent heartbeat loop.

The shell hook's heartbeat loop calls this as a subprocess:
    python3 /path/to/api/services/heartbeat_idle.py <agent_name> [threshold_seconds]

Exit codes:
    0  keep going (transcript is active, file missing, or first-iteration grace)
    1  auto-complete (transcript idle beyond threshold)

Pure functions are exported for direct import by tests.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

TRANSCRIPT_IDLE_SECONDS = 120

# Repo root: api/services/heartbeat_idle.py -> api/services -> api -> repo
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _project_label(repo_root: Path) -> str:
    return str(repo_root).replace("/", "-").lstrip("-")


def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _tasks_root() -> Path:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(f"/private/tmp/claude-{uid}")


def find_transcript(name: str, repo_root: Optional[Path] = None) -> Optional[Path]:
    """Return the freshest transcript file for the named agent, or None.

    Searches (in order):
    1. ~/.claude/projects/-<label>/<session>/subagents/agent-*.jsonl
       whose first 4 KB references the agent name.
    2. /private/tmp/claude-<uid>/-<label>/<session>/tasks/*.output
       whose first line references the agent name.
    3. <repo_root>/transcripts/<name>.md (legacy).
    """
    root = repo_root or _REPO_ROOT
    label = f"-{_project_label(root)}"

    best: Optional[tuple[float, Path]] = None

    def _check_newer(path: Path) -> None:
        nonlocal best
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if best is None or mtime > best[0]:
            best = (mtime, path)

    # 1. Claude Code subagent JSONL files.
    project_dir = _claude_projects_dir() / label
    if project_dir.exists():
        try:
            for session_dir in project_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                subagents_dir = session_dir / "subagents"
                if not subagents_dir.exists():
                    continue
                for f in subagents_dir.glob("agent-*.jsonl"):
                    try:
                        with open(f, "rb") as fh:
                            head = fh.read(4096).decode("utf-8", errors="replace")
                        if name in head:
                            _check_newer(f)
                    except OSError:
                        pass
        except OSError:
            pass

    # 2. Tasks output files.
    tasks_project_dir = _tasks_root() / label
    if tasks_project_dir.exists():
        try:
            for session_dir in tasks_project_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                tasks_dir = session_dir / "tasks"
                if not tasks_dir.exists():
                    continue
                for f in tasks_dir.glob("*.output"):
                    try:
                        with open(f, "r", errors="replace") as fh:
                            first = fh.readline(512)
                        if name in first:
                            _check_newer(f)
                    except OSError:
                        pass
        except OSError:
            pass

    if best is not None:
        return best[1]

    # 3. Legacy markdown.
    md = root / "transcripts" / f"{name}.md"
    if md.exists():
        try:
            if md.stat().st_size > 0:
                return md
        except OSError:
            pass

    return None


def decide_to_complete(
    transcript_path: Optional[Path],
    threshold_seconds: int = TRANSCRIPT_IDLE_SECONDS,
    *,
    _now: Optional[float] = None,
) -> bool:
    """Return True if the transcript has been idle long enough to auto-complete.

    Returns False when:
    - transcript_path is None (file not found; fresh spawn grace period)
    - file does not exist on disk
    - file mtime is within threshold_seconds of now

    Returns True when:
    - file exists and its mtime is at least threshold_seconds old
    """
    if transcript_path is None:
        return False
    try:
        mtime = transcript_path.stat().st_mtime
    except OSError:
        return False
    now = _now if _now is not None else time.time()
    return (now - mtime) >= threshold_seconds


def main(argv: list) -> int:
    if len(argv) < 2:
        print(
            "usage: heartbeat_idle.py <agent_name> [threshold_seconds]",
            file=sys.stderr,
        )
        return 2

    name = argv[1]
    try:
        threshold = int(argv[2]) if len(argv) > 2 else TRANSCRIPT_IDLE_SECONDS
    except ValueError:
        threshold = TRANSCRIPT_IDLE_SECONDS

    transcript_path = find_transcript(name)
    if decide_to_complete(transcript_path, threshold):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
