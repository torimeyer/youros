# Polling Hooks Audit — →1286

> Status: COMPLETE — all FAILs fixed and green-tested

Canonical pattern (from ab8bd19): AbortController + chained `setTimeout` + backoff on error.

---

## Results

| file:line | hook name | AbortController? | chained-setTimeout? | backoff? | verdict |
|-----------|-----------|:---------------:|:-------------------:|:--------:|---------|
| `app/src/hooks/useNotificationsFeed.ts:28` | `useNotificationsFeed` | YES | YES | YES | PASS |
| `app/src/hooks/useDashboardFeed.ts:29` | `useDashboardFeed` | YES | YES | YES | PASS |
| `app/src/hooks/useCalendarFeed.ts:29` | `useCalendarFeed` | YES | YES | YES | PASS |
| `app/src/hooks/useWebSocket.ts` | `useWebSocket` | N/A | N/A | YES WS reconnect | PASS-INTENTIONAL — WS connection manager, not a data-polling hook; has its own exponential backoff for reconnect |
| `app/src/hooks/useLocksFeed.ts:54` | `useLocksFeed` (WS fallback) | YES fixed | YES fixed | YES fixed | FIXED (was setInterval, no abort, no backoff) |
| `app/src/hooks/useRunningAgentsFeed.ts:67` | `useRunningAgentsFeed` (WS fallback) | YES fixed | YES fixed | YES fixed | FIXED (was setInterval, no abort, no backoff) |
| `app/src/hooks/useSessionsFeed.ts:54` | `useSessionsFeed` (WS fallback) | YES fixed | YES fixed | YES fixed | FIXED (was setInterval, no abort, no backoff) |
| `app/src/hooks/useGrantsFeed.ts:53` | `useGrantsFeed` (WS fallback) | YES fixed | YES fixed | YES fixed | FIXED (was setInterval, no abort, no backoff) |
| `app/src/hooks/useAdhdMode.ts:100` | `useAdhdMode` check-in | YES fixed | YES fixed | YES fixed | FIXED (was setInterval, no abort, no backoff) |
| `app/src/components/ReleaseNotesWatcher.tsx:144` | `ReleaseNotesWatcher` spec poll | YES fixed | YES fixed | YES fixed | FIXED (was setInterval, no abort, no backoff) |
| `app/src/components/TopBar.tsx:210` | UI clock tick | N/A | N/A | N/A | PASS-INTENTIONAL — setInterval triggers local state update for relative-time display, no network call; abort pattern is not applicable |

---

## PASS-INTENTIONAL non-polling setTimeout usages

These are one-shot or UI-timing uses — not recurring data-fetch loops — so the 3-part pattern does not apply:

- `TopBar.tsx` line 332: 100ms delay before dispatching a UI event
- `McpInstaller.tsx`: 3.5s toast auto-dismiss
- `QuickSpawnAgentModal.tsx`, `SpecTemplateDetailsModal.tsx`: 0ms focus-on-mount
- `GuidedTour.tsx`: 80ms DOM poll to find tour target element (one-shot DOM search, no fetch)
- `ChatPanel.tsx`: various animation/grace-window timers (no fetch)
- `TackAutocomplete.tsx`: debounce timer
- `stores/notifications.ts`: toast TTL timers

---

## What was fixed

All 6 FAIL sites were refactored from setInterval to a chained tick function with:

1. **AbortController** — created per tick, stored in pollAbortRef; cancelled on WS reconnect and component unmount, preventing in-flight requests from completing after cleanup.
2. **Chained setTimeout** — next tick is only scheduled after the current fetch resolves/rejects, eliminating pile-up under slow or unresponsive backends.
3. **Backoff** — `backoff = 0 ? 1000 : Math.min(backoff * 2, 30_000)` on error; reset to 0 on success. Caps at 30s. Prevents ERR_INSUFFICIENT_RESOURCES storms during backend wedges.

For `ReleaseNotesWatcher` the polling was also moved from `api.get` (internal-only AbortController, no external cancel) to `fetch` with an external signal, matching the pattern used by the other hooks.

---

## Tests

| test file | tests | result |
|-----------|------:|--------|
| `src/hooks/useLocksFeed.test.ts` | 7 | all pass |
| `src/hooks/useRunningAgentsFeed.test.ts` | 5 | all pass |
| `src/hooks/useSessionsFeed.test.ts` | 5 | all pass |
| `src/hooks/useGrantsFeed.test.ts` | 5 | all pass |
| `src/hooks/useAdhdMode.test.ts` | 4 | all pass |
| `src/components/ReleaseNotesWatcher.test.tsx` | 16 | all pass (13 existing + 3 new pattern tests) |

Each new test file covers: abort on cleanup, backoff doubling, no pile-up under slow response.
