"""
Transcript-idle detection for the subagent heartbeat loop.

The shell hook's heartbeat loop calls this as a subprocess:
    python3 /path/to/api/services/heartbeat_idle.py <agent_name> \
            [threshold_seconds] [spawned_at_epoch] [pid] [last_heartbeat]

Exit codes:
    0  keep going (transcript is active, file missing, first-iteration grace,
       or ANY liveness signal says the agent is alive)
    1  auto-complete (transcript idle beyond threshold or spawn-age ceiling
       hit, AND no liveness signal contradicts it)

→2607: silence alone is never sufficient to complete an agent. Overnight on
2026-07-09 this module flipped live agents to completed because it decided
from transcript mtime and spawn age only. Completion now requires a liveness
probe to FAIL: a live pid, transcript growth since the previous check, or a
recent heartbeat each veto completion — including the spawn-age ceiling.

→2659: vetoes are not enough — a POSITIVE death signal is also required.
On 2026-07-10 the spawn-age ceiling completed saa-2650-slack-chat (live,
mid-pytest, output growing) because none of the probes could SEE it: pid
unknown, transcript unresolved, heartbeat quiet. "No data" now keeps the
agent running; only a confirmed-dead pid or a resolved-but-idle transcript
may complete it.

Pure functions are exported for direct import by tests.
"""
from __future__ import annotations

import json
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

    # 1b. Worktree-project top-level JSONL (bridge-spawned agents).
    # Bridge-spawned agents run as independent claude-code sessions inside
    # their worktree. Their transcript lives at
    #   ~/.claude/projects/<worktree-label>/<session>.jsonl
    # — NOT in the parent session's subagents/ dir. The project dir name
    # is derived from the worktree path, which always contains the agent
    # name (e.g. "-Users-...-agent-port-release-notes-...-to-9962ff").
    # Scan all project dirs that contain the agent name to find these.
    # Root cause: port-release-notes-ux-bundle-to-9962ff (2026-05-04) —
    # find_transcript returned None for a worktree agent that was actively
    # writing to its transcript, so only the 900s spawn-age ceiling fired.
    try:
        claude_projects = _claude_projects_dir()
        if claude_projects.exists():
            for proj_dir in claude_projects.iterdir():
                if not proj_dir.is_dir():
                    continue
                if name not in proj_dir.name:
                    continue
                for f in proj_dir.glob("*.jsonl"):
                    _check_newer(f)
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


def _pid_is_alive(pid) -> Optional[bool]:
    """POSIX existence probe, same pattern as services/ghost_reaper.py.

    Returns True when the process exists (including EPERM — it exists but
    is not ours; keep safe), False when it is confirmed gone, and None when
    no usable pid was supplied (no signal either way).
    """
    if pid is None:
        return None
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else — alive
    except OSError:
        return True  # unknown state — keep safe, never flip on ambiguity


def _idle_state_dir() -> Path:
    return _tasks_root() / "heartbeat-idle-state"


def transcript_grew_since_last_check(
    name: str,
    transcript_path: Optional[Path],
    state_dir: Optional[Path] = None,
) -> bool:
    """Return True when the transcript changed since the previous check.

    Persists ``{path, size}`` per agent under ``state_dir`` (default:
    ``<tasks_root>/heartbeat-idle-state``). Any change — growth, shrink
    (rotation), or a different file resolved by find_transcript — counts as
    activity. The FIRST observation also returns True: with no baseline,
    growth cannot be ruled out, so one full check interval must pass with a
    stable size before "no growth" can be asserted (→2607: silence alone is
    never sufficient).

    A missing transcript returns False — there is nothing to measure, and
    the caller's transcript-idle signal cannot fire without a file anyway.
    """
    if transcript_path is None:
        return False
    try:
        size = transcript_path.stat().st_size
    except OSError:
        return False

    sdir = state_dir if state_dir is not None else _idle_state_dir()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:200] or "_"
    state_file = sdir / f"{safe}.json"

    prev: Optional[dict] = None
    try:
        with open(state_file, "r") as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = None

    changed = (
        prev is None
        or prev.get("path") != str(transcript_path)
        or prev.get("size") != size
    )

    try:
        sdir.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(
                {"path": str(transcript_path), "size": size, "checked_at": time.time()},
                fh,
            )
        os.replace(tmp, state_file)
    except OSError:
        pass  # best-effort state; a write failure must not break the probe

    return changed


def decide_to_complete(
    transcript_path: Optional[Path],
    threshold_seconds: int = TRANSCRIPT_IDLE_SECONDS,
    *,
    _now: Optional[float] = None,
    spawned_at_epoch: Optional[float] = None,
    spawn_age_ceiling_seconds: int = SPAWN_AGE_CEILING_SECONDS,
    pid: Optional[int] = None,
    last_heartbeat_epoch: Optional[float] = None,
    heartbeat_grace_seconds: Optional[int] = None,
    transcript_grew: Optional[bool] = None,
) -> bool:
    """Return True if the agent has been idle long enough to auto-complete.

    →2607 liveness gate (checked FIRST, vetoes everything below including
    the spawn-age ceiling — silence alone is never sufficient):

    - ``pid``: when supplied and the process is alive (``os.kill(pid, 0)``),
      never complete. A live process is ground truth.
    - ``transcript_grew``: caller-observed transcript size growth since the
      previous check (see :func:`transcript_grew_since_last_check`). Growth
      means the agent is issuing tool calls; never complete.
    - ``last_heartbeat_epoch``: a heartbeat POST within
      ``heartbeat_grace_seconds`` (default: ``threshold_seconds``) means the
      agent is alive between tool calls; never complete.

    Two independent signals can then trigger completion, and each is a
    POSITIVE death observation (→2659 — "no data" never completes):

    1. Transcript-idle (primary): ``transcript_path`` exists and its mtime is
       at least ``threshold_seconds`` seconds old — a resolved file observed
       unwritten for the full window.
    2. Spawn-age ceiling: ``spawned_at_epoch`` is at least
       ``spawn_age_ceiling_seconds`` seconds old AND the pid is confirmed
       dead (``os.kill`` raised ``ProcessLookupError``). Without a pid on
       record the ceiling never fires; a silent row with no resolvable
       transcript is left to the backend's 15-minute terminated_stale sweep.

    Returns False when:
    - any liveness signal above says the agent is alive.
    - transcript_path is None or missing AND spawned_at_epoch is also None or
      still within the spawn-age ceiling (fresh-spawn grace period).
    - transcript_path exists and its mtime is within threshold_seconds.
    - spawned_at_epoch exists but is within the ceiling.
    """
    now = _now if _now is not None else time.time()

    # ---- Liveness gate (→2607) --------------------------------------------
    pid_alive = _pid_is_alive(pid)
    if pid_alive:
        return False
    if transcript_grew:
        return False
    if last_heartbeat_epoch is not None:
        grace = (
            heartbeat_grace_seconds
            if heartbeat_grace_seconds is not None
            else threshold_seconds
        )
        if (now - last_heartbeat_epoch) < grace:
            return False

    # ---- Positive death signal required (→2659) ---------------------------
    # Passing every veto above only proves the agent is SILENT, and silence
    # is not death: on 2026-07-10 saa-2650-slack-chat was mid-pytest (unable
    # to heartbeat, pid never recorded, transcript unresolved) when the
    # ceiling below flipped it to completed at spawn+918s while its output
    # was demonstrably growing. Completion now needs positive evidence:
    # a pid confirmed dead (os.kill -> ProcessLookupError), or a RESOLVED
    # transcript observed unwritten for the full idle threshold.
    pid_confirmed_dead = pid_alive is False

    # Signal 2: spawn-age ceiling — only past a confirmed-dead pid. With no
    # pid on record the ceiling can no longer complete anyone; unresolved
    # rows are left to the backend's 15-minute stale sweep, which reaps with
    # an honest terminated_stale reason instead of a false "completed".
    if pid_confirmed_dead and spawned_at_epoch is not None and spawn_age_ceiling_seconds > 0:
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


def _parse_pid_arg(arg: str) -> Optional[int]:
    """Parse a pid CLI arg. Returns None for empty/"-"/non-numeric/<=0."""
    if not arg or arg == "-":
        return None
    try:
        pid = int(arg)
    except ValueError:
        return None
    return pid if pid > 0 else None


def main(argv: list) -> int:
    if len(argv) < 2:
        print(
            "usage: heartbeat_idle.py <agent_name> [threshold_seconds] "
            "[spawned_at_epoch] [pid] [last_heartbeat]",
            file=sys.stderr,
        )
        return 2

    name = argv[1]
    try:
        threshold = int(argv[2]) if len(argv) > 2 else TRANSCRIPT_IDLE_SECONDS
    except ValueError:
        threshold = TRANSCRIPT_IDLE_SECONDS

    spawned_at_epoch = _parse_epoch_arg(argv[3]) if len(argv) > 3 else None
    pid = _parse_pid_arg(argv[4]) if len(argv) > 4 else None
    last_heartbeat_epoch = _parse_epoch_arg(argv[5]) if len(argv) > 5 else None

    transcript_path = find_transcript(name)
    grew = transcript_grew_since_last_check(name, transcript_path)
    if decide_to_complete(
        transcript_path,
        threshold,
        spawned_at_epoch=spawned_at_epoch,
        pid=pid,
        last_heartbeat_epoch=last_heartbeat_epoch,
        transcript_grew=grew,
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
