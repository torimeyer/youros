# Diagnose: Waves > Tasks subtab error after clicking Update (→1504)

**Date**: 2026-05-19  
**Status**: Fixed  
**Commit**: see git log for `fix(waves): diagnose + fix Tasks subtab error after Update (→1504)`

## Symptom

Clicking the "Update waves" button on the Tasks page (or "Plan waves" when no
assignments exist yet) opens the Plan panel. Navigating to the Tasks subtab
inside that panel crashed with:

```
TypeError: Cannot read properties of null (reading 'split')
  at PlanWavesPanel.tsx:327
```

## Root cause

`GET /api/tasks` returns all tasks including closed ones. Closed tasks in
`~/.youros/issues.jsonl` can have `title: null` (the `close_reason` field carries
the relevant text instead). The Tasks subtab rendered:

```tsx
<p>{t.title.split('⊕')[0].trim()}</p>
```

Calling `.split()` on `null` throws immediately. Affects any closed task
missing a `title` field (3 found in production on 2026-05-19).

## Fix

One-character guard in `PlanWavesPanel.tsx` line 327:

```tsx
// before
{t.title.split('⊕')[0].trim()}

// after
{(t.title ?? '').split('⊕')[0].trim()}
```

## Regression test

`PlanWavesPanel.test.tsx` — new describe block "PlanWavesPanel — Tasks subtab":
- "renders without crashing when a task has a null title (→1504)": mocks `/tasks`
  with one normal task + two tasks with `title: null`/`undefined`, clicks Tasks
  subtab, asserts all rows render.
- "strips ⊕-suffix from task title in Tasks subtab": verifies the split still
  works correctly for non-null titles.

All 12 tests green. `tsc -b` clean.
