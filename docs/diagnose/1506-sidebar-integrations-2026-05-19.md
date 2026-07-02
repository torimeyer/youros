# →1506 Sidebar Integrations Grouping

**Date**: 2026-05-19  
**Author**: build-1506-sidebar-integrations-retry-1  
**Status**: COMPLETE

## Summary

Restructure the sidebar so that the two existing collapsible groups (Files & Docs, Comms) plus the top-level Gems item are consolidated under a single "Integrations" parent section.

## Before (post →1489)

Top-level items: Home, Tasks, Specs, Agents, Kanban view, Gems  
Groups:
- "Files & Docs" (`files`): Docs (`/files`), ostk, Drive
- "Comms" (`comms`): Gmail, Calendar, Messages, Slack, GitHub, Jira, Confluence

## After (→1506)

Top-level items: Home, Tasks, Specs, Agents, Kanban view  
Groups:
- "Integrations" (`integrations`, collapsible): Gems, Docs, ostk, Drive, Gmail, Calendar, Messages, Slack, GitHub, Jira, Confluence

## Changes Made

### Sidebar.tsx
- `TOP_LEVEL_ROUTES`: removed `/gems`
- `NAV_GROUPS`: replaced `files` + `comms` with single `integrations` group
- `ALL_NAV_ITEMS`: removed explicit Gems entry (now comes from NAV_GROUPS flatMap)
- `topLevelItems` sort order: removed `/gems`
- `SidebarGroup` button: added `aria-expanded` and `aria-controls` attributes
- `SidebarGroup` items div: added `id` for `aria-controls` reference

### Sidebar.test.tsx
- `expandAllGroups()`: updated to expand `integrations` instead of `files` + `comms`
- Updated grouped nav tests: all `comms`/`files` group IDs → `integrations`
- Updated `'two group headers are rendered'` → `'one group header rendered: integrations'`
- Updated Gems tests: no longer a top-level item
- Added new Integrations-specific tests (auto-expand, aria-expanded, items not top-level)

## Accessibility Decisions

- `aria-expanded={!collapsed}` on the toggle button: reflects open/closed state for screen readers
- `aria-controls="group-items-{id}"` on toggle, `id="group-items-{id}"` on items container
- Enter/Space already work via the `<button>` element natively
- Full arrow-key navigation within expanded sub-items NOT implemented (not required by spec)

## Test Results

### Vitest (2026-05-19)

```
 RUN  v4.1.3 /Users/you/claude/torios/app

 Test Files  2 passed (2)
      Tests  121 passed (121)
   Start at  21:35:39
   Duration  1.45s

=== EXIT 0 ===
```

### TypeScript

```
npx tsc -b → exit 0, no errors
```

All 121 tests pass, zero TypeScript errors.
