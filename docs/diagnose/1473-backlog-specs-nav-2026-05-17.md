# →1473 Backlog Specs sub-tab nav disappears

**Date:** 2026-05-17
**Status:** FIXED (see commit history)

## Root cause

`Specs.tsx` (line 924) and `Tasks.tsx` (line 1656) each render `<TopBar>` as their first child. `TopBar` is a `position: fixed; top-0; z-40` header spanning the viewport width (minus sidebar). When either component is rendered **embedded** inside `Backlog.tsx` as a sub-tab, that fixed header overlays the top of the viewport — visually covering the Backlog `All / Specs / Tasks` nav row. The nav remains in the DOM but is hidden underneath the TopBar.

**One-liner:** Embedding full-page Specs/Tasks components inside Backlog causes their fixed TopBar to cover the Backlog nav row.

## Evidence

- `TopBar.tsx`: `className="fixed top-0 left-0 lg:left-56 ... z-40"` — fixed, viewport-anchored
- `Specs.tsx:924`: `<TopBar title="Specs" />` unconditionally rendered
- `Tasks.tsx:1656`: `<TopBar title="Tasks" />` unconditionally rendered
- `Layout.tsx`: No `pt-14/pt-16` on `<main>` — pages own their own TopBar padding
- `Backlog.tsx`: Embeds both as `{isSpecs && <Specs />}` / `{isTasksTab && <Tasks />}` with no suppression

## Fix

Added `embedded?: boolean` prop to `Specs` and `Tasks`. When `embedded` is true, `TopBar` is not rendered and the `pt-16/pt-20` top padding (which existed only to clear the TopBar) is removed. `Backlog.tsx` passes `embedded` to both.

## Files changed

- `app/src/pages/Specs.tsx` — add `embedded` prop, conditional TopBar + padding
- `app/src/pages/Tasks.tsx` — add `embedded` prop, conditional TopBar + padding
- `app/src/pages/Backlog.tsx` — pass `embedded` to `<Specs>` and `<Tasks>`

## Regression test

`app/src/pages/__tests__/Backlog.test.tsx` — asserts no `role=banner` (TopBar) renders when Specs/Tasks tabs are active inside Backlog.
