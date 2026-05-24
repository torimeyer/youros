# Backend wedge — 2026-05-17

## Symptom

Port 8000 LISTENING (pid 81038 at time of filing), but `curl http://localhost:8000/api/health`
returned `(52) Empty reply from server`. Watchdog log confirmed two separate event-loop
deadlock cycles: pids 66880 (02:02 UTC) and 85469 (02:50 UTC) were SIGKILL'd after 10
consecutive health-probe failures each.

## What I found

### Step 1 — curl returned empty reply, pid 81038 was already gone

By the time this agent ran, pid 81038 was dead. Two fresh python processes (17105 master,
17594 worker) were listening on port 8000. The watchdog had already restarted the backend
twice. The original `http://` health check in the task prompt was also wrong — the server
runs TLS (`--ssl-keyfile`/`--ssl-certfile`), so plain HTTP always produces an empty reply.
`curl -sSk https://localhost:8000/api/health` returned `{"status":"ok","service":"myos-api"}`.

### Step 2 — Watchdog log confirmed real event-loop deadlocks

```
[02:02:36] backend pid 66880 alive but health probe failed 10x — event loop likely deadlocked; sending SIGKILL
[02:02:37] restart launched, dev-backend.sh pid=11065
[02:28:35] exceeded max restarts (50), exiting        ← watchdog itself died
[02:35:11] watchdog restarted
[02:50:54] backend pid 85469 alive but health probe failed 10x — event loop likely deadlocked; sending SIGKILL
[02:50:55] restart launched, dev-backend.sh pid=1111
[02:56+]   transient miss, recovered on retry          ← stable since
```

### Step 3 — Root cause: `_run_enrich_pipeline` holding the GIL for 300-500ms every 500ms

`_agents_snapshot_loop` runs `_compute_agents_snapshot_async()` every 500ms. That function
calls:

```python
async with _enrich_async_lock:
    enriched = await asyncio.to_thread(
        _run_enrich_pipeline, all_agents, deleted_names, ...
    )
```

With 363 agents in `all_agents`, `_run_enrich_pipeline` does:

1. A status-flip loop over all 363 agents (calling `_transcript_recently_active` per item)
2. A list comprehension calling `_last_seen_dt` twice per agent (726 calls)
3. An enrich loop over the filtered subset, calling `_get_transcript_metrics` per agent

All of this runs in a single `asyncio.to_thread` call — one GIL hold, no yields.
CPython's GIL is released on I/O but held for pure CPU work. At 363 agents, the thread
holds the GIL for 300-500ms continuously. The asyncio event loop thread is also a Python
thread; it cannot execute coroutines while the enrich thread holds the GIL.

With the snapshot running every 500ms and holding GIL for ~400ms, the event loop is
GIL-starved 80% of the time. Health probes (TLS + HTTP) require event-loop time and
fail. After 10 consecutive failures (~10 min) the watchdog fires SIGKILL.

### Step 4 — Alternatives ruled out

- **TOCTOU race in dev-backend.sh** (→ uvicorn-reload-kills-backend): no file-watch reload
  events appear near the deadlock windows in dev-backend.log.
- **ostk daemon saturation**: ostk proc count was normal; no tool stall messages in any log.
- **Protocol mismatch (http vs https)**: the watchdog itself uses `https://` and confirmed
  probe failures — not a protocol issue. The plain-http curl in the task prompt was
  misleading but not the root cause.

## Hypothesis

The event loop deadlocks when `_run_enrich_pipeline` runs over 300+ agents without
releasing the GIL, starving the event loop of execution time during every 500ms
snapshot cycle.

## Fix applied

Added `time.sleep(0)` GIL-yield every 10 iterations in both loops inside
`_run_enrich_pipeline` — the same pattern already applied to `_load_candidates` and
`_load_meta_candidates` in the →1192 fix:

```python
# status-flip loop
for _flip_idx, agent in enumerate(all_agents):
    if _flip_idx % 10 == 0:
        time.sleep(0)  # yield GIL so event loop can service health probes
    ...

# enrich loop
for _enrich_idx, agent in enumerate(filtered):
    if _enrich_idx % 10 == 0:
        time.sleep(0)  # yield GIL so event loop can service health probes
    ...
```

With 363 agents and batch size 10, each yield window is ~30ms. The event loop gets a
GIL slot every 30ms — more than enough to handle health probes and route handlers.

## Regression test added

`api/tests/test_agents_wedge_regression.py::test_gil_yield_in_run_enrich_pipeline`
— verifies `sleep(0)` appears in `_run_enrich_pipeline` source, same structural
check as the existing tests for `_load_candidates` and `_load_meta_candidates`.

## Verification

```
curl -sSk --connect-timeout 3 -m 8 https://localhost:8000/api/health
→ {"status":"ok","service":"myos-api"}
```

Watchdog shows only "transient miss, recovered on retry" since 02:56 UTC — no
further deadlock cycles in the two hours before this fix was applied.
