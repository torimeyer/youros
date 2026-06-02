---
status: spec
promoted_at: 2026-06-01T12:34:44Z
---
# Live agent activity feed

**Needle:** →2029  
**UAT item:** B6  
**Source ACs:** →1904 (event types), →1902 sphere (smoke tests), →1876, →1890, →1913

---

## Problem

When agents are running, the user has no way to see a live log of what just happened. The Activity page (`/activity`) exists and has a working feed UI, but it does not show agent lifecycle events (`agent.spawned`, `agent.completed`, `agent.failed`). The page also has no sidebar nav entry, so it is only reachable by typing the URL directly. The feed refreshes by polling every 10 seconds, which means there can be a visible lag even when the WebSocket connection is live.

## Goals

- Make the three core agent lifecycle events appear in the Activity feed with readable labels and appropriate icons.
- Wire the feed to the existing WebSocket connection so new agent events appear in under 2 seconds, not up to 10 seconds.
- Add an "Activity" nav entry in the sidebar so the page is discoverable.
- Show a clear empty state when no events exist yet.

## Non-goals

- Adding new event categories beyond agent lifecycle (e.g. file edits, needle changes) is out of scope.
- Redesigning the existing Activity page tabs ("events", "transcripts", "learned") or the stream row layout.
- Any backend changes beyond verifying the existing `_emit_audit_event` calls produce rows that `/api/activity` returns.

---

## Acceptance Criteria

### Event types in the feed (sourced from needle →1904)

- [ ] Spawning an agent produces an `agent.spawned` entry in the Activity feed within 2 seconds of the agent registering.
- [ ] An agent completing its work produces an `agent.completed` entry in the Activity feed within 2 seconds.
- [ ] An agent failing produces an `agent.failed` entry in the Activity feed within 2 seconds.
- [ ] The `agent.spawned` entry shows the agent name and the word "spawned" (e.g. "diagnose-bug-abc spawned").
- [ ] The `agent.completed` entry shows the agent name and "completed" (e.g. "diagnose-bug-abc completed").
- [ ] The `agent.failed` entry shows the agent name and "failed" with a visually distinct color (red palette).
- [ ] Each entry's expand drawer shows the raw event type and timestamp.

### Real-time updates (sourced from →1904 "live" requirement and →1902 smoke test)

- [ ] When the WebSocket connection (`/api/ws/agents/state`) is live, a new agent event appears in the feed without waiting for the 10-second poll.
- [ ] When the WebSocket is disconnected, the 10-second HTTP poll fallback still updates the feed.
- [ ] A smoke test: spawn one agent, observe the `agent.spawned` row appear in the Activity page within 2 seconds.

### Sidebar navigation (sourced from B6 UAT discoverability requirement)

- [ ] An "Activity" link is visible in the sidebar, navigating to `/activity`.
- [ ] The "Activity" link is reachable without typing the URL.

### Empty state (standard UI requirement)

- [ ] When the activity feed has no events, the page shows an empty state message instead of a blank area (e.g. "No activity yet. Events will appear here as agents run.").

### Existing code reuse (pre-design audit findings)

- [ ] The `app/src/pages/Activity.tsx` component (492 lines, existing on main) is extended, not replaced.
- [ ] The `app/src/lib/activityStream.ts` `curateEvent` function handles `agent.spawned`, `agent.completed`, and `agent.failed` event types, mapping them to stream entries with palette and icon.
- [ ] The `app/src/hooks/useRunningAgentsFeed.ts` hook is used (or its store is read) to trigger a feed refresh when a WS frame arrives, rather than building a second WebSocket connection.
- [ ] The sidebar update is made in `app/src/components/Sidebar.tsx`.

---

## Design notes

### Where events come from

The backend (`api/routers/agents.py`) already calls `_emit_audit_event("agent.spawned", ...)` and `_emit_audit_event("agent.completed", ...)`. These write rows to the ostk audit log. The `/api/activity` endpoint reads from that log and returns them. No new backend code is needed beyond confirming the event rows include enough fields for the frontend to label them.

### Real-time mechanism

`useRunningAgentsFeed` (in `app/src/hooks/useRunningAgentsFeed.ts`) already listens on the WebSocket at `/api/ws/agents/state`. When a WS frame arrives signaling a status change, the Activity page should trigger a re-fetch of `/api/activity` immediately rather than waiting for the `setInterval` timer. The simplest approach: subscribe to `useRunningAgentsStore` in `Activity.tsx` and trigger `fetchActivity()` when the store changes.

### The feed page is a dedicated page, not a panel

The Activity page lives at `/activity` and has its own full-page layout. It is not an inline panel or modal. The sidebar link navigates to that route.

---

## Verified against the codebase

- `app/src/pages/Activity.tsx` exists on main at line 1, 492 lines. Route registered in `app/src/App.tsx:165`.
- `app/src/lib/activityStream.ts` exists on main with `curateEvent` at line 153 and `buildStream` at line 347.
- `app/src/hooks/useRunningAgentsFeed.ts` exists on main, uses `useWebSocket('/api/ws/agents/state', true)`.
- `app/src/components/Sidebar.tsx` exists on main with no current "Activity" or "/activity" entry (confirmed by grep returning 0 results).
- `api/routers/agents.py` emits `agent.spawned` at line 6750 and `agent.completed` at line 2060 via `_emit_audit_event`.
- `docs/spec/live-agent-activity-feed.md` does not yet exist on main (confirmed: only `docs/spec/` files are drive-slides spec, pre-design spec, workspace spec, discord spec, text spec).
