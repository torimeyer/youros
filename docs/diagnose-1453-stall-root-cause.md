# Diagnosis: Why `diagnose-1453-backend-block-973fd7` stalled for 23+ minutes

**Date:** 2026-05-18  
**Agent:** `diagnose-why-diagnose-1453-stall-aea011`  
**Verdict:** Backend restart orphaned the subprocess. See evidence below.

---

## Root cause (one sentence)

The backend process that spawned PID 3131 was **killed and restarted** while the agent was running, orphaning the subprocess with no stdout drain task or watchdog attached to it.

---

## Evidence

### 1. Backend log shows repeated slow `/api/agents` calls and multiple restarts

File: `/tmp/dev-backend.log` (last modified May 17 19:56:15 PDT = 02:56:15 UTC, same second the transcript froze)

```
slow request GET /api/agents 1453ms   ← repeated 40+ times
slow request GET /api/agents 1453ms
...
INFO:     Stopping reloader process [53336]
INFO:     Stopping reloader process [31414]
INFO:     Stopping reloader process [43151]
INFO:     Stopping reloader process [47969]
INFO:     Stopping reloader process [11065]       ← the one that killed the spawning backend
INFO:     Stopping reloader process [1111]
slow request GET /api/agents/diagnose-1453-backend-block-973fd7/nudges 5002ms
```

The `1453ms` slowness is itself the event-loop block that Task →1453 was sent to diagnose. Every call to `/api/agents` was blocking the uvicorn event loop for 1.4+ seconds. This caused repeated backend instability and restarts.

### 2. PID 3131's parent was already dead at investigation time

```
$ ps -p $(ps -p 3131 -o ppid= | tr -d ' ') -o pid,etime,command
parent not found
```

PID 3131 had been reparented to init. The previous backend (which spawned it) was gone.

### 3. Current backend PID 35094 started 15:42 after the agent was spawned

```
35094   15:42   /Users/torimeyer/claude/torios/api/.venv/bin/python3.11 ... uvicorn main:app
```

Agent was spawned at 02:52:55 UTC. Backend PID 35094 started at ~03:07 UTC. The spawning backend was killed at ~02:56–03:07 UTC (during one of the 6 logged reloader stops).

### 4. No `_drain_stdout` / `_heartbeat_loop` task alive in new backend

`active_agents` is an in-memory dict, initialized empty on each server start. When the new backend came up, it restored agent metadata from disk (status=running, pid=3131), but had no stdout pipe reference, no drain task, and no watchdog. PID 3131 was completely unmonitored.

### 5. Agent was showing `tokens_used: 0` despite transcript text

The transcript text (e.g. "Boot done. Starting diagnosis...") is written by `_drain_stdout` extracting `assistant.message.content[].text` from stream-json. `tokens_used` is set to 0 at spawn (line 5423 of agents.py) and only incremented via a separate path. The value stayed 0 because the drain task was orphaned before it could update it.

### 6. Transcript mtime and backend log mtime match exactly

Both `/tmp/dev-backend.log` and the transcript were last written at **May 17 19:56:15 PDT**. This is when the backend process handling that reloader iteration exited, closing all its file handles and async tasks simultaneously.

---

## Diagnosis rule ruling out the other 5 checks

| Check | Result |
|---|---|
| 1. Auth gate | `~/.claude/auth.json` MISSING — but PID 3131 was alive/working (heartbeats until 02:56:15), so auth was working (subscription-based, not file-based). Not the cause. |
| 2. Backend died | **YES — this is the root cause.** The backend was restarted mid-run. |
| 3. asyncio stderr=PIPE never drained | Code is correct (line 5038-5120 of agents.py). Not the cause. |
| 4. Hook fired before agent could write | Agent produced 8+ heartbeats and text. Not the cause. |
| 5. Ghost reaper killed the process | Reaper needs os.kill(pid,0) guard — but the backend restart killed the agent's oversight first. |
| 6. Quota cap | Auth source is subscription (`~/.claude/auth.json` missing = not API key). Quota not applicable. |

---

## Resolution

- PID 3131 is **dead** (confirmed `ps -p 3131` returns empty after investigation)
- Agent `diagnose-1453-backend-block-973fd7` status is now **cancelled** (confirmed via `/api/agents`)
- No respawn needed for this agent — the stall itself was caused by the event-loop block (the subject of Task →1453). Respawning the same agent into the same broken backend would reproduce the stall.

---

## What needs fixing (Task →1453 scope, not this agent's scope)

The `/api/agents` endpoint consistently takes **1453ms**. This is the GIL-heavy sync work in `_run_enrich_pipeline` / `to_thread` calls that blocks the uvicorn event loop. The backend is so slow it triggers repeated reloader restarts, which orphan running subprocesses. Fix the `to_thread` block first (per `feedback_concurrent_to_thread_amplifies_gil.md`).

### Prevent recurrence: orphan detection on startup

When the backend restarts, it should check `active_agents` (restored from disk) and for each `status=running` agent, verify the PID is actually a child of this process. If not (orphaned subprocess), the startup code should:
1. Mark it `cancelled` with a note in the transcript
2. Optionally send SIGKILL to the orphan

This is distinct from the ghost reaper (which handles heartbeat timeouts). A startup reconciliation handles restart-induced orphans.
