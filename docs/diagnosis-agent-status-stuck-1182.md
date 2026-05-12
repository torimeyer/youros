# Diagnosis: Agent status stays 'running' after completed_at is set (→1182)

## Root cause

When an agent is spawned via the API (`POST /agents/spawn`), its asyncio subprocess handle is stored in `active_agents[name]`. The `GET /agents` endpoint (step 2a, ~line 3131 of `api/routers/agents.py`) iterates `active_agents` and checks `proc.returncode` to determine if the process has exited.

If the process has exited but asyncio's child watcher has not yet called `proc.wait()` to reap it, `proc.returncode` remains `None`. The listing code then falls into the `else` branch and derives `effective_status` from a set called `_TERMINAL_FROM_META`:

```python
_TERMINAL_FROM_META = {
    "cancelled", "failed", "terminated_stale",
    "killed", "stopped", "abandoned",
    "completed_timeout",
    # "completed" was MISSING here — this is the bug
}
persisted = meta.get("status", "")
effective_status = persisted if persisted in _TERMINAL_FROM_META else "running"
agents_map[name] = {
    "name": name,
    "source": "api",
    **meta,             # spreads completed_at from metadata
    "status": effective_status,  # overrides with "running"
}
```

The `/complete` endpoint correctly sets `meta["status"] = "completed"` and `meta["completed_at"] = now` together and persists both to disk. However, because `"completed"` was absent from `_TERMINAL_FROM_META`, the next `GET /agents` call that still found the proc handle in `active_agents` (with `returncode=None`) would override the persisted status back to `"running"` while leaving `completed_at` in place from the `**meta` spread.

This produced the impossible state in the API response: `status="running"` with `completed_at` set. The standing-rules hook in `.claude/hooks/standing-rules.sh` filters CURRENT RUNNING AGENTS by `status="running"`, so the agent stayed in the live list. No stop-notification was fired, and the parent session never ran `git log` to surface what landed.

## Writer that set completed_at correctly

`mark_agent_complete` (line 6628-6644 of `api/routers/agents.py`) sets both fields atomically:

```python
agent_metadata[name]["status"] = "completed"
agent_metadata[name]["completed_at"] = completed_at
```

The write is correct. The bug is in the reader (the listing path) which overwrote the correct status with "running" when the proc handle was not yet reaped.

## Fix

Added `"completed"` to `_TERMINAL_FROM_META` (line 3164-3175 of `api/routers/agents.py`). When `meta["status"] == "completed"`, the listing path now respects the persisted status instead of falling through to `"running"`.

## Regression test

`api/tests/test_agent_status.py::test_list_shows_completed_not_running_when_proc_not_reaped`

### Failing output (before fix)

```
FAILED tests/test_agent_status.py::test_list_shows_completed_not_running_when_proc_not_reaped
AssertionError: Agent with meta status='completed' and completed_at set must show as
'completed' even when proc.returncode is None, got 'running'. Root cause: 'completed'
missing from _TERMINAL_FROM_META (→1182).
```

### Passing output (after fix)

```
1 passed in 0.10s
```
