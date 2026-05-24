# →1501 — kanban cards clickable to detail
## Root cause
Kanban cards in Backlog.tsx used `cursor-pointer` styling but had no navigation — only the chevron expand button was interactive. Nothing linked to the task/spec detail pages.

## Changes
- **Backlog.tsx**: Added `import { Link } from 'react-router-dom'`. Wrapped TaskCard content in `<Link to="/tasks?focus={id}" data-testid="task-card-link">` and SpecCard content in `<Link to="/specs?focus={encoded-path}" data-testid="spec-card-link">`. Added `e.stopPropagation()` to the Build button so it doesn't also navigate.
- **Specs.tsx**: Added `useSearchParams`, `focusParam`, `specRowRefs`, and a `useEffect` that scrolls the matching spec card into view and briefly highlights it with `ring-2 ring-emerald-400` when `?focus=<path>` is in the URL. Wired `ref` on each spec row div.

## Tests added
- Backlog.test.tsx: 2 tests — TaskCard link href, SpecCard link href
- Specs.test.tsx: 1 test — scrollIntoView called when ?focus param matches a spec path

All 94 tests pass.
