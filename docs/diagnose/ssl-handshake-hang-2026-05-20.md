# SSL Handshake Hang — Diagnose Report
**Date:** 2026-05-20  
**Needle:** →1526  
**Commit:** see below

---

## Symptom

Uvicorn HTTPS backend on `127.0.0.1:8000` intermittently hangs at the TLS handshake.  
Curl output before the fix:

```
* TLSv1.3 (OUT), TLS handshake, Client hello (1):
* SSL connection timeout
* Closing connection.
curl: (28) SSL connection timeout
```

Sometimes the handshake completes but the response body is empty. Restarting via `scripts/restart-backend.sh` recovers it briefly, then it hangs again after a few minutes of agent activity.

---

## Reproduction

1. Start the backend: `scripts/dev-backend.sh`
2. Create several workflow agents (POST `/api/workflows` and spawn agents with `workflow_run_id`)
3. Hit the backend repeatedly while the snapshot loop runs:

```bash
for i in $(seq 1 20); do curl --connect-timeout 3 -m 5 -sSk https://127.0.0.1:8000/api/health; done
```

Hang is more reliable with 5+ running workflow agents and a large `~/.myos/.ostk/audit.jsonl`.

---

## Root Cause

**Single-worker uvicorn** (`--workers 1`) means the entire TLS+HTTP stack shares one asyncio event loop. When that loop is blocked on synchronous work, no new connections can be accepted and in-flight TLS handshakes time out.

The 500ms background snapshot loop (`_agents_snapshot_loop` → `_compute_agents_snapshot_async`) called **two synchronous functions directly on the event loop**:

### Finding 1 — `_reconcile_workflow_step_agents()` — N file reads per 500ms

**File:** `api/routers/agents.py:2163`  
**Caller:** `api/routers/agents.py:3913`

```python
# Before fix — called sync, no to_thread
wf_changed = _reconcile_workflow_step_agents()
```

Inside `_reconcile_workflow_step_agents()`, for every running agent with a `workflow_run_id`, it called:

```python
wf = get_workflow(wf_run_id)   # api/routers/agents.py:2196 (before fix)
```

`get_workflow()` calls `_load()` in `api/services/workflows.py:156`:

```python
def get_workflow(workflow_id: str) -> Optional[dict]:
    return _load().get(workflow_id)   # full file read every call
```

`_load()` at `api/services/workflows.py:61`:

```python
return json.loads(WORKFLOWS_FILE.read_text())   # sync file read
```

**Effect:** With N running workflow agents, this was N synchronous `workflows.json` reads directly on the event loop per 500ms tick. Each read blocks all other async operations including TLS frame processing.

### Finding 2 — `_recover_bulk_cancelled_agents()` — sync audit file I/O

**File:** `api/routers/agents.py:2112`  
**Caller:** `api/routers/agents.py:3902`

```python
# Before fix — called sync, no to_thread
rc_changed = _recover_bulk_cancelled_agents()
```

When it found a bulk-cancel regression, it called `_emit_audit_event()` at `api/routers/agents.py:912`. That function reads up to 32KB from `audit.jsonl` for dedup, then appends a new entry — both synchronous file operations on the event loop.

### Finding 3 — `_sweep_stale_running_agents()` in reconcile loop

**Caller:** `api/routers/agents.py:8061`

```python
# Before fix — called sync in 30s reconcile loop
stale_changed = _sweep_stale_running_agents()
```

`_sweep_stale_running_agents()` calls `_transcript_is_stub()` and `_transcript_has_real_content()`, which open and read transcript files synchronously. Less frequent (30s loop vs 500ms) but still blocks the loop for the duration.

### Why it worsens over time then recovers on restart

- At restart, `agent_metadata` is pruned (stale rows removed) — fewer workflow agents to iterate
- `audit.jsonl` is smaller just after restart — dedup read is faster  
- As new agents register and workflows run, `agent_metadata` grows and `audit.jsonl` grows — blocking duration increases until the next restart

---

## Why Other Hypotheses Are Ruled Out

| Hypothesis | Verdict | Reason |
|---|---|---|
| TLS keyfile/cert issue | No | Handshake sometimes completes; problem is load-dependent |
| FD exhaustion | No | Restart recovers immediately; FD leaks accumulate permanently |
| Double-bind via SO_REUSEPORT | No | dev-backend.sh has an explicit kill-and-replace guard for this |
| Heavy enrich pipeline blocking | No | `_run_enrich_pipeline` already wrapped in `asyncio.to_thread` with `_enrich_async_lock` |
| Uvicorn worker count | No | `--workers 1` is correct for asyncio; adding workers adds state-sharing complexity without fixing the loop-blocking root cause |

---

## Fix

**File:** `api/routers/agents.py`

### Change 1 — `_reconcile_workflow_step_agents` accepts pre-loaded dict (line ~2163)

```python
# Before
def _reconcile_workflow_step_agents() -> bool:
    from services.workflows import get_workflow, WF_DONE, WF_FAILED
    ...
    wf = get_workflow(wf_run_id)  # file read per agent

# After
def _reconcile_workflow_step_agents(all_workflows: dict | None = None) -> bool:
    from services.workflows import WF_DONE, WF_FAILED
    if all_workflows is None:
        from services.workflows import _load as _load_wf
        all_workflows = _load_wf()  # single load when called standalone
    ...
    wf = all_workflows.get(wf_run_id)  # O(1) dict lookup, no file I/O
```

### Change 2 — Snapshot loop offloads both functions and pre-loads workflows (line ~3902)

```python
# Before
rc_changed = _recover_bulk_cancelled_agents()
wf_changed = _reconcile_workflow_step_agents()

# After
rc_changed = await asyncio.to_thread(_recover_bulk_cancelled_agents)
from services.workflows import _load as _load_wf_sync
all_wf = await loop.run_in_executor(None, _load_wf_sync)
wf_changed = await asyncio.to_thread(_reconcile_workflow_step_agents, all_wf)
```

### Change 3 — Reconcile loop offloads stale sweep (line ~8061)

```python
# Before
stale_changed = _sweep_stale_running_agents()

# After
stale_changed = await asyncio.to_thread(_sweep_stale_running_agents)
```

**Why this is safe:**
- `asyncio.to_thread` releases the event loop entirely; the thread pool executes the sync work
- `_fire_delta()` called from threads already handles `RuntimeError: no running loop` with a silent pass; the snapshot loop pushes the correct state within the next 500ms anyway
- Python GIL makes dict reads/writes atomic at the C level; no crash risk from concurrent heartbeat updates

---

## Regression Test

**File:** `api/tests/test_1526_ssl_handshake_hang.py`

Four tests:
1. `test_reconcile_workflow_single_load_with_n_agents` — verifies `_load()` is called at most once for N workflow agents (was N times before the fix)
2. `test_reconcile_workflow_accepts_preloaded_dict` — verifies zero `_load()` calls when all_workflows is supplied
3. `test_snapshot_wraps_recover_and_reconcile_in_thread` — verifies `_recover_bulk_cancelled_agents` appears in `asyncio.to_thread` calls
4. `test_snapshot_preloads_workflows_via_executor` — verifies `_reconcile_workflow_step_agents` receives the pre-loaded dict

**How to run:**
```bash
MYOS_API_DIR=/Users/torimeyer/claude/torios/api \
  api/.venv/bin/python3.11 -m pytest api/tests/test_1526_ssl_handshake_hang.py -v
```

---

## Verification

```
4 passed in 0.03s
```

Full suite (excluding pre-existing failures in `test_agent_duration_stats`, `test_data_safety`, `test_retro_prevention`, `test_tool_executor`):

```
5316 passed, 7 skipped in 191.64s
```

Zero regressions introduced. Changes are backward-compatible — standalone callers of `_reconcile_workflow_step_agents()` (none at boot/startup) continue to work via the `all_workflows=None` fallback path.
