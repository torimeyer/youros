# Diagnosis: /api/agents endpoint wedge (→1192)

*Written 2026-05-12 from diagnosis transcript `diagnose-fix-api-agents-wedge-11-c0b856`*

## Symptoms

- `/api/agents` intermittently returns an empty body or hangs, making all
  agent visibility disappear from the Agents page.
- Periodic ~3.5s latency spikes on cold-cache requests (warm = 0.07–0.25s).
- When multiple pollers (standing-rules hooks across sessions) hit a cold
  cache simultaneously, requests queue through `_enrich_async_lock` and each
  one waits ~1.5s.

## Root cause (three compounding issues)

### 1. Reaper interval ≡ cache TTL → synchronized cold-cache spikes

`_TRANSCRIPT_FLUSH_INTERVAL = 30.0` writes to transcript files every 30 s.
This changes the directory mtime, which busts the mtime-keyed
`_candidates_cache` immediately. All three caches (`_RESOLVE_TTL_SECONDS`,
`_CANDIDATES_TTL_SECONDS`, `_META_CANDIDATES_TTL_SECONDS`) also expire at 30 s.

When the flush fires, every cache goes cold at the same instant. The next
`/api/agents` request triggers a full cold rebuild (glob + readline over
~826 files = 3.487 s on the diagnosis machine). Any concurrent request
serializes through `_enrich_async_lock`, adding 1.5 s per queued caller.

**Fix:** Set flush interval to 25 s and cache TTLs to 60 s so they never
expire together.

### 2. `json.dumps(agent_metadata)` runs on the event loop

`_save_agent_state_async` took a snapshot and then called
`asyncio.to_thread(_write_state_content, content)`, but the `json.dumps`
call itself executed synchronously on the event loop. With 1506 agent rows
this costs 10–30 ms on the loop, blocking TLS handshakes on queued requests.

**Fix:** Take a per-agent dict copy on the event loop (safe, no await can
interleave), then pass the snapshot to `asyncio.to_thread` which does both
the `json.dumps` and the file write.

### 3. Cold candidates rebuild holds the GIL without yielding

The cold-rebuild loop in `_load_candidates` iterates hundreds of files
(glob + stat + readline per file). Running in `asyncio.to_thread` does not
free the event loop for GIL-heavy synchronous code: concurrent `to_thread`
calls contend on the GIL and amplify rebuild time from ~0.3 s to >3 s under
load (see `feedback_concurrent_to_thread_amplifies_gil.md`).

`_enrich_async_lock` already prevents thundering-herd at the caller level.
Adding `time.sleep(0)` every 10 iterations inside the cold-rebuild loop
releases the GIL briefly, allowing the event loop and other threads to make
progress.

## Duplicate uvicorn worker pattern

At diagnosis time, `lsof -iTCP:8000 -sTCP:LISTEN -P` showed two Python PIDs
(32719 and 33799) bound to the same port. This is a known uvicorn behaviour
when `--reload` is active: the reloader spawns a child worker and both the
parent watchdog and the child can hold the socket briefly during a reload.
No code change warranted; documented here for observability.

## Fix commits

All four changes land in `api/routers/agents.py`. Regression test in
`api/tests/test_agents_wedge_regression.py`.
