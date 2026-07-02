# Phase C: Cross-session file conflict alerts

## Design decision: data source

Chose **option (b)** — compute conflicts from the per-session event files in
`.ostk/sessions/*/events.jsonl`, the same data source Phase B already uses
for `recent_files` enrichment.

Rejected option (a) (gen-table reader via `ostk gen list`) because:
- `ostk gen list` produces human-readable output only; no `--json` flag confirmed.
- No existing reader of this data in `api/`; would require a new subprocess-based
  reader with fragile text parsing.
- The session event files already carry every file-write event (tool, path,
  timestamp, seat), making conflict detection computable with zero new I/O
  primitives.

## Conflict rule

Two distinct sessions writing the same path within `CONFLICT_WINDOW_MINUTES = 15`
of each other. Same-session self-conflicts are excluded. Non-write tools excluded.

## Data flow

`_detect_file_conflicts(sessions_dir, now)` → called inside
`_gather_sessions_and_events_sync` (already runs in asyncio.to_thread) → added
to coordination snapshot as top-level `conflicts` list → same `/sessions/coordination`
endpoint consumed by Agents page and Sessions page.

## Surface

- Conflict strip above the agents list in Agents page Active Sessions section
- Same strip in Sessions page (rendered above the existing 3-column grid)
- Informational only — never blocks anything (hard product rule)

## Files touched

Backend:
- `api/routers/sessions.py` — add `_detect_file_conflicts`, update snapshot
- `api/tests/test_gen_conflicts.py` — new unit tests for detection logic
- `api/tests/test_sessions_router.py` — add `conflicts` field tests

Frontend:
- `app/src/pages/Sessions.tsx` — add ConflictsStrip
- `app/src/pages/Sessions.test.tsx` — new component test
- `app/src/pages/Agents.tsx` — add conflict state + ConflictsStrip
- `app/src/pages/Agents.test.tsx` — add conflict strip test
