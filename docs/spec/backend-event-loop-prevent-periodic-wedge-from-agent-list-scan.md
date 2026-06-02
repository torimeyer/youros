---
promoted_at: 2026-06-01T16:24:23Z
title: 'Backend event loop: prevent periodic wedge from agent list scan'
status: spec
---

## Problem

Every 30 seconds, `GET /api/agents` triggers a cache expiry and runs a full linear scan through all agent JSONL candidates to enrich each active agent. The scan holds the Python GIL long enough to freeze the event loop, causing health probes to time out and the watchdog to kill the backend process with SIGKILL.

Observed pattern: TTFB jumps to 4+ seconds on the first request after each 30-second cache window. Backend pid is killed by watchdog, visible in `/tmp/myos-backend-watchdog.log`.

## Root cause

`_find_freshest_matching_jsonl` in `agents.py` performs an O(n * m) scan on every enrichment pass:
- n = number of active agents (up to ~162)
- m = number of candidate JSONL files (up to ~826)
- Each comparison runs 12 pattern checks at ~4µs each

Total time per expiry: ~6 seconds. Because this runs inside `asyncio.to_thread`, the GIL-heavy Python string matching still blocks the main event loop. Health probes time out, the watchdog fires.

## Goals

- `GET /api/agents` returns in under 200ms even after cache expiry.
- The event loop remains responsive (health probes succeed) during enrichment.
- No backend process kills from watchdog during normal agent list usage.

## Non-goals

- Removing the resolve cache entirely.
- Changing JSONL file storage layout.

## Acceptance criteria

- [ ] The candidate list for `_find_freshest_matching_jsonl` is indexed once on startup (or on cache miss) rather than scanned linearly on every call.
- [ ] `asyncio.to_thread` tasks that do GIL-heavy work yield to the event loop via `asyncio.sleep(0)` every N iterations (N <= 10).
- [ ] `api/tests/test_2018_event_loop_wedge.py` passes: no request latency spike > 1 second after simulated cache expiry.
- [ ] Health probe endpoint (`/api/health`) responds in under 100ms during a concurrent enrichment pass.
- [ ] Existing `/api/agents` response-shape tests continue to pass.
