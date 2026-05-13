# Needle 1290: Chat connection error "Connection dropped before the response finished" recurring

## Symptom

User-visible toast/bubble in mychat:

> Connection dropped before the response finished. Please try again.

Reported by torios as recurring "despite backend green" (i.e. `/api/health` returns 200 at the time of the error).

## Where the string is thrown

Only one throw site for the literal string: `app/src/hooks/useWebSocket.ts:121`, inside the WebSocket `onclose` handler. It fires when:

- the socket closes
- `serverDoneReceivedRef.current` is false (server did NOT send a `done` or `error` frame)
- `streamEndedRef.current` is false (server emitted at least one in-flight frame type: `token`, `thinking`, `tool_use*`, `tool_result*`, or the client called `send()` without yet receiving a terminal frame)

So the error fires when a turn was in flight and the socket closed before the server sent a terminal frame.

## What's already in place (prior fixes →874, →1113, →1122)

1. Backend `_TerminalTrackingWS` wrapper (chat.py:1132): observes whether `done`/`error` was sent during a turn. Emits a fallback `{"type":"done"}` in `finally` if no terminal frame was sent.
2. Backend `notify_active_websockets_of_shutdown` (chat.py:71): sends `{"type":"error", "data":"backend restarting"}` to every live chat WS during uvicorn reload before the socket is torn down.
3. Heartbeat loops in both `claude_code_provider.py:741` and `chat_providers.py:1009`: send `{"type":"heartbeat"}` every 10s during silent phases (thinking, tool-use planning) so the vite proxy and browser idle timers do not close the socket.
4. Frontend `useWebSocket.ts:182`: drops heartbeat frames on the floor without overwriting `lastMessage` or touching `streamEndedRef`.
5. Stream timeout raised to 1800s in `claude_code_provider.py:99`.

## Hypotheses to investigate (root-cause search)

H1. **Direct `websocket.send_json` calls bypass `_TerminalTrackingWS`.** Lines in chat.py that send via the raw `websocket` rather than `tracked_ws`:
   - 1207 (`{"type":"error","data":"No messages"}`) — but followed by `continue`, so the turn loop restarts cleanly. Frontend `streamEndedRef` flips on `send()`, so an `error` frame here DOES properly set `serverDoneReceivedRef`. Likely fine.
   - 1227 (`_send_backend_active`) — type is `backend_active`, not a terminal frame, so non-issue.
   - `call_model` at 630 sends `{"type":"model_label"}` via `websocket`, but the caller passes `tracked_ws`, so this actually goes through the wrapper. OK.

H2. **Frame types that DO NOT flip `streamEndedRef` but DO represent "turn in flight".** A turn that consists ONLY of these frames followed by socket close would surface the error:
   - `model_label`
   - `backend_active`
   - `multi_ai_status` (phase=thinking/speaking)
   - `multi_ai_turn_start` / `multi_ai_turn_end`
   - `peer_chat_turns_required`
   - text/non-terminal `text` (line 1047 sends `{"type":"text"}`, not `token`)
   
   But the `send()` call at the client flips `streamEndedRef.current = false` so the close-as-error still fires; that is intended.

H3. **TLS/SSL termination race.** Backend now uses `--ssl-keyfile`/`--ssl-certfile` (uvicorn args: `--ssl-keyfile /Users/torimeyer/.myos/localhost.key`). If the WSS handshake survives but the TLS read returns 0 bytes mid-stream (e.g. due to a TLS-level renegotiation or self-signed cert revalidation), the browser fires `onclose` with no close frame. The `notify_active_websockets_of_shutdown` only fires on uvicorn shutdown, not on a per-socket TLS hiccup.

H4. **Multiprocessing fork still tied to port 8000.** `ps` shows two python processes bound to port 8000: the uvicorn parent (PID 86540) and a `multiprocessing.spawn` child (PID 25392). If a worker process is killed mid-stream (e.g. via the periodic reaper or py-spy stack dump), the WSS connection terminated by that worker drops every active stream on it. There IS a watchdog SIGKILL on deadlock (commit eb2d0b8) which would kill the worker without any chance to notify the WS.

H5. **`asyncio.wait_for(_read_stdout(), timeout=1800.0)` cancelled by CancelledError doesn't reach the terminal-frame guarantee.** If `_read_stdout()` raises `CancelledError`, the outer `try/finally` in claude_code_provider DOES cancel the heartbeat task, but it does NOT send a fallback terminal frame. The chat.py `_TerminalTrackingWS` `finally` block should still catch this because it surrounds the `await call_model(...)`. Let me confirm in code.

## Plan

1. Scaffold (this doc) + commit. DONE.
2. Falsify H1–H5 via code reading + write a failing regression test for the highest-probability cause.
3. Likely root cause from existing context: watchdog SIGKILL (H4) or CancelledError in subprocess heartbeat (H5). Both are not covered by the existing tracked_ws fallback.
4. Add a backend-side guarantee: a top-level `finally` that always sends `{"type":"done"}` or `{"type":"error","data":"backend hiccup"}` on socket close path, regardless of cancellation type.
5. Test command:
   - Frontend: `bash scripts/run-vitest.sh app/src/hooks/useWebSocket.test.tsx`
   - Backend: `api/.venv/bin/pytest api/tests/test_chat_providers.py -v`

## Close criteria

- Regression test capturing the recurring close path passes.
- Commit hash for fix.
- Test name in close reason.
