# MCP flapping diagnosis

## Evidence

Live processes at 2026-04-18 16:42:

- Four `ostk kernel serve` children (17317 Wed, 69347 Wed, 92841 Wed, 69736 Sat) and two `fcp-drawio` children (50424 Sat, 92813 Wed). Every one has a live parent: `claude --dangerously-skip-permissions` (PIDs 9075, 49762, 68606, 92180), themselves all children of one Warp `terminal-server` (PID 88910, alive since Apr 13).
- `lsof -nP -iTCP:8000`: PIDs 1921 and 68280 both on socket fd device `0xb7b1c6342aefb7a3`. PID 68280's parent is 1921, command is `multiprocessing.spawn.spawn_main`. One inherited fd, not two binds.
- `/tmp/torios-backend.log` contains one historical guard rejection (Apr 17 at 13:34, PID 13692). `/tmp/myos-backend-watchdog.log` shows **106 restart attempts** in the past hour, most ending in "transient miss, recovered on retry" but many launching a fresh dev-backend.sh that the guard rejects.

## Root causes

1. **Stale MCP children are not orphans.** The Wednesday `claude` processes never exited because four Warp terminal tabs are still open. `ostk kernel serve` runs over stdio and exits on stdin EOF, but its parent is still alive so EOF never arrives. `.claude/hooks/session-end.sh` never fired for these sessions.
2. **"Dual uvicorn on 8000" is a misread.** PID 68280 is the uvicorn `--reload` worker forked from the reloader supervisor (PID 1921). Same inherited fd. One listener. The guard in `dev-backend.sh` is correct and did fire.
3. **Real flapping source: `backend_watchdog.sh`.** `--reload-delay 10.0` briefly breaks `/api/health` while uvicorn swaps workers. The watchdog only waits 2 seconds between retries, fails the second probe, relaunches dev-backend.sh, which hits the guard and exits. Repeat every 30s. Mid-flight MCP calls see "Empty reply from server" during the swap window.

## Proposed fixes

### Fix 1: widen the watchdog retry window

`scripts/backend_watchdog.sh`, replace the retry block at lines 72 to 77:

```bash
    # Three spaced retries absorb the 10-second reload window.
    sleep 5 && probe_once && { log "INFO transient miss, recovered"; continue; }
    sleep 5 && probe_once && { log "INFO transient miss, recovered"; continue; }
    sleep 5 && probe_once && { log "INFO transient miss, recovered"; continue; }
```

### Fix 2: orphan sweeper in SessionStart hook

`.claude/hooks/session-start.sh`, insert before the final `exit 0`:

```bash
for pid in $(pgrep -f "ostk kernel serve") $(pgrep -f "fcp-drawio"); do
    ppid=$(ps -o ppid= -p "$pid" | tr -d ' ')
    [ -n "$ppid" ] && ! ps -p "$ppid" >/dev/null && kill "$pid" 2>/dev/null
done
```

Only reaps true orphans (parent gone). Leaves live sessions alone.

### Fix 3: harden the guard to include non-script uvicorns

`scripts/dev-backend.sh` already exits on a live `uvicorn main:app` but treats anything else as stale and kills it. Change line 43 to match any uvicorn on the port:

```bash
        if echo "$_cmd" | grep -qE "uvicorn|python.*main:app"; then
```

Same behavior for dev-backend spawns; now also blocks accidental raw `uvicorn` from other terminals rather than silently killing them.

## One-liner health probe

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN | awk 'NR>1{print $9}' | sort -u | wc -l | awk '{print ($1<=1)?"OK 1 socket":"WARN "$1" sockets"}'; pgrep -f "ostk kernel serve" | while read p; do pp=$(ps -o ppid= -p $p | tr -d ' '); ps -p $pp >/dev/null || echo "ORPHAN ostk $p"; done
```

Prints `OK 1 socket` or `WARN N sockets`, then one line per orphaned `ostk kernel serve`. Silent when clean.
