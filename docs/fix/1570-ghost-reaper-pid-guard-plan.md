# →1570 Ghost Reaper Kills Live Agents — Diagnosis and Fix Plan

## Root Cause (confirmed 2026-05-21)

**File**: `api/routers/agents.py:5473`  
**Function**: `_drain_stdout._heartbeat_loop`

The `_drain_stdout` coroutine runs a `_heartbeat_loop` watchdog task that monitors stdout silence. Three thresholds:

| Phase | Condition | Limit |
|-------|-----------|-------|
| 1 | No bytes at all | 45s (`_STDOUT_FIRST_BYTE_LIMIT_SECONDS`) |
| 2 | Hook events but no model output | 120s (`_STDOUT_API_HANG_LIMIT_SECONDS`) |
| 3 | Had model output, now silent | 300s (`_STDOUT_SILENCE_LIMIT_SECONDS`) |

When a Claude agent issues a `tool_use` (e.g., `mcp__ostk__bash` for pytest), it emits the `tool_use` stream-json event (resets `_last_model_output_at`), then **blocks in silence** waiting for the tool result. For a pytest run of 5600 tests (~5-10 min), this silence exceeds 300s.

The watchdog fires: `os.killpg(pgid, SIGKILL)` — entire process group killed, transcript gets the "subprocess silent for Xs (mid-stream) - killing wedged process" message.

**Evidence**: `transcripts/diagnose-pytest-pollution-1569-d1e6bf.md` ends with:
```
Agent 'diagnose-pytest-pollution-1569-d1e6bf' subprocess silent for 317s (mid-stream) - killing wedged process.
```

The ghost_reaper's `reap_ghost_agents` (with its PID guard) was NOT the killer in this incident.

## Fix

### Primary: Suspend silence watchdog during active tool calls

Track open tool calls in `_drain_stdout`:
- Increment `_open_tool_calls` when processing `assistant.content[].tool_use` blocks
- Decrement when a top-level `tool_result` event arrives
- In `_heartbeat_loop`: skip the kill if `_open_tool_calls[0] > 0`

This is a targeted fix: the watchdog correctly identifies silence as legitimate "waiting for tool result" vs "wedged subprocess".

### Defense-in-depth: Child process check

Before killing, also check if `p.pid` has active children via `pgrep -P <pid>`. If children exist, the subprocess is doing real work (pytest, bash subprocess, etc.).

## Acceptance Criteria

- [ ] `_open_tool_calls` counter tracks tool_use/tool_result balance
- [ ] `_heartbeat_loop` skips kill when `_open_tool_calls[0] > 0`
- [ ] Child-process guard as fallback (secondary defense)
- [ ] Regression test: spawn agent doing `sleep 60` as a tool call, verify it survives the 300s watchdog
- [ ] All existing tests pass
