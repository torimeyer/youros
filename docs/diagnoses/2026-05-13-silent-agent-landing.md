# Silent Agent Landing — 2026-05-13

**Incident:** `audit-8-origin-wip-branches-231d7b` finished its report at 20:54:28 UTC. Parent claude
was not told until user asked "done?" ~5 minutes later.

## Root cause

**Parent-behavior bug, not a backend bug.** When the Monitor stream ended cleanly (agent touched
`/tmp/wip-audit-done`), the parent had the `feedback_report_agent_landing_immediately` rule loaded
but applied the wrong check: it ran `git log`, found no commits (read-only audit agent — nothing to
commit), concluded it couldn't confirm, and deferred to "the next heartbeat." No next heartbeat
arrived until the user asked, because background Agent-tool completion notifications only surface on
the next user turn.

## Why git log alone failed here

`feedback_report_agent_landing_immediately` says "run git log when monitor dies." Correct for
commit-producing agents. A read-only audit agent's entire deliverable is the transcript file — git
log is empty. The parent saw nothing in git log, concluded "can't confirm," and waited for the
Agent-tool `<status>completed</status>` notification. That notification queued and only arrived when
the user sent "done?" — triggering the next turn.

## Backend status

No backend bug. The auto-complete loop (`_autocomplete_exited_subagents`, 60s interval, path A:
transcript-idle) correctly transitioned the row to `completed` within ~2 minutes of the transcript
going quiet. It only updates the UI row — it does not signal the parent claude. That is expected.

## Fix applied

New memory rule: `feedback_monitor_end_read_transcript_directly.md`

**Rule:** when Monitor stream ends OR transcript file grew since last check → read the transcript
file directly this turn, surface its content. Do NOT wait for Agent-tool completion notification.
The sequence: (1) curl /api/agents for status, (2) read transcript at known path, (3) surface key
findings. Git log is step 3 fallback only, not primary.

Added to MEMORY.md standing-rule triggers and INDEX.
