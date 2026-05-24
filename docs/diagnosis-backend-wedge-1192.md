# Backend wedge diagnosis — →1192

## Symptom

`GET /api/agents` periodically stalls for ~4 seconds every 30 seconds, blocking the event loop long enough to fail health probes. The watchdog kills the backend with SIGKILL. curl reports HTTP 000 / curl 52 because the test was accidentally using `http://` against an HTTPS-only server (red herring); the real signal is TTFB = 4 seconds on cache expiry, 0.19 seconds on a warm cache.

## Root cause

`_find_freshest_matching_jsonl` (agents.py ~line 2688) is called once per agent during every `GET /api/agents` enrichment pass. Each call linearly scans `_load_candidates` — up to 826 candidate JSONL files — calling `_first_line_matches_needle` (12 string comparisons each) against every candidate.

When `_resolve_cache` expires (TTL = 30 s), the next request resolves every agent in the 24-hour enrichment window. With ~162 agents and 826 candidates:

    162 agents × 826 candidates × 12 pattern checks × ~4 µs each ≈ 6 seconds

All of this runs in `asyncio.to_thread`. Python-heavy work (dict lookups, string matching) holds the GIL. While the thread holds the GIL, the main event loop thread cannot accept new TLS handshakes or fire scheduled callbacks. Health probes time out. Watchdog kills the process.

Evidence:
- `/tmp/myos-backend-watchdog.log`: "backend pid 87064 alive but health probe failed 3x — event loop likely deadlocked; sending SIGKILL"
- TTFB measurement: 4.016 s first request after 35 s idle; 0.19 s second request immediately after
- `_RESOLVE_TTL_SECONDS = 30.0` at agents.py line 2095 (30 s TTL exactly matches observed 30 s wedge period)

## Fix

Build an inverted `{name_lower → freshest_path}` index inside `_load_candidates` while iterating the 826 candidates once (at cache-fill time). Cache the index alongside the candidates list. In `_find_freshest_matching_jsonl`, do an O(1) dict lookup first; fall back to linear scan only if the name could not be extracted (rare edge cases). This converts 1.6 M string comparisons per 30-second window to ~162 dict lookups.

## Files changed

- `api/routers/agents.py`: `_load_candidates`, `_find_freshest_matching_jsonl`, new `_extract_agent_name`
- `api/tests/test_agents_list.py`: regression test asserting resolve returns under 2 s with 800 candidates
