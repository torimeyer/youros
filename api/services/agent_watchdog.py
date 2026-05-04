"""
Transcript-aware liveness watchdog for running subagents.

Progress-log mtime alone is not a reliable stall signal. An agent
working through a complex conflict-resolution loop can make genuine
tool-call progress (transcript growing) while never writing to its
progress log. Watching only the log mtime caused port-release-notes to
be cancelled 2 seconds before completion (2026-05-04).

Root cause: agent wrote 170 transcript tool-calls during the 9-minute
"stall" window, but never wrote a heartbeat line to its progress log,
so the parent's monitor declared stall and cancelled.

Two independent signals guard against this:
  1. transcript_bytes (primary) — if the JSONL file is growing the agent
     is issuing tool calls. Silence here is a real stall indicator.
  2. progress_log mtime (secondary) — explicit writes by the agent.

An agent is STALLED only when BOTH are flatlined AND the pid is still
alive (so it did not exit normally).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .heartbeat_idle import find_transcript

STALL_THRESHOLD_SECONDS = 90


@dataclass
class WatchdogState:
    """A single liveness snapshot."""

    taken_at: float
    transcript_bytes: Optional[int]  # None when transcript not found
    log_mtime: Optional[float]       # None when no log path or file missing
    pid_alive: Optional[bool]        # None when no pid provided


@dataclass
class AgentWatchdog:
    """
    Accumulates liveness snapshots and reports when an agent is stalled.

    Usage (parent side, alongside Monitor)::

        wd = AgentWatchdog(
            agent_name="port-release-notes-ux-bundle-to-9962ff",
            progress_log_path=Path("/tmp/release-notes-port.log"),
            pid=43690,
            stall_threshold_seconds=90,
        )
        # call snapshot() every ~30s
        wd.snapshot()
        # ... after each tick ...
        if wd.is_stalled():
            # cancel and report

    The watchdog does NOT cancel the agent itself — that stays with the
    caller so it can log and notify before acting.
    """

    agent_name: str
    progress_log_path: Optional[Path] = None
    pid: Optional[int] = None
    stall_threshold_seconds: int = STALL_THRESHOLD_SECONDS
    repo_root: Optional[Path] = None
    _states: list = field(default_factory=list, repr=False)

    def snapshot(self) -> WatchdogState:
        """Record the current liveness state and return it."""
        transcript = find_transcript(self.agent_name, repo_root=self.repo_root)
        transcript_bytes: Optional[int] = None
        if transcript is not None:
            try:
                transcript_bytes = transcript.stat().st_size
            except OSError:
                pass

        log_mtime: Optional[float] = None
        if self.progress_log_path is not None:
            try:
                log_mtime = self.progress_log_path.stat().st_mtime
            except OSError:
                pass

        pid_alive: Optional[bool] = None
        if self.pid is not None:
            try:
                os.kill(self.pid, 0)
                pid_alive = True
            except (ProcessLookupError, OSError):
                pid_alive = False

        state = WatchdogState(
            taken_at=time.time(),
            transcript_bytes=transcript_bytes,
            log_mtime=log_mtime,
            pid_alive=pid_alive,
        )
        self._states.append(state)
        return state

    def is_stalled(self) -> bool:
        """
        Return True only when the agent is confirmed stalled:
          - pid is alive (agent did not exit)
          - transcript bytes have not grown since the first snapshot
          - progress log has not been updated since the first snapshot
            (only checked when a log path was provided)
          - at least stall_threshold_seconds have elapsed since snapshot[0]

        If transcript bytes are advancing, the agent is alive — log
        silence is acceptable (conflict resolution, heavy reads, etc.).
        If we have fewer than 2 snapshots we cannot determine stall.
        """
        if len(self._states) < 2:
            return False

        first = self._states[0]
        latest = self._states[-1]
        elapsed = latest.taken_at - first.taken_at
        if elapsed < self.stall_threshold_seconds:
            return False

        # pid gone → agent finished or crashed, not a stall
        if latest.pid_alive is False:
            return False

        # transcript advancing → agent is issuing tool calls, not stalled
        if (
            first.transcript_bytes is not None
            and latest.transcript_bytes is not None
            and latest.transcript_bytes > first.transcript_bytes
        ):
            return False

        # progress log advancing → agent is writing, not stalled
        if (
            self.progress_log_path is not None
            and first.log_mtime is not None
            and latest.log_mtime is not None
            and latest.log_mtime > first.log_mtime
        ):
            return False

        return True

    def last_state(self) -> Optional[WatchdogState]:
        return self._states[-1] if self._states else None
