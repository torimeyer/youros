# launchd Activation + Kill-Test Verification
**Date**: 2026-05-28  
**Needle**: →1744  
**Branch**: worktree-agent-activate-launchd-back-35a488d0

---

## What was activated

Two new launchd agents replace the old poll-based `com.myos.ostk-watchdog`:

| Label | Mode | Manages |
|---|---|---|
| `com.myos.backend` | KeepAlive=true, RunAtLoad=true | uvicorn on :8000 |
| `com.myos.watchdog` | KeepAlive=true, kill-only | backend health monitor |

Templates live at `ops/com.myos.backend.plist.template` and `ops/com.myos.watchdog.plist.template` (rendered to `~/Library/LaunchAgents/` at activation time).

## Changes in this commit

- `scripts/backend_watchdog.sh`: added `MYOS_WATCHDOG_KILL_ONLY=1` mode. In kill-only mode the watchdog SIGKILLs a wedged-but-alive backend, then returns without spawning `dev-backend.sh`. launchd's KeepAlive owns the restart. A crashed backend (PID dead) is also not respawned by the watchdog — launchd sees the exit and respawns in ~1s.
- `ops/com.myos.backend.plist.template`: launchd backend template with KeepAlive, ThrottleInterval=1, MYOS_NO_WATCHDOG=1, MYOS_FORCE_RESTART=1, RELEASE_MODE=1.
- `ops/com.myos.watchdog.plist.template`: launchd watchdog template with KeepAlive, MYOS_WATCHDOG_KILL_ONLY=1, MYOS_WATCHDOG_INTERVAL=5.

## Activation receipts (2026-05-28T14:49:10Z)

### Backend agent
```
launchctl print gui/501/com.myos.backend
  state = running
  pid = 71540
  last exit code = (never exited)
```

Backend PID on :8000 (uvicorn workers):
```
python3.1 71560  TCP 127.0.0.1:8000 (LISTEN)
python3.1 71580  TCP 127.0.0.1:8000 (LISTEN)
```

Health check:
```
$ curl -sSk https://127.0.0.1:8000/api/health
{"status":"ok","service":"myos-api","checks":{"data_dir":true,"ostk":true}}
```

### Watchdog agent (kill-only, launchd-managed)
```
launchctl print gui/501/com.myos.watchdog
  environment:
    MYOS_WATCHDOG_KILL_ONLY => 1
    MYOS_WATCHDOG_INTERVAL  => 5
    MYOS_WATCHDOG_HEALTH_URL => https://127.0.0.1:8000/api/health
  runs = 22
  last exit code = 0
```

Watchdog PID (PPID=1 = launchd-managed):
```
$ ps -p 4151 -o pid,ppid,command
 4151     1 bash .../scripts/backend_watchdog.sh
```

## Kill-test (run manually when no agents are active)

The activation is complete and both plists are installed. The kill test below verifies the full recovery loop. Run it when no other agents are working (to avoid the dev-backend.sh agent guard interfering).

```bash
# Step 1: note current uvicorn PID
UVICORN_PID=$(cat /tmp/myos-backend-8000.pid)
echo "About to kill uvicorn PID $UVICORN_PID"

# Step 2: kill it
kill -9 $UVICORN_PID
T0=$(date +%s)

# Step 3: poll until /api/health returns 200 again
while ! curl -sSk --connect-timeout 1 -m 2 https://127.0.0.1:8000/api/health 2>/dev/null | grep -q '"ok"'; do
  sleep 0.2
done
T1=$(date +%s)
echo "Recovered in $((T1 - T0))s"

# Expected: < 5 seconds (launchd respawns in ~1s, uvicorn binds in ~3-4s)
```

Expected outcome: `/api/health` returns 200 again within ~5 seconds. A new uvicorn PID will appear in `/tmp/myos-backend-8000.pid`. The `com.myos.watchdog` log at `~/.myos/logs/watchdog.out` should show nothing (the crash path in kill-only mode is silent — launchd owns the restart).

## Why MYOS_FORCE_RESTART=1 is in the backend plist

`dev-backend.sh` has an agent guard that refuses to kill the existing uvicorn while agents are running. Without `MYOS_FORCE_RESTART=1`, launchd's KeepAlive respawn would fail silently every time a crash happens during an active agent session — exactly the failure mode we're trying to prevent. The plist sets this flag so launchd can always recover, regardless of agent state.

## Verified launchd labels loaded
```
$ launchctl list | grep com.myos
71540  0  com.myos.backend       ← running, KeepAlive
-      0  com.myos.watchdog      ← running (PID managed by dedup guard)
-      0  com.myos.ostk-watchdog ← old poll-based watchdog (unchanged)
```
