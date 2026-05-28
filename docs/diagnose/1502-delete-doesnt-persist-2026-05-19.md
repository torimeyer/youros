# →1502 Delete doesn't persist (kanban/backlog)

**Date:** 2026-05-19  
**Status:** FIXED — `api/services/ostk.py` `delete_task`, commit on worktree branch

## Summary

User clicks delete on a Task/task in the kanban or backlog UI. The row disappears optimistically but reappears on next refresh. `ostk work list --status open` still shows the Task.

## Reproduction

- Task →1477 ("Tell Scott about how I lost my friend group to White Castle")
- Click delete in kanban/backlog
- Row disappears for 5 seconds (undo window)
- On next `fetchTasks` poll (3s interval), the row reappears
- `ostk work list --status open | grep 1477` still returns the entry

## Root Cause

Two different data stores, one write, one read:

1. **`delete_task` in `api/services/ostk.py`** reads and edits `issues.jsonl` — the local JSONL file at `.ostk/Tasks/issues.jsonl`. This file only holds the ~22 most recently created tasks (it is written by the local daemon session, not the full historical store).

2. **`list_tasks`** calls `ostk work list --json` via socket/CLI, which reads the full daemon store — currently 1245 tasks spanning all of project history.

Any task that pre-dates the current `issues.jsonl` window (like →1477) is **not found** in the file. The old `delete_task` raised `OstkError("task not found")`, which the router translated to HTTP 404. The frontend's `deleteTask` function in `Tasks.tsx` calls `api.delete(...)` with a silent `.catch()` — the 404 is swallowed, the optimistic removal stays, but the next `fetchTasks` poll fetches the unchanged daemon store and the row reappears.

```
Frontend           Backend                         ostk stores
─────────          ───────                         ───────────
DELETE /tasks/1477
                   delete_task("→1477")
                     read issues.jsonl             ← ~22 recent entries only
                     "not found" → 404
.catch(() => {})   ← 404 silently swallowed
optimistic remove
         ... 3 seconds ...
fetchTasks()
                   list_tasks()
                     ostk work list --json         ← 1245-entry full store
                     →1477 still present
← task reappears
```

## Fix

`delete_task` now has a two-stage approach:

1. **Fast path**: attempt the direct `issues.jsonl` edit (unchanged — works for recently created tasks).
2. **Fallback**: if the task is not in `issues.jsonl`, call `await self._run("work", "close", task_id)`, which routes through the ostk socket/CLI and has full daemon store visibility.

Only raises `OstkError("not found")` if the CLI also reports the task is unknown.

**File changed:** `api/services/ostk.py` — `delete_task` method (~line 504)

## Evidence

Direct Python verification against the worktree's `OstkService`:

```
BEFORE: →1477 in open list: True
DELETE result: deleted →1477
```

The `_run("work", "close", "→1477")` path successfully closed the Task in the daemon store.

## Tests

Two tests in `api/tests/test_tasks.py`:

- **`test_ostk_delete_task_not_in_jsonl_falls_back_to_cli`** (new): confirms that a task absent from `issues.jsonl` triggers the CLI fallback path. RED before the fix, GREEN after.
- **`test_ostk_delete_task_missing_file_falls_back_to_cli`** (renamed from `test_ostk_delete_task_missing_file_raises`): updated to reflect new behavior — no `issues.jsonl` → CLI fallback, and only raises if CLI also fails.

Full suite: 5195 passed, 5 pre-existing failures on main (unrelated: `test_cache_honors_ttl`, two `test_speckit`, `test_specs_journey`).
