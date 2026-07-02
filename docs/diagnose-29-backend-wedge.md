# Backend / Vite wedge root cause — 2026-05-28

## Root cause (one line)

`_compute_conv_totals_incremental()` cold-reads the 571 MB `metrics.jsonl` in
a thread pool worker every time the backend restarts, holding the Python GIL
for **4.4 seconds** and preventing the asyncio event loop from completing TLS
handshakes for any new connection.

## Evidence

| Measurement | Value |
|---|---|
| `metrics.jsonl` size | 571 MB / 2,889,091 entries |
| Cold full-scan time | **4.38 s** (Python JSON parsing, GIL-held) |
| Client connect timeout (`--connect-timeout 3`) | 3 s |
| Synthetic load test (5 concurrent requests) | All 5 fail at exactly 3.005 s |
| `ostk work list --json --status open` | 10 ms (not the bottleneck) |
| Snapshot loop sync work (450 agents) | 0.3 ms (not the bottleneck) |
| glob+stat 1760 transcript files | 6 ms (not the bottleneck) |
| After disk cache warm: cold-restart cost | **0 ms** |

Reproduction: 5 concurrent `POST /api/onboarding/intent` calls with
`--connect-timeout 3` → all fail simultaneously with `SSL connection timeout`.
After merging the fix and warming the disk cache: all 5 return HTTP 200.

## Mechanism

`prewarm_savings()` fires 2 seconds after every backend start:

```python
await loop.run_in_executor(None, token_metrics.get_ostk_savings)
```

This calls `_compute_conv_totals_incremental()`. The in-memory cache
(`_METRICS_TOTALS_CACHE`) is `None` on every restart, so the function falls
through to the full re-read path: reads all 571 MB, parses 2.9 M JSON lines.

`asyncio.to_thread` / `run_in_executor` does NOT free the event loop for
GIL-heavy Python work. While the thread holds the GIL parsing JSON, the
asyncio event loop cannot call `ssl.do_handshake()` for incoming connections.
Chrome's `--connect-timeout 3` expires before the handshake completes.

## Why it cascades to Vite (mode a → mode b)

1. Backend event loop blocked for 4.4 s → all health probes fail
2. Watchdog sees 2 consecutive failures → runs `dev-backend.sh`
3. `dev-backend.sh` kills the running process, new one fails `EADDRINUSE`
   (macOS socket in TIME_WAIT, or a race between two watchdog instances)
4. Backend is completely dead (mode b)
5. Vite's 10-slot proxy pool fills waiting for the dead backend → Node.js
   event loop saturates → Vite TLS handshakes also fail

## Fix

Persist `_METRICS_TOTALS_CACHE` to `.ostk/metrics_totals_cache.json` so
restarts load the file position from disk and do a cheap tail-read.

**`api/services/token_metrics.py`** changes:
- Added `_METRICS_DISK_CACHE_PATH` module constant
- Added `_load_metrics_disk_cache()` — reads disk JSON into `_METRICS_TOTALS_CACHE` on cold start
- Added `_save_metrics_disk_cache()` — writes cache after every update
- `_compute_conv_totals_incremental()` calls `_load_metrics_disk_cache()` at entry

Cold-start path after fix: load 200-byte JSON file (< 1 ms) → tail-read any
bytes appended since last save → return. Full re-scan only happens once (first
ever run or if `metrics.jsonl` inode changes).

## Disk cache pre-seeded

`/Users/you/claude/torios/.ostk/metrics_totals_cache.json` was written
during this investigation. The next restart will use it immediately.

## Branch / commit

`worktree-agent-29-root-cause-backend-fa37e781`
Commit: `5ec5bda7` — fix(→29): persist metrics totals cache to disk

Tests: `api/tests/test_token_metrics.py` — 7 pass, includes 2 new tests for
disk cache round-trip (`test_disk_cache_skips_full_scan_on_cold_start`,
`test_disk_cache_written_after_full_scan`).

## What was ruled out

- `ostk work list --json --status open`: 10 ms subprocess — not the cause
- Snapshot loop (450 agents): 0.3 ms sync work per cycle — not the cause
- `_run_enrich_pipeline`: skips old stopped agents, < 1 ms normal — not the cause
- `glob+stat` 1760 transcript files: 6 ms — not the cause
- `audit.jsonl` read: incremental tail cache, < 1 ms warm — not the cause
- Socket lock contention: explains slow `task_counts` (3.7 s queue time) but
  is async — does not block TLS handshakes directly
