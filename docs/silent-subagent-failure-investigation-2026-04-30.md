# Silent Subagent Failure Investigation - 2026-04-30

## Summary

Subagents spawned today via `task-isolation-bridge.sh` registered and received a subprocess but produced 0-byte transcripts, then became invisible in `/api/agents`. The core issue has two interacting parts. First, when a subprocess exits without calling `/complete`, `agent_metadata` keeps `status="running"` indefinitely. The lock sweep at `api/routers/agents.py:1735` explicitly skips agents with that status, so path locks stay held until the stale-agent sweep promotes the status to `terminated_stale` (which only happens on `GET /api/agents` calls, after a 15-minute threshold). Second, the orphaned diagnose agent (`diagnose-silent-subagent-failure-c76466`) was NOTFOUND at `/api/agents/...` but its locks were still held -- indicating the backend was running and the subprocess had not yet exited (keeping `_drain_stderr` blocked on `p.stderr.read(4096)`). The stale sweep cannot fire without an incoming HTTP request, and if no one polls `/api/agents` for 15 minutes the agent sits "running" with locks held.

---

## A. Auth Gate Result

`claude /status` is not available in the worktree environment. Authentication is confirmed by the successful POST to `/api/agents/register` (returned HTTP 200 with mailbox instructions) and successful heartbeat calls throughout this session. The source token in this session is `ANTHROPIC_API_KEY` (API key auth, not a Max subscription). Quota-cap silent failures (step 4 in the diagnosis checklist) do NOT apply here -- those only affect Max subscription accounts.

---

## B. Transcript Comparison

**Byte counts (verified via `wc -c`):**

| Agent | Status in task | Bytes | Stderr log |
|---|---|---|---|
| `diagnose-silent-subagent-failure-c76466` | Failing | 0 | 0 |
| `atlassian-wave-3a-retry-2-way-ba-517a87` | Successful | 0 | 0 |
| `atlassian-wave-3b-2-way-frontend-b40561` | Failing | 2415 | 0 |

**Divergence point:** Both the truly failing diagnose agent and the "successful" wave-3a-retry have 0-byte transcripts. The wave-3a-retry was listed as successful because its work landed in commits, not because its transcript shows output. This means the transcript byte count alone is not a reliable success/failure signal -- a working agent can leave a 0-byte transcript if it completed quickly via a worktree commit path.

**First 15 lines of `diagnose-silent-subagent-failure-c76466.md`:**
```
(empty -- 0 bytes)
```

**First 15 lines of `atlassian-wave-3a-retry-2-way-ba-517a87.md`:**
```
(empty -- 0 bytes)
```

**First 15 lines of `atlassian-wave-3b-2-way-frontend-b40561.md` (the hallucinated stub case):**
```
All four verification checks passed. Here's the mandatory final report:

---

## Wave 3b Verification

**1. `git diff HEAD --stat` (4 files changed):**
```
```

The wave-3b transcript is 2415 bytes of detailed verification text. It was written at `15:47` (13 minutes AFTER the agent spawned at `15:21`). The stderr log is 0 bytes. This is consistent with the agent running in its worktree and writing its final report directly to the transcript file -- then the `_drain_stdout` task flushed it. But the parent classified it as "failing" because it saw a lock conflict at spawn time (409) and spawned the retry. The original ran concurrently.

---

## C. Hypotheses, Ranked

### Hypothesis 1 (Most Likely): Subprocess alive, drain blocked, sweep skips "running"

**What would cause the symptom:** The subprocess spawned at `/api/agents/spawn` starts (no immediate crash) but produces zero stdout/stderr. `_drain_stderr` and `_drain_stdout` are both blocking on `p.stderr.read(4096)` and `p.stdout.read(4096)`. The subprocess is alive but idle -- possibly stuck at authentication, model initialization, or waiting for stdin data it will never get.

**Suspect:** `api/routers/agents.py:3922` (subprocess fork) + `api/routers/agents.py:1735-1736` (sweep skip).

The sweep at line 1735 does:
```python
if status in ("running", "pending", "spawned"):
    continue
```
The agent's metadata shows `status="running"` (set at spawn, never updated). The sweep runs every 300 seconds but never releases the lock because the agent looks live.

**Lock stays held until:** Either the process exits (drain fires at 4020), the backend restarts (in-memory dict cleared), or 15 minutes pass and the `terminated_stale` sweep promotes the status (which then lets the NEXT lock sweep release it on its next 300s cycle).

**Experiment:** Add a log line to `_sweep_stale_locks_once` printing every skip reason. Also add `os.kill(pid, 0)` before the `status=="running"` skip to distinguish "process alive" from "process dead but metadata stale."

### Hypothesis 2 (Likely secondary): `_drain_stderr` never scheduled because task creation raises

**What would cause the symptom:** The `asyncio.create_task(_drain_stderr(...))` at line 4103 raises an exception (event loop in a bad state, closed, or overloaded). The belt-and-suspenders at 4113-4116 does release locks if this happens, but only if the `create_task` itself raises -- not if the created task is later cancelled by event loop shutdown.

**Suspect:** `api/routers/agents.py:4103-4116`. If the event loop is shutting down when `create_task` is called, the task may be silently dropped without raising, and the lock release at 4113 never fires.

**Experiment:** Add `asyncio.get_event_loop().call_soon(lambda: None)` before `create_task` to detect a closed loop.

### Hypothesis 3: `meta is None` never reached -- deleted agents stay in `agent_metadata`

**What would cause the symptom:** The `/api/agents/{name}` endpoint returns 404 because the agent is in `deleted_agents.json`, but `agent_metadata.get(spawn_id)` still returns the in-memory dict entry (which shows `status="running"`). The sweep checks `agent_metadata`, not the filtered view, so it hits the `status=="running"` early-continue before ever reaching the `meta is None` branch.

**Suspect:** `api/routers/agents.py:1732-1736`. The `meta is None` path at line 1741 is unreachable for any agent that was ever persisted and loaded at startup, even if it appears NOTFOUND to the API.

**Experiment:** Call `agent_metadata.get("diagnose-silent-subagent-failure-c76466")` from a debug endpoint and check whether it returns a dict with `status="running"`.

### Hypothesis 4: Worktree creation failure bypasses lock release

**What would cause the symptom:** Worktree creation fails (git error, existing branch conflict), raises `HTTPException` at line 3869, caught by the outer handler at 4257 which calls `_release_spawn_locks`. BUT: the 0-byte stderr log and 0-byte transcript together indicate the subprocess never started, which is consistent with a worktree failure. If `_release_spawn_locks` in the HTTPException handler itself raised (caught at 4263), the lock would leak.

**Suspect:** `api/routers/agents.py:4257-4264`.

**Experiment:** Check whether `diagnose-silent-subagent-failure-c76466` has a `worktree_path` in its persisted metadata. If not, the worktree was never created, pointing to a fork failure.

---

## D. Recommended Next Step

Add a single log line to `_sweep_stale_locks_once` at line 1735:

```python
if status in ("running", "pending", "spawned"):
    logger.info(
        "lock_sweep.skip_live name=%s key=%s age=%ds pid=%s",
        spawn_id, key, int(age),
        (agent_metadata.get(spawn_id) or {}).get("pid"),
    )
    continue
```

Then add the companion check:

```python
_pid = (agent_metadata.get(spawn_id) or {}).get("pid")
if _pid:
    try:
        os.kill(int(_pid), 0)
    except (ProcessLookupError, OSError):
        logger.warning(
            "lock_sweep.dead_proc_held name=%s key=%s age=%ds",
            spawn_id, key, int(age),
        )
        should_release = True
```

This would immediately show whether the 664s hold is "process still alive" (expected, drain will fire when it exits) vs "process dead, metadata stale" (the actual leak scenario requiring a fix).

---

## E. Orphaned Lock Specific Finding

**Why age 664s is still held despite two sweep cycles:**

The sweep loop in `_spawn_lock_sweep_loop` (line 1763-1770) starts with a 30s delay, then runs every `MYOS_LOCK_SWEEP_INTERVAL_S = 300` seconds (env-configurable, defaults to 300). At 664 seconds, the sweep has run exactly twice (at ~30s and ~330s) and is between its second and third fire (~630s).

Each time it ran, it found `agent_metadata.get("diagnose-silent-subagent-failure-c76466")` returning a dict with `status="running"` and hit the early continue at line 1735. This is intentional design: the sweep skips live agents so it does not release locks for agents actively writing files.

**The gap is:** there is no fast-path in the lock sweep for dead processes. A process can be dead with `status="running"` in metadata for up to 15 minutes (the `STALE_AGENT_TIMEOUT_SECONDS` threshold). During that window, every sweep cycle skips the lock. The lock stays held through two or more sweep cycles.

**`schedule_spawn_lock_sweep` is called correctly:** `api/main.py:40` calls `await agents.schedule_spawn_lock_sweep()` inside the lifespan context. Confirmed at grep line `/Users/torimeyer/claude/torios/api/main.py:40`. The sweep loop IS registered. The problem is not a missing registration -- it is the 15-minute gap between subprocess death and metadata status update.

**The `meta is None` branch never fires in practice** because `agent_metadata` is loaded from disk at startup and the agent is already in it. Deleted agents (filtered from the API view via `deleted_agents.json`) are NOT removed from `agent_metadata`, so `agent_metadata.get(spawn_id)` returns the dict and the "missing agent" path is never taken. The sweep's `meta is None` guard only applies to a lock entry whose `spawn_id` was never persisted -- which in practice means a lock acquired but the backend restarted before `_save_agent_state()` ran.

---

## Mandatory Final Report Sections

### A. `claude /status` output

`claude /status` is not available in this worktree environment (returns "not available in this environment"). Auth confirmed via successful API calls to `https://127.0.0.1:8000`. This session uses `ANTHROPIC_API_KEY` (api key auth, not Max subscription -- quota-cap hypothesis ruled out per `feedback_zero_byte_transcript_diagnose_order.md` step 4 prerequisite).

### B. First 15 lines of failing diagnose transcript (`diagnose-silent-subagent-failure-c76466.md`)

```
(file is 0 bytes -- no content)
```

Verified: `wc -c` = 0. Timestamp: Apr 30 15:34.

### C. First 15 lines of working wave-3a-retry transcript (`atlassian-wave-3a-retry-2-way-ba-517a87.md`)

```
(file is 0 bytes -- no content)
```

Verified: `wc -c` = 0. Timestamp: Apr 30 15:16. This agent succeeded (commits landed) despite a 0-byte transcript -- success is not detectable from transcript bytes alone.

### D. `wc -l` of this document

189 lines.

### E. `grep -n "schedule_spawn_lock_sweep|_spawn_lock_sweep_loop" api/routers/agents.py api/main.py` output

```
api/routers/agents.py:1763:async def _spawn_lock_sweep_loop() -> None:
api/routers/agents.py:1767:            _sweep_stale_locks_once()
api/routers/agents.py:1773:async def schedule_spawn_lock_sweep() -> None:
api/routers/agents.py:1774:    asyncio.create_task(_spawn_lock_sweep_loop())
api/main.py:22:async def lifespan(app: FastAPI):
api/main.py:40:    await agents.schedule_spawn_lock_sweep()
api/main.py:51:app = FastAPI(title="myOS API", lifespan=lifespan)
```

The sweep IS registered. The gap is in the lock-release logic, not the schedule registration.

---

*Written by: diagnose-silent-failures-writeup-b48c51 agent, 2026-04-30*
