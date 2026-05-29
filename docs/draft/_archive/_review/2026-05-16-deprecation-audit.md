# Deprecation Audit — 2026-05-16

Reviewed by: deprecate-auditor-codebase-scan-6d2ebe
Safety constraint: HIGH PRECISION. Each candidate has at least one strong, verifiable signal. Ambiguous cases skipped.

## Summary

- 0 HIGH-confidence candidates
- 3 MEDIUM-confidence candidates
- 1 LOW-confidence (manual review)
- Total files scanned: 545 Python, 331 TypeScript
- Total closed-Task references inspected: 50+, explicit deprecated markers: 4 files, orphan import check: all api/services/*.py

---

## HIGH-confidence candidates

None found. The codebase has no cases where multiple converging signals (closed Task + explicit deprecated marker + zero callers) all align.

---

## MEDIUM-confidence candidates

### `api/routers/onboarding.py:304-309` — Deprecated `/onboarding/dream` endpoint with zero frontend callers

- **Signals:** Explicit "Deprecated" comment in code; replacement endpoint confirmed present and active; no frontend callers found
- **Evidence:**
  ```python
  # api/routers/onboarding.py:304
  # Deprecated: use /onboarding/intent instead. Kept so existing flows don't break.
  ```
  The replacement `POST /onboarding/intent` is live at line 365 and actively called by `app/src/components/OnboardingWizard.tsx:715`. Zero calls to `/onboarding/dream` in `app/src/`. The deprecated route also pulls in `DreamRequest`, `DreamResponse`, `_build_user_message`, and `_fallback_response` helpers (lines 22-216) that are only used by this one endpoint.
- **Tests:** `api/tests/test_onboarding.py` still exercises the deprecated path (lines 49-230). Those tests would need to be removed or replaced with intent-endpoint tests.
- **Recommendation:** Remove the `dream` endpoint, its Pydantic models, and helper functions. Update `test_onboarding.py` to drop the `/dream` test class. No frontend changes needed.

---

### `api/routers/agents.py:5729-5840` — `POST /agents/fleets/spawn` endpoint marked deprecated

- **Signals:** Explicit "Deprecated" docstring; frontend fleet panel removed with comment confirming the move; backend emits a warning log on every call
- **Evidence:**
  ```python
  # api/routers/agents.py:5736-5743
  """Spawn all members of a fleet template as parallel agents.
  ...
  Deprecated: fleet launching has been folded into the Plans page
  template grid. This endpoint is kept alive for backwards compatibility
  with existing callers but new UI should use
  POST /api/specs/from-template instead.
  """
  logger.warning("spawn_fleet called; fleets are folded into plan templates; ...")
  ```
  `app/src/pages/Agents.tsx:2550`: "Fleets panel removed. The backend /agents/fleets/* endpoints stay alive for backwards compatibility, but the Plans page template grid is the new home for team-style starter templates."
  The `GET /agents/fleets` and `POST /agents/fleets/{id}/prewarm` siblings are also present but NOT marked deprecated.
- **Caveat:** The backwards-compat note means there may be external or script-level callers outside the scanned codebase. The warning log would make it visible if the endpoint is hit in production.
- **Recommendation:** Check server logs for recent `/agents/fleets/spawn` calls before removing. If zero hits, remove `spawn_fleet`, `FleetSpawn` model, and the fleet-spawn test coverage in `api/tests/test_agent_templates.py:1378` and `api/tests/test_agents.py:9254`. The sibling `list_fleets` and `prewarm_fleet` endpoints are NOT candidates.

---

### `api/services/comprehensive_build.py` — Wrapper module never imported; build_queue.py does the work directly

- **Signals:** Zero import references from any router or service file; `api/routers/agents.py` directly calls `build_queue.try_start_build` and `build_queue.finish_build` instead of going through this wrapper
- **Evidence:**
  ```python
  # api/services/comprehensive_build.py (full file)
  # Module exports: start_comprehensive_build(), on_build_complete()
  # These functions wrap build_queue.try_start_build / finish_build
  ```
  Verified: `grep` across all `.py`, `.ts`, `.tsx`, `.sh` files finds zero callers outside the file itself. The only external mention is a test function *name* (`test_task_specific_locks_allow_parallel_comprehensive_builds` in `test_spawn_locks.py`) which references the concept, not the module.

  Meanwhile, `agents.py` directly imports `build_queue`:
  ```python
  # api/routers/agents.py:4555
  from services.build_queue import try_start_build as _try_start_build
  # api/routers/agents.py:5127
  from services.build_queue import finish_build as _finish_build
  ```
  The triage doc at `docs/worktree-triage-2026-05-05.md:70` confirms both modules were scaffolded together under Task →970, but `comprehensive_build.py` was never wired up.
- **Recommendation:** Remove `api/services/comprehensive_build.py`. No callers to update. Verify by running `grep -r "comprehensive_build" api/` before deleting.

---

## LOW-confidence (manual review only)

### `app/src/pages/Dashboard.tsx:486` — Orphaned migration note for `cardClass`

- **Signals:** Single comment referencing a variable (`cardClass`) that does not exist anywhere in the file
- **Evidence:**
  ```typescript
  // app/src/pages/Dashboard.tsx:486
  // cardClass replaced by Card component. Use: <Card hover padding="sm" className="sm:p-6">
  ```
  `grep -n "cardClass" Dashboard.tsx` returns only this comment line. The variable was removed; the comment was left behind.
- **Confidence:** LOW because it is just a stale comment, not runnable code. No production risk.
- **Recommendation:** Delete the one comment line. 5-second cleanup.

---

## Skipped patterns

- **Naming-pattern matches** (`_old`, `_legacy`, `_v1`, etc.): 12 found, skipped per spec (too many false positives in variable names and log strings)
- **Aged TODO/FIXME/HACK:** 6 found, skipped per spec (intentional reminders, not dead code)
- **Closed-Task references as traceability annotations:** ~50 inspected. All Task references in `agents.py` are traceability comments explaining *why* a line exists, not workarounds awaiting removal. The referenced Tasks are closed because their fixes are already in the code.

---

## Methodology notes

**What I searched:**

1. Task references (`→NNNN` pattern) in comments across `api/` and `app/src/` — confirmed all referenced Tasks are closed, then checked whether surrounding code looked like a surviving workaround. Found none: all are traceability annotations.

2. Explicit deprecated/superseded/replaced markers — searched for `deprecated`, `superseded by`, `replaced by`, `obsoleted by` in comments and docstrings. Found 4 relevant hits (onboarding, agents fleet, atlassian scope note, model deprecation handler). Atlassian and model-deprecation hits are about external API changes, not internal code.

3. Orphan Python service imports — checked every `api/services/*.py` for zero references elsewhere. Initial pass found 9 zero-ref modules; broader search (including string matches, scripts, config) reduced real candidates to 1 (`comprehensive_build.py`). Others are used via dynamic imports, different naming, or are legitimately self-contained.

4. Replacement comments (`previously`, `old approach`, `before this`, `instead of`) — found 20 matches; all are explanatory annotations for current code decisions, not pointers to surviving old code.

**What I excluded:**

- TypeScript page/component orphan scan was abandoned after finding it produces ~180 false positives (pages are router-mounted, not imported by other files; stores are imported by hook name, not module path). A proper TS dead-code scan requires running the TypeScript compiler with `noUnusedLocals` or a dedicated tool like `ts-prune`.

**What to check next if signals were broader:**

- Run `ts-prune` on `app/src/` for true TypeScript dead-export detection.
- Grep server access logs for recent hits to `/agents/fleets/spawn` to confirm it is safe to remove.
- Check whether `api/services/fleet_templates.py` itself is still needed after `spawn_fleet` is removed, or whether `list_fleets` and `prewarm_fleet` still justify it.
