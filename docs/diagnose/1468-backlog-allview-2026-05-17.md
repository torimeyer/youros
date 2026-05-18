# Diagnose →1468: Backlog AllView crash — `tasks.filter is not a function`

**Date:** 2026-05-17  
**Branch:** worktree-agent-diagnose-1468-backlog-76d1da1e  
**File:** app/src/pages/Backlog.tsx line 43

## Error

```
TypeError: tasks.filter is not a function
at AllView (Backlog.tsx:43:33)
```

## Root cause

`AllView` fetches from two endpoints and passes the raw response directly into state:

```tsx
api.get<Spec[]>('/specs').then(setSpecs).catch(() => {})
api.get<Task[]>('/tasks').then(setTasks).catch(() => {})
```

But the real API responses are:

- `GET /api/tasks` → `{ tasks: [...], total: N, ... }` (object, NOT array)
- `GET /api/specs` → `{ docs: [...] }` (object, NOT array)

So after the fetch resolves, `tasks` state holds `{ tasks: [...] }` — an object.
`tasks.filter(...)` at line 43 crashes because objects don't have `.filter`.
`specs.flatMap(...)` at line 42 would also crash for the same reason.

The crash at line 43 (not 42) because `/tasks` resolves before `/specs` in practice,
triggering a re-render where `tasks` is already the object but `specs` is still `[]`.

## Evidence

- Live API check: `GET /api/tasks` returns `{"tasks":[...]}` confirmed
- Live API check: `GET /api/specs` returns `{"docs":[...]}` confirmed
- `Tasks.tsx:381-382`: uses `api.get<TasksResponse>("/tasks")` then `res.tasks ?? []`
- `Specs.tsx:562-564`: uses `api.get<SpecsResponse>("/specs")` then `data.docs || []`
- Existing `Backlog.test.tsx` mocks with arrays (wrong shape) so tests pass despite broken code

## Fix plan

1. Add a RED test in `Backlog.test.tsx` that mocks with real API shapes — should fail now
2. Change `AllView` fetches to extract the array from each response (matches Tasks.tsx / Specs.tsx pattern)
3. Re-run tests — should go GREEN

## Fix (minimal, matches codebase pattern)

```tsx
// Before (wrong — raw response object passed to state)
api.get<Spec[]>('/specs').then(setSpecs).catch(() => {})
api.get<Task[]>('/tasks').then(setTasks).catch(() => {})

// After (correct — extract array, match Tasks.tsx and Specs.tsx pattern)
api.get<{ docs: Spec[] }>('/specs').then(r => setSpecs(r.docs ?? [])).catch(() => {})
api.get<{ tasks: Task[] }>('/tasks').then(r => setTasks(r.tasks ?? [])).catch(() => {})
```
