# Diagnosis: /api/agents wedge (→1192)

*Written 2026-05-12 by agent `diagnose-fix-api-agents-wedge-11`*

## Symptom

`GET /api/agents` intermittently returns HTTP 000 (empty reply / hang), or takes 3-5s on
the first call after a ~30s idle window, blocking all agent visibility. Standing-rules hook
fires this endpoint on every tool call, so one slow response serialises an entire Claude
session.

## Reproduction

```bash
# HTTP (no TLS) always returns 000 — server drops the connection
curl -sk http://localhost:8000/api/agents  # HTTP 000, 0.058s

# HTTPS sequential — usually fast, spikes to 2.6-4.9s at 30s boundaries
for i in $(seq 10); do curl -sSk -w "%{http_code} %{time_total}\n" -o /dev/null https://localhost:8000/api/agents; done

# HTTPS concurrent — all serialise to ~1.55s each (lock contention)
for i in $(seq 10); do curl -sSk -w "%{time_total}\n" -o /dev/null https://localhost:8000/api/agents & done; wait

# Cold-cache spike: wait 32s, then hit the endpoint
sleep 32 && time curl -sSk -o /dev/null https://localhost:8000/api/agents  # ~4.4s
```

Observed: first=4.403s, second=1.724s, subsequent <0.1s. Cold-cache confirmed ~3.5s.

## Root cause (multi-factor thundering herd at 30s boundary)

### Primary: blocking subprocess on the event loop (agent_reaper.py:285-296)

`api/lib/agent_reaper.py` `_worktree_head_hash()` calls `subprocess.run(["git", "rev-parse",
"HEAD"], ...)` **synchronously inside an `async def` function** with no thread offload. The
reaper runs every `SWEEP_INTERVAL_SECONDS = 30` seconds. With 826 agent worktree entries on
disk each call can block the event loop for tens of milliseconds per agent scanned.

```python
# BEFORE (blocking — stalls event loop)
def _worktree_head_hash(name: str, worktree_path: str) -> Optional[str]:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree_path, ...)
    return result.stdout.strip() if result.returncode == 0 else None
```

`_do_sweep()` is `async def` but calls `_worktree_head_hash` inline (not via
`asyncio.to_thread`), so the git subprocess occupies the event loop thread directly.

Fix: wrap `_do_sweep` body in `asyncio.to_thread` so the entire sweep runs off-loop.

### Secondary: coincident 30s cache TTL (thundering herd)

Both `_candidates_cache` (`_CANDIDATES_TTL_SECONDS = 30.0`, agents.py:2498) and
`_resolve_cache` (`_RESOLVE_TTL_SECONDS = 30.0`, agents.py:2095) expire every 30s — the
**same interval** as `SWEEP_INTERVAL_SECONDS`. At the 30s boundary:

1. Both caches miss simultaneously
2. The reaper fires its blocking git subprocesses
3. Incoming `/api/agents` requests stack behind `_enrich_async_lock`
4. All N concurrent callers (including standing-rules hook) wait the full cold-enrich time

Combined latency: ~3.5s. Each caller blocks for the full duration.

### Tertiary: HTTP callers generating failed TCP connections

- `session-start.sh` tries **HTTP first** (always fails with HTTP 000) before HTTPS
- `heartbeat-agent.sh` tries HTTPS first but falls back to HTTP (always fails)
- These generate connection-establishment overhead on every hook fire

`chat.py:_fetch_agents_list` was fixed in ca2a943. Shell hooks remain unpatched.

## py-spy

`py-spy` required `sudo` on macOS (SIP). Could not attach to uvicorn worker (PID 33799)
without terminal sudo. Stack signature inferred from code analysis and Python subprocess
benchmark. Timing data gathered via `time.perf_counter` in a direct Python subprocess
calling the same code paths.

## Fix plan

1. `api/lib/agent_reaper.py`: wrap `_do_sweep` in `asyncio.to_thread` so blocking git
   subprocesses run off the event loop.
2. `.claude/hooks/session-start.sh`: move HTTPS attempt before HTTP (or remove HTTP).
3. Write regression test: N=50 fake agents, 10× concurrent `/api/agents` calls, all <2s.
4. Quality gate: `python3 -m pytest api/tests/ -x -q`.

## Files touched

- `api/lib/agent_reaper.py` (primary fix)
- `.claude/hooks/session-start.sh` (secondary fix)
- `api/tests/test_agents_wedge_regression.py` (new regression test)
