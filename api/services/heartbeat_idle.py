"""
Transcript-idle detection for the subagent heartbeat loop.

The shell hook's heartbeat loop calls this as a subprocess:
    python3 /path/to/api/services/heartbeat_idle.py <agent_name> \
            [threshold_seconds] [spawned_at_epoch]

Exit codes:
    0  keep going (transcript is active, file missing, or first-iteration grace)
    1  auto-complete (transcript idle beyond threshold, or spawn-age ceiling
       hit regardless of transcript signal)

Pure functions are exported for direct import by tests.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

TRANSCRIPT_IDLE_SECONDS = 120

# Hard ceiling on agent lifetime regardless of transcript signal. If an agent
# has been "running" this long, treat it as a zombie even if find_transcript
# keeps matching a busy unrelated file. Protects against the substring-in-4KB
# false match that latches a probe-style agent onto whichever subagent JSONL
# happens to mention it and thus never reaches the idle threshold.
SPAWN_AGE_CEILING_SECONDS = 900  # 15 minutes

# Repo root: api/services/heartbeat_idle.py -> api/services -> api -> repo
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _project_label(repo_root: Path) -> str:
    return str(repo_root).replace("/", "-").lstrip("-")


def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _tasks_root() -> Path:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tempfile.gettempdir()) / f"claude-{uid}"


def _name_appears(name: str, text: str) -> bool:
    """Strict name match for transcript attribution.

    Require the agent name to appear with non-identifier chars on both sides
    (word boundary) so that a probe agent like "retro-on-missed-root-cause"
    does not match when its name is only a substring of a bigger quoted
    prompt that belongs to an UNRELATED subagent's JSONL. That substring
    false match was the root cause of the zombie-agent bug: the bigger
    file's mtime kept refreshing, so the probe's transcript never looked
    idle and the idle-sweep could not close the row.

    ``re.escape`` the name so dashes, slashes, and other punctuation are
    treated as literals.
    """
    if not name or not text:
        return False
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None


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
                        if _name_appears(name, head):
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
                        if _name_appears(name, first):
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
    spawned_at_epoch: Optional[float] = None,
    spawn_age_ceiling_seconds: int = SPAWN_AGE_CEILING_SECONDS,
) -> bool:
    """Return True if the agent has been idle long enough to auto-complete.

    Two independent signals can trigger completion:

    1. Transcript-idle (primary): ``transcript_path`` exists and its mtime is
       at least ``threshold_seconds`` seconds old. Unchanged from the original
       behavior.
    2. Spawn-age ceiling (belt-and-suspenders): ``spawned_at_epoch`` is at
       least ``spawn_age_ceiling_seconds`` seconds old. Fires REGARDLESS of
       transcript state. Protects against find_transcript latching onto an
       unrelated busy JSONL whose mtime keeps refreshing forever, which was
       the zombie-agent root cause before this change.

    Returns False when:
    - transcript_path is None or missing AND spawned_at_epoch is also None or
      still within the spawn-age ceiling (fresh-spawn grace period).
    - transcript_path exists and its mtime is within threshold_seconds.
    - spawned_at_epoch exists but is within the ceiling.
    """
    now = _now if _now is not None else time.time()

    # Signal 2: spawn-age ceiling. Pure age check, independent of file state.
    if spawned_at_epoch is not None and spawn_age_ceiling_seconds > 0:
        if (now - spawned_at_epoch) >= spawn_age_ceiling_seconds:
            return True

    # Signal 1: transcript idle.
    if transcript_path is None:
        return False
    try:
        mtime = transcript_path.stat().st_mtime
    except OSError:
        return False
    return (now - mtime) >= threshold_seconds


def _parse_epoch_arg(arg: str) -> Optional[float]:
    """Parse an epoch-seconds CLI arg. Accepts int/float or an ISO-8601 string
    (``2026-04-19T03:26:11.034340+00:00``). Returns None for empty/"-"/unparseable.
    """
    if not arg or arg == "-":
        return None
    try:
        return float(arg)
    except ValueError:
        pass
    # ISO-8601 fallback.
    iso = arg
    # Python 3.9 datetime.fromisoformat doesn't accept trailing "Z"; normalize.
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


def main(argv: list) -> int:
    if len(argv) < 2:
        print(
            "usage: heartbeat_idle.py <agent_name> [threshold_seconds] [spawned_at_epoch]",
            file=sys.stderr,
        )
        return 2

    name = argv[1]
    try:
        threshold = int(argv[2]) if len(argv) > 2 else TRANSCRIPT_IDLE_SECONDS
    except ValueError:
        threshold = TRANSCRIPT_IDLE_SECONDS

    spawned_at_epoch = _parse_epoch_arg(argv[3]) if len(argv) > 3 else None

    transcript_path = find_transcript(name)
    if decide_to_complete(
        transcript_path,
        threshold,
        spawned_at_epoch=spawned_at_epoch,
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
