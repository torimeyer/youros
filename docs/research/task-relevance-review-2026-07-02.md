# Task Relevance Review — 2026-07-02

**Reviewed by:** agent `review-all-open-tasks-relevance-0b1e89`
**Date:** 2026-07-02
**Scope:** All 198 open tasks (via `GET /api/tasks?include_session_tasks=true&include_ac_children=true`)
**Method:** API + file reads; git sandboxed in worktree (ran `GIT_DIR` checks via kernel audit)
**Provenance note:** re-materialized to main by the orchestrator from a full read of the agent's report after its worktree was deleted pre-merge (same data-loss incident as the spec review; see bug task). Content below is the agent's report as read at 2026-07-02T20:30Z, unchanged except this note and the post-approval status updates marked [UPDATE].

---

## Summary

| Bucket | Count |
|--------|-------|
| STILL RELEVANT | 139 |
| ALREADY DONE (closed this session) | 2 |
| DUPLICATES CLOSED | 0 |
| STALE — NEEDS HUMAN CONFIRMATION | 57 |
| **Total** | **198** |

[UPDATE 2026-07-02T20:36Z: user approved archiving all 57 stale tasks; executed via close endpoint, ARCHIVED_OK=57 FAILED=0. Open count dropped 203→147.]

**6 new tasks** (count went 192→198 around 2026-07-02T20:15Z): →2451–→2456, all from the just-promoted `mission-control-for-live-sessions.md` spec. Confirmed new; do not close.

---

## ALREADY DONE — Closed This Session (2 tasks)

Both closed via `POST /api/tasks/{id}/close?source=user` with `reason=completed`.

| Task | Title (truncated) | Evidence |
|------|-------------------|----------|
| **→2337** [P1] | Release smoke test silently skips browser journey checks | `scripts/e2e_smoke.sh:1782` — check uses `${API_BASE%%:*}://localhost:${FRONTEND_PORT:-3010}` which extracts the scheme (http/https) from `API_BASE`. When SSL certs are present, API_BASE is `https://...` so the frontend reachability check correctly uses https. Fix was already in place. |
| **→2323** [P2] | S014 unbuilt AC: add reminders_today to /api/briefing response | `api/routers/briefing.py:130-159` — `_get_reminders_today()` helper exists (line 33), called at line 130, and included in all response paths (lines 138, 151, 158). Code comment at line 129 explicitly cites `AC17/18`. Already done. |

---

## STILL RELEVANT — Regular Tasks (4 open, prioritized work)

| Task | Priority | Title | Relevance note |
|------|----------|-------|----------------|
| **→2298** | P1 | Plug first-run coverage holes: wrong Claude auth command + missing items | No evidence of fix found in codebase. Still relevant. [UPDATE: fix agent spawned 2026-07-02T20:36Z] |
| **→2450** | P1 | Act on hook deletion provenance: restore 2, commit 2 | [UPDATE: incorrect — →2450 was closed at ae031e5f per the v5.11 session handoff; the API open-list confirms 0 open rows for →2450. Agent misread a stale row.] |
| **→2297** | P2 | Surface orphaned in_progress tasks with no running agent or session link | Feature not found in `api/routers/tasks.py` or `task_audit.py`. Tasks health endpoint uses `ostk.refine_tasks()` which doesn't surface orphaned-in-progress issues. Still needed. [UPDATE: fix agent spawned 2026-07-02T20:36Z] |
| **→2423** | P2 | Fix test_drive_auth_url_return_to_uses_frontend_url: expects 8000 but auth returns frontend URL | Searched all of `api/tests/test_drive.py` — function does not exist. Test still needs to be created. Still relevant. [UPDATE: fix agent spawned 2026-07-02T20:36Z] |

---

## STILL RELEVANT — AC Children from Active Specs (135 tasks)

Spec-derived AC child tasks (`spec_ref` set, no `priority`). Not closed because spec completion is managed by the spec driver. Grouped by spec.

- **pattern-watcher.md — 13 tasks (→1827–→1839).** Write layer (v1) exists in `api/services/pattern_watcher.py` with tests; UI panel and resilience ACs not confirmed done. Spec active.
- **1652-diagnosis.md — 4 tasks (→1840–→1843).** pattern_watcher teardown bug-fix ACs. Active.
- **team-mode-plan.md — 27 tasks (→1844–→1871).** Services exist (`enterprise_store.py`, `teams.py`, `team_catalog.py`) but team mode unshipped; many ACs are open questions. Actively unfinished.
- **adopt-claude-code-s-good-ideas — 44 tasks (→1873–→1917).** `runtime_provider.py` exists but its own docstring defers live spawn wiring to →1895. In progress.
- **pattern-watcher-v2.md — 1 task (→2037).** Placeholder pending v1. [UPDATE: spec archived by spec review as OBSOLETE (self-deferred DECISION).]
- **cross-source-search — 16 tasks (→2082–→2103).** Core implemented (`cross_search.py`, `excerpts.py`, `embeddings.py`, tests); live-integration and e2e ACs remain.
- **done-means-all-acceptance-criteria-reviewed.md — 5 tasks (→2231–→2234).** Active.
- **remind-me — 3 tasks (→2228–→2230).** Routers/services/tests exist; slash command, notifications, activity-feed ACs need verification.
- **executive-summary-jira — 3 tasks (→2220–→2222).** Live Jira integration required to verify. Active.
- **backend-event-loop-wedge — 3 tasks (→2224–→2226).** Scan timeout/backoff ACs; not verified in agents.py. Relevant.
- **guided-google-self-setup.md — 10 tasks (→2285–→2294).** OAuth self-setup wizard ACs. Active.
- **mission-control-for-live-sessions.md — 6 tasks (→2451–→2456) ⭐ NEW.** Do not close. [UPDATE: phase A (prewarm) merged as 40a418b7 and pushed.]

---

## STALE — 57 tasks [UPDATE: all archived with user approval, ARCHIVED_OK=57 FAILED=0]

### A. Journey ID e2e test artifact specs (55 tasks)
Evidence: spec filenames `e2e-specs-journey-id*.md` created by test runs, no `journey_id` anywhere in product code, 20+ specs all describing the same feature, paths span the myos→youros rename eras.

→2059, →2062, →2071, →2072, →2073, →2075, →2204, →2213, →2214, →2215, →2248, →2249, →2250, →2270, →2271, →2272, →2282, →2283, →2284, →2308, →2309, →2310, →2320, →2321, →2322, →2334, →2335, →2336, →2347, →2348, →2349, →2359, →2360, →2361, →2371, →2372, →2373, →2383, →2384, →2385, →2395, →2396, →2397, →2408, →2409, →2410, →2420, →2421, →2422, →2435, →2436, →2437, →2447, →2448, →2449

### B. Debug/probe test artifacts (2 tasks)
→2217 (`debug-probe-draft-test.md`, task title literally "check"), →2173 (`direct-test-1780595646.md`; if model-choice persistence is wanted, re-file properly).

Root cause follow-up: task filed to make e2e runs clean up the spec files they create (prevents recurrence).

---

## Notes on what was NOT checked
- No pytest/vitest run performed; code/test existence verified, outcomes not.
- Live integration ACs (→2102, →2103, →2220–→2222) require the running app against real Slack/Jira.
- Frontend completeness for pattern-watcher panel and guided-google-setup UI not read.

## Verified against the codebase (agent's receipts)
| Claim | File:line |
|-------|-----------|
| →2337 done: browser check uses correct scheme | `scripts/e2e_smoke.sh:1782` |
| →2323 done: reminders_today in briefing | `api/routers/briefing.py:130-159` |
| →2423 still open: test function missing | `api/tests/test_drive.py` — grep returned 0 matches |
| →2297 still open: no orphaned-task health check | `api/routers/tasks.py:424-435` |
| cross-source: router + tests exist | `api/routers/cross_search.py:1` + test files |
| pattern-watcher: write layer exists | `api/services/pattern_watcher.py:1` |
| runtime_provider: not fully wired | `api/services/runtime_provider.py:32-34` docstring |
| journey_id: not in codebase | `api/` search, 0 product-code matches |
| mission-control tasks are the 6 new ones | `spec_ref` confirmed on →2451–→2456 |
