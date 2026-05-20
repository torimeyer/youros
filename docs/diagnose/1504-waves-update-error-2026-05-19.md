# Diagnose: →1504 — Waves > Tasks update error

**Date:** 2026-05-19  
**Worktree:** agent-diagnose-1504-waves-u-9c7a820b

## Root cause (confirmed)

`PlanWavesPanel.tsx` (Tasks subtab) calls `GET /api/tasks` **without a status filter**.  
`ostk work list --json` returns **all 1250 tasks** (open + closed).  
**3 closed tasks have `title: null`** in the ostk data store.  
When the Tasks subtab renders, it calls `t.title.split('⊕')` — this throws
`TypeError: Cannot read properties of null (reading 'split')`, crashing React.

Reproduction:
1. Tasks page → click "Update waves" → PlanWavesPanel opens (Waves subtab)
2. Click "Tasks" subtab → `GET /api/tasks` returns all 1250 tasks, 3 with null titles
3. React crashes rendering `{t.title.split('⊕')[0].trim()}` — user sees an error

Evidence:
- `ostk work list --json` → 1250 tasks, 3 with null title (id →147 and 2 others), status=closed
- `ostk work list --json --status open` → 13 tasks, 0 null titles
- PlanWavesPanel.tsx line 134: `api.get<TasksResponse>('/tasks')` — no status filter
- PlanWavesPanel.tsx line 327: `{t.title.split('⊕')[0].trim()}` — crashes on null

## Fix

**File: `app/src/components/PlanWavesPanel.tsx`** (safe to edit)

1. **Line 134**: Change `/tasks` to `/tasks?status=open` — only 13 tasks, none null.  
   Also better UX: the Tasks subtab exists to let the user plan work, showing 1250 closed tasks is wrong.

2. **Line 327** (defense-in-depth): Change `t.title.split(...)` to `(t.title ?? '').split(...)`.

The mock in `PlanWavesPanel.test.tsx` `beforeEach` must also be updated:
- Change `path === '/tasks'` matcher to `path.startsWith('/tasks')` and exclude waves/assignments.

## Tests added

- Tasks subtab requests `/tasks?status=open` (not `/tasks`)
- Null-title task does not crash the component

## Status

- [x] Root cause identified  
- [x] Scaffold commit  
- [ ] Failing tests written (RED)  
- [ ] Fix implemented (GREEN)  
- [ ] Tests passing  
- [ ] Final commit
