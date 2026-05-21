# →1574: Kanban + Specs/Tasks cold-load performance

## Baseline (measured 2026-05-21)

| Endpoint | Time | Size |
|----------|------|------|
| GET /api/tasks | 685ms | 1.5MB |
| GET /api/specs | 83ms | 108KB |
| GET /api/specs/templates | 41ms | 11KB |

Task breakdown: 1182 total — 1165 closed (98%), 17 open/in_progress.

## Root Causes

1. **`/tasks` runs `compute_task_readiness()` on all 1182 tasks** including 1165 closed ones.
   Closed tasks can never be `clear_to_build`, so this is pure waste.
   `clear_to_build_checks` (avg 258 chars each) adds ~300KB of payload for closed tasks.

2. **Kanban (Backlog.tsx) fetches all 1182 tasks but only renders open + in_progress (17).**
   98% of the payload is silently filtered out in the browser.

3. **No localStorage SWR cache on Kanban or Specs pages.**
   Tasks.tsx already has this pattern. Backlog.tsx and Specs.tsx do not.
   Every navigation to these pages fires a full cold fetch.

4. Backlog.tsx fetches `/specs` and `/tasks` in parallel (already correct — not waterfall).

## Fix Plan

### Fix 1: Backend — skip readiness for closed tasks (tasks.py)
In `list_tasks()`, before the `compute_task_readiness` loop:
- If `t.get("status") == "closed"`: set `clear_to_build=False`, `clear_to_build_checks=[]`, skip.
- Removes 1165 unnecessary `compute_task_readiness()` calls per request.
- Removes ~200KB from response payload.
- **Zero behavior change** — closed tasks are already done, the UI never shows build buttons for them.

### Fix 2: Frontend — localStorage SWR cache for Backlog.tsx (Kanban)
Same pattern as Tasks.tsx `readTasksCache()` / `writeTasksCache()`:
- `KANBAN_SPECS_CACHE_KEY` + `KANBAN_TASKS_CACHE_KEY` in localStorage
- Seed state from cache on mount → immediate first paint
- Background fetch updates state + writes cache
- **Perceived first paint: <50ms** (from cache) vs 685ms cold

### Fix 3: Frontend — localStorage SWR cache for Specs.tsx
Same pattern. `/specs` is 83ms so smaller win but consistent.

## Target
- Kanban cold first-ever load: ~685ms (no change, no cache yet)
- Kanban revisit: <50ms (from localStorage cache)
- Specs cold first-ever load: ~83ms (already fast; with backend fix even less)
- Specs revisit: <50ms (from localStorage cache)
- Perceived improvement on repeat navigation: >90%

## Files
- `api/routers/tasks.py` — skip readiness loop for closed tasks
- `app/src/pages/Backlog.tsx` — add localStorage SWR
- `app/src/pages/Specs.tsx` — add localStorage SWR
