# Torichat "Connection dropped" diagnosis

Date: 2026-04-18
Scope: inline ChatPanel WebSocket drop, zero tokens streamed, "Retry" shown.

## Root cause (ranked)

### 1. Uvicorn reload killed the WebSocket mid-turn (CONFIRMED)

The `/ws/chat` socket was accepted at line 67909 of `/tmp/dev-backend.log`, the
connection opened at 67910, and two log lines later uvicorn shut down because
`WatchFiles detected changes in 'routers/agents.py'`:

```
67909 INFO: 127.0.0.1:58616 - "WebSocket /ws/chat" [accepted]
67910 INFO: connection open
67911 WARNING: WatchFiles detected changes in 'routers/agents.py'. Reloading...
67912 INFO: Shutting down
67913 INFO: connection closed
67956 RuntimeError: WebSocket is not connected. Need to call "accept" first.
```

That stack trace lands on `api/routers/chat.py:945` (`await websocket.receive_json()`)
because shutdown raced the first read. The frontend `useWebSocket` hook at
`app/src/hooks/useWebSocket.ts:98-128` sees the onclose with no prior `done`
event, flips `streamEndedRef` to "mid-turn drop", and emits the exact string
the user saw on line 120.

Why agents.py gets touched so often: `api/routers/agents.py` is 290 KB and is
edited in bursts whenever any Claude subagent registers, heartbeats, or stores
a mailbox write. Even with `--reload-delay 10.0` in
`scripts/dev-backend.sh:207`, an edit that lands 1 second into a chat turn
restarts the server before the first Anthropic token is flushed.

### 2. Double-uvicorn confusion also in play

The log repeatedly prints `ERROR: A dev-backend.sh uvicorn is already
listening on port 8000 (PID(s): 1921)` while `backend_watchdog.sh` relaunches
dev-backend.sh every ~43 s because `/api/health` does not respond during the
reload window. The watchdog fleet churn accelerates the reload cycle. I verified
PID 1921 has been running 3 h 51 m but curl to `https://127.0.0.1:8000/api/agents/register`
timed out at 3 s during this session.

### 3. Not the cause

- **Anthropic SDK timeout / 5xx**: `api/services/chat_providers.py:1760-1771`
  already wraps the stream in `_with_ws_heartbeat` (10 s heartbeats) and
  `_anthropic_retry_call` (3 attempts, 0.5/1.5/4 s). No tokens were streamed,
  so the stream never ran.
- **Frontend closed the socket**: `useWebSocket.ts` only closes on unmount or
  explicit disconnect. No evidence in the log.
- **Payload too large**: no Anthropic 4xx in the log; shutdown happened before
  the request was even read.

## Fix (concrete)

**Primary: scope the reload watch more tightly so hot files stop cycling.**

Edit `scripts/dev-backend.sh:209-227` to watch ONLY `api/routers/chat.py` and
`api/services/chat_providers.py` plus a narrow allowlist. Simplest form:
replace `--reload-dir "$API_DIR"` with multiple `--reload-include` globs, or
add `--reload-exclude 'routers/agents.py'` since agents.py is the main
offender and its handlers are pure-Python (reload is rarely needed mid-dev).

**Secondary: guard the chat websocket against reload.** In
`api/routers/chat.py:942`, wrap the `accept`/`receive_json` pair so a pending
Anthropic stream is told to cancel when uvicorn receives SIGTERM, and send a
typed `{"type":"error","data":"Server restarting, please retry"}` before the
socket closes. Today the frontend sees a silent onclose and has no way to
tell a reload apart from a real network drop.

**Tertiary: stop the watchdog thrash.** `backend_watchdog.sh` is
restarting dev-backend every 43 s because `/api/health` does not answer
during the 10 s reload window. Bump the watchdog's failure threshold to
3 consecutive misses at 5 s apart before restarting.

## Retry cap and error copy

- `useWebSocket.ts:16` already caps reconnects at 10 (RECONNECT_MAX_ATTEMPTS)
  with exponential backoff, so no new cap is needed.
- The current error "Connection dropped before the response finished. Please
  try again." is vague. Suggest surfacing the reason when known: on a
  server-initiated close with code 1012 (service restart), show "Server
  restarted. Retrying..." and auto-retry once without user action.
