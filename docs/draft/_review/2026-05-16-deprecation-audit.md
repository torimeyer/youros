# Deprecation Audit — 2026-05-16

Reviewed by: deprecate-auditor-codebase-scan-6d2ebe
Safety constraint: HIGH PRECISION. Each candidate has at least one strong, verifiable signal.

## Summary

- 1 HIGH-confidence candidate
- 4 MEDIUM-confidence candidates
- 2 LOW-confidence (manual review)
- Total files scanned: 416 (228 Python, 188 TypeScript/TSX, non-test)
- Total needle references inspected: 213 in source (vs 1,250 closed needles)
- Deprecated markers found: 14 across the codebase
- Orphan-import checks run: all 112 api/services/*.py modules, all app/src/pages/*.tsx components

---

## HIGH-confidence candidates

### api/routers/onboarding.py:302 — deprecated `/onboarding/dream` endpoint

- Signals: explicit `# Deprecated` comment naming replacement, zero frontend callers, replacement fully implemented
- Evidence:
  ```python
  @router.post("/onboarding/dream", response_model=DreamResponse)
  async def dream(body: DreamRequest):
      # Deprecated: use /onboarding/intent instead. Kept so existing flows don't break.
  ```
  Replacement at line 365:
  ```python
  @router.post("/onboarding/intent", response_model=IntentResponse)
  async def intent(body: IntentRequest):
  ```
- Verification: searched all of `app/src/` for `onboarding/dream` — zero results. The replacement `/onboarding/intent` is actively called from `OnboardingWizard.tsx`. The `DreamResponse` and `DreamRequest` models are only referenced within `onboarding.py` itself and its test file.
- Closed needle: no needle reference in the comment, but the evidence is self-contained.
- Recommendation: remove the `/onboarding/dream` endpoint and its `DreamRequest`/`DreamResponse` models and related `_call_llm`/`_FIRST_RUNS_HINTS` logic if those are only used by `/dream`. Remove test coverage for `/dream` at the same time.

---

## MEDIUM-confidence candidates

### api/services/fcp_export.py — service module with zero production callers

- Signals: zero imports outside tests, one commit history, no router or endpoint references it
- Evidence: 397-line module providing `FCPSession`, `detect_transport`, `get_server_command`, and related helpers for spawning FCP MCP server subprocesses. Its entire public API is exercised only in `api/tests/test_fcp_export.py`.
  ```
  grep -rn "fcp_export" api/ frontend/ app/ (excluding tests and self)
  → 0 results
  ```
  Git history: one commit (4949eda, 2026-04-23) which fixed its test interface. No follow-up commit wiring it into a router or calling service.
- What this was for: the module header says it wraps FCP server sessions for one-shot file generation (sheets, slides, drawio, pdf). No FCP export endpoint exists in any router.
- Recommendation: file a needle to either (a) wire `fcp_export` into a new `/api/fcp/export` endpoint if the feature is still planned, or (b) delete the module and its test. Do not delete silently — the tests are the only evidence of the intended interface.

### api/services/agent_watchdog.py — stall-detection module never wired to production

- Signals: zero imports outside tests, commit added it to fix a specific bug but never integrated it
- Evidence: 155-line `AgentWatchdog` class that tracks transcript growth + PID liveness to detect stalled agents. Created in commit e96eba0 (2026-05-03) to fix the port-release-notes premature cancel.
  ```
  grep -rn "agent_watchdog|AgentWatchdog|WatchdogState" api/ (excluding tests and self)
  → 0 results
  ```
  The commit also modified `ghost_reaper.py` to accept a `transcript_resolver` kwarg — but `AgentWatchdog` was never called from `ghost_reaper`, `agents.py`, or the heartbeat loop.
- What this was for: detecting agent stalls by watching both transcript bytes and progress log mtime. The heartbeat shell hook (`heartbeat-and-drain.sh`) calls `heartbeat_idle.py` directly, not `AgentWatchdog`.
- Recommendation: file a needle to integrate `AgentWatchdog` into the heartbeat loop or document why `heartbeat_idle.py` alone is sufficient, then delete `agent_watchdog.py`.

### api/services/transcript_resolver.py — incomplete integration (TODO not done, needle closed)

- Signals: closed needle →1280, prominent TODO in module docstring for step that was not implemented
- Evidence:
  ```python
  # Integration note
  # The primary call site is inside routers.agents._resolve_transcript_source_uncached
  # (→1280 integration needle). ...
  # TODO (→1280 integration): wire resolve_transcript into
  # routers.agents._resolve_transcript_source_uncached as step 3d — after 3c's
  # worktree_path check — to handle agents whose worktree_path is no longer in
  # agent_metadata but whose transcript JSONL still exists on disk.
  ```
  Searching `agents.py` for `transcript_resolver` import: zero results. The function exists in `ghost_reaper.py` as an injectable callback but the `services.transcript_resolver.resolve_transcript` function is never passed as that callback in production code.
- Why MEDIUM not HIGH: the module is not entirely dead — it has tests and was intentionally designed as a "standalone import path." Needle →1280 being closed might mean the team decided step 3c was sufficient. But the TODO is still present and the module's primary purpose was never executed.
- Recommendation: either (a) implement step 3d by wiring `resolve_transcript` into `_resolve_transcript_source_uncached`, or (b) remove the TODO and add a comment explaining why step 3c is sufficient and this module is a reserve.

### app/src/pages/Team*.tsx — four frontend page components not reachable via routing

- Signals: zero imports in App.tsx, zero non-test references across the entire codebase
- Files: `TeamStart.tsx`, `TeamDashboard.tsx`, `TeamHome.tsx`, `TeamSettings.tsx`
- Evidence: App.tsx imports only the following team/admin pages: `admin/Overview`, `admin/Members`, `admin/Policies`, `admin/AuditTrail`, `admin/Security`. The four Team pages are not imported anywhere:
  ```
  grep -rn "TeamStart|TeamDashboard|TeamHome|TeamSettings" app/src/ (excluding tests and self)
  → 0 results for each
  ```
  Additionally, admin sub-pages `admin/Catalog.tsx`, `admin/OrgSettings.tsx`, `admin/StarterPack.tsx`, `admin/TeamAdoption.tsx` are also absent from App.tsx and have no non-test callers.
- Why MEDIUM not HIGH: these could be features that are built but not yet wired up (intentionally staged), or they could be enterprise-only pages that ended up in main. Without that context, deletion would be risky.
- Recommendation: confirm with Tori whether these are (a) planned but not yet wired into routing, (b) enterprise-only features belonging only on the `nr-enterprise` branch, or (c) abandoned. If (c), delete all eight components and their test files.

---

## LOW-confidence (manual review only)

### app/src/pages/Adoption.tsx — page component whose route now redirects away from it

- Signals: the `/adoption` route in App.tsx is a `<Navigate to="/settings" replace />`, meaning `Adoption.tsx` is never rendered
- Evidence:
  ```tsx
  // App.tsx line 190
  <Route path="adoption" element={<Navigate to="/settings" replace />} />
  ```
  `Adoption.tsx` is not imported in App.tsx. References in the codebase are string-level only (a path string in `UniversalSearch.tsx` pointing to `/adoption`, and an `AdoptionSummary` interface in `TeamHome.tsx`, which is itself not wired up).
- Why LOW: `TeamHome.tsx` has an `AdoptionSummary` type and a reference to adoption data — these might be revived together. The redirect might be temporary.
- Recommendation: ask whether the Adoption page was intentionally removed. If yes, delete `Adoption.tsx` and `Adoption.test.tsx` and the `AdoptionSummary` interface in `TeamHome.tsx`.

### Dashboard.tsx:486 — leftover guide comment for removed `cardClass` variable

- Signals: "cardClass replaced by Card component" comment with no surrounding code
- Evidence:
  ```tsx
  // app/src/pages/Dashboard.tsx line 486
  // cardClass replaced by Card component. Use: <Card hover padding="sm" className="sm:p-6">
  ```
  `cardClass` does not exist anywhere in the file. The `Card` component is already in use throughout Dashboard.tsx. This is a dangling comment from a refactor.
- Why LOW: a comment, not dead code. No functional impact.
- Recommendation: delete the comment. One-line change, safe.

---

## Skipped patterns

- Naming-pattern matches (`_old`, `_legacy`, `_v1`): not searched (skipped per spec)
- Aged TODO/FIXME: not individually flagged (skipped per spec)
- Needle references in source code: all 213 references traced. Most are historical context ("fix introduced because →NNN"). The only actionable closed-needle workaround pattern that passed the "does the code below look like a workaround that closing the needle made unnecessary?" test is the `transcript_resolver.py` TODO, covered above.

---

## Methodology notes

**Signal 1 (closed-needle workarounds):** extracted all 213 `→NNN` references from non-test Python and TypeScript source files, cross-referenced against 1,250 closed needles, and searched for workaround language (`workaround`, `hack`, `temp`, `TODO.*→`, `until.*→`, `once.*fixed`) near each reference. Very few hits; most needle comments are historical attribution, not workaround flags. Only `transcript_resolver.py` and the `→999` auto-merge comment showed meaningful context, and the auto-merge code is the fix itself, not a leftover workaround.

**Signal 2 (deprecated/superseded markers):** searched 14 occurrences of `deprecated`, `superseded by`, `replaced by`, `obsoleted by` across non-test files. Most were prose in comments explaining prior approaches that had already been removed. Only `/onboarding/dream` had a live deprecated endpoint with a named replacement.

**Signal 3 (orphaned imports):** checked all 112 `api/services/*.py` modules for callers outside tests. Found two with zero non-test callers: `fcp_export.py` and `agent_watchdog.py`. Verified with broader searches (string matches for class/function names, shell hook references) — `heartbeat_idle.py` is called by shell hooks directly, ruling out `agent_watchdog.py` being used that way. Checked 50+ app/src/pages components against App.tsx; four Team pages have zero wiring.

**Signal 4 (replacement comments):** searched for "previously", "old approach", "old code" patterns. No significant hits beyond regression-test comments describing past bugs.

**What I would search next if scope allowed:**
- Dynamic `__import__`/`importlib` patterns that could hide real callers of the orphaned services
- Shell scripts and hook files for any invocations of `fcp_export` or `agent_watchdog`
- Git blame to confirm the Team pages have never been in App.tsx (vs. removed recently)
