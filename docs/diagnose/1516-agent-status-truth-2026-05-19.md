# →1516 Agent Status Truth at the Source

**Date**: 2026-05-19  
**Branch**: worktree-agent-build-1516-api-status-03251f42  
**Status**: FIXED (belt-and-suspenders via agent_reaper; primary fix blocked on →1505)

---

## Problem

`/api/agents` returns `status=running` for agents whose subprocess has already exited.
Today: 3 agents listed as running while `ps -ef | grep claude-code` returned no
matching processes. The fleet-active gate counts these zombie rows and blocks git ops.

---

## Exit-Path Audit

When an agent is spawned via `POST /api/agents/spawn`, the lifecycle looks like:

```
spawn_agent() in api/routers/agents.py
  └── asyncio.create_subprocess_exec(...) → proc
  └── asyncio.create_task(_drain_stderr(proc, name, ...))   # exit hook lives here
  └── asyncio.create_task(_drain_stdout(proc, name, ...))
  └── agent_metadata[name] = {"status": "running", "pid": proc.pid, ...}
```

The `_drain_stderr` task (lines ~5136-5266 of agents.py) is the exit hook. It:
1. Drains stderr to a log file.
2. Reads `proc.returncode` after EOF.
3. If `rc not in (None, 0)` → calls `_set_agent_status(name, "failed")` ✓
4. If `rc == 0` AND `_is_roadmap_agent(name, template)` → marks `completed` ✓

**The gap**: there is NO handler for `rc == 0` on non-roadmap agents (step 4 is
roadmap-only). These agents are expected to call `POST /api/agents/{name}/complete`
before exiting. If they don't (crash during cleanup, process killed before the HTTP
call completes, server restart), the row stays `status=running` forever.

---

## Root Cause

**Gap in `_drain_stderr`** (api/routers/agents.py, ~line 5186):

```python
# existing code
if rc not in (None, 0):
    _fail_meta = agent_metadata.get(name)
    if _fail_meta and _fail_meta.get("status") == "running":
        _set_agent_status(name, "failed", ...)   # ← handles rc != 0

if rc == 0 and _is_roadmap_agent(name, template):
    ...
    _m["status"] = "completed"                   # ← handles rc=0 roadmap only

# ← NO HANDLER for rc=0 non-roadmap agents
```

**Secondary gap in `find_stuck_agents`** (api/lib/agent_reaper.py):
The liveness supervisor requires `transcript_bytes < 1024` to flag a dead-PID agent.
Agents with real output (>1KB transcript) who died without calling `/complete` were
never caught by the reaper either.

---

## Fix

### Primary fix — BLOCKED on →1505 (agents.py on avoid list)

Add ~4 lines to `_drain_stderr` in `api/routers/agents.py` after the roadmap block:

```python
elif rc == 0:
    _m = agent_metadata.get(name)
    if _m and _m.get("status") == "running":
        _m["status"] = "completed"
        _m["completed_at"] = datetime.now(timezone.utc).isoformat()
        _fire_delta(name, "completed")
        await _save_agent_state_async()
        logger.info("spawn.marked_completed name=%s", name)
```

This makes status transitions **immediate** (within the drain task, milliseconds after
process exit). Zero dependency on any reaper or polling mechanism.

### Belt-and-suspenders fix — SHIPPED in this PR

Added `find_zombie_agents()` to `api/lib/agent_reaper.py`:

- Scans all `status=running` agents with known PIDs.
- If PID is dead AND transcript >= 1024 bytes → marks `completed` within the next
  sweep interval (≤30 s).
- Complements `find_stuck_agents` (dead PID + empty transcript → `failed`).
- Wired into `_do_sweep_sync` alongside existing victim/stalled logic.

This handles the most common zombie class: productive agents (>1KB output) that
exited cleanly without calling `/complete`.

---

## Retry Path Audit

When an agent is re-spawned under the same name, `agent_metadata[body.name] = spawn_meta`
(line ~5632) replaces the old row entirely. The new spawn gets `status=running` with
the new PID. The old drain task continues running — when it finishes it reads
`agent_metadata.get(name)` which now points to the NEW spawn's metadata. If the old
drain task's rc was 0, it does nothing (the gap above). The new spawn's metadata is
unaffected. This is a latent but low-impact issue (the reaper eventually catches the
old drain's ghost).

## Cancel Path Audit

`POST /api/agents/{name}/cancel` calls `_set_agent_status(name, "cancelled")` and
terminates the subprocess via SIGTERM+SIGKILL. This path is correct ✓.

## Server-Restart Orphan Audit

On restart, `active_agents` (the name→proc dict) is empty. Any old `status=running`
row with a dead PID is an orphan. The liveness supervisor (`_do_sweep_sync`) handles
these within 30 s via `find_zombie_agents` (if transcript ≥ 1KB) or `find_stuck_agents`
after 180 s (if transcript < 1KB).

---

## Test Results

File: `api/tests/test_agent_lifecycle.py`

```
...........
11 passed in 0.14s
```

Tests cover:
- Dead PID with real transcript → `find_zombie_agents` detects it (rc=0 case)
- Alive PID → not detected
- Empty transcript → not zombie territory (goes to `find_stuck_agents`)
- Terminal statuses (completed/failed/cancelled/stalled) → not re-detected
- No `pid` field → skipped safely
- Dead PID + empty transcript + stale heartbeat → `find_stuck_agents` detects (rc!=0 case)
- Dead PID + fresh heartbeat → not yet past threshold
- Cancel transition → status=cancelled
- SIGKILL + real transcript → `find_zombie_agents` catches it
- SIGKILL + empty transcript → `find_stuck_agents` catches it

---

## Files Changed

| File | Change |
|------|--------|
| `api/lib/agent_reaper.py` | Added `find_zombie_agents()`, wired into `_do_sweep_sync` |
| `api/tests/test_agent_lifecycle.py` | New — 11 lifecycle tests |
| `docs/diagnose/1516-agent-status-truth-2026-05-19.md` | This file |

---

## Follow-up Required

After →1505 folds in and `api/routers/agents.py` is no longer hot, add the 4-line
primary fix to `_drain_stderr`. This makes rc=0 → `completed` **immediate** instead
of waiting up to 30 s for the reaper sweep.
