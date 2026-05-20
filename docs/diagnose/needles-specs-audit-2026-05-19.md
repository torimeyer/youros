# Needles + Specs Audit — 2026-05-19

Reviewed: 13 open needles + 8 active specs.
Cross-checked against codebase and git log (last 40 commits on main).

---

## Summary

- **Total reviewed:** 13 needles + 8 specs
- **Recommended actions:** 7 keep, 3 modify (blocker cleared), 3 modify (spec update needed),
  1 delete, 0 combine
- **Active worktrees in flight:** →1517/1518/1519/1521 (one agent), →1523 (one agent) —
  do not touch those needles until the worktrees land.

### Top 5 highest-value cleanups (do these first)

1. **Remove the stale "Blocked by Phase 1" note from →1425, →1426, →1427.**
   Phase 1 backend landed as commit `245138f`. All three needles still say blocked. They are
   ready to pick up now.

2. **Update `mychat-claude-code-parity.md` spec to mark 6 of 7 sections shipped.**
   Slash commands, plan mode, todo panel, audit/undo, background work, and receipts gate are all
   in main. Only AskUserQuestion (Section 5) remains. The spec still reads like nothing is done.

3. **Close →1511.** The BDD invariant test for Rule 2c is already wired (confirmed in
   `api/tests/test_narrative_v2.py` and `api/tests/test_saa_brief.py`). There is nothing left
   to do; an open needle creates false signal.

4. **Update `spec-auto-status.md` progress markers.** Phase 1 (commit `245138f`), Phase 2a
   (commit `35e7479`) are done. The spec reads like nothing shipped.

5. **Update →1441 spec reference.** The needle points to
   `docs/spec/narrative-coordination-intel-themes-main-only.md`, which is now in archive.
   Theme C MVP (→1440, commit `f627bd0`) also landed, clearing the stated blocker.

---

## Needles

### →1512 [P1] Spec-drawer hygiene — stage as state + shipped/husk detectors

- **Status:** KEEP
- **Evidence:** Full spec at `~/.myos/specs/spec-drawer-hygiene-stage-as-state.md`, created
  2026-05-19. No implementation files found (`api/services/spec_audit.py` does not exist).
  Not started.
- **Recommendation:** This is the highest-priority needle and its spec is detailed and ready.
  Build it.

---

### →1425 [P2] Spec-auto-status Phase 2b — agent self-register claim path

- **Status:** MODIFY
- **Evidence:** The needle says "Blocked by: Phase 1 backend." Phase 1 landed in commit
  `245138f` (feat: add spec-claims registry + /claim endpoint). The `/api/specs/{path}/claim`
  endpoint exists and is live in `api/routers/specs.py`.
- **Recommendation:** Remove the "Blocked by" note. This is unblocked and ready to build.

---

### →1426 [P2] Spec-auto-status Phase 2c — in-chat slash handler `/build-spec`

- **Status:** MODIFY
- **Evidence:** Same as →1425. Phase 1 done (commit `245138f`). Slash command infrastructure
  landed in `→1394` (commit `70b140a`). Both stated blockers are now resolved.
- **Recommendation:** Remove the "Blocked by" note. Ready to build.

---

### →1441 [P2] Theme C v2 — Intel digest + spec promotion

- **Status:** MODIFY
- **Evidence:** The needle says "Blocked by Theme C MVP." Theme C MVP (→1440) landed in commit
  `f627bd0`. The needle's spec reference (`docs/spec/narrative-coordination-intel-themes-main-only.md`)
  is now in archive. The secondary blocker — "intel-synthesize skill landing in plugin
  marketplace" — remains unresolved (no evidence that skill shipped).
- **Recommendation:** Update the spec reference to the archive path. Note that Theme C MVP
  unblocked this, but the intel-synthesize skill dependency is still outstanding. Partial unblock.

---

### →1465 [P2] Plan Waves backend pieces — spawn-preflight + builder-prompt recheck

- **Status:** KEEP
- **Evidence:** Frontend rebuild confirmed done (commit `bd04785`, →1461). Searched
  `api/routers/agents.py` for `spawn_preflight` — not found. The two backend deliverables
  (GET `/api/agents/spawn-preflight` + builder-prompt recheck instruction) do not exist yet.
- **Recommendation:** Still valid. Frontend is done; backend is the remaining work.

---

### →1513 [P2] Upstream ostk: drop `docs/spec/` as promote target

- **Status:** MODIFY
- **Evidence:** `docs/spec/` directory does not exist in this repo. Locally the problem is
  already moot. The needle's value is an upstream request to Scott's ostk binary so the
  `ostk doc promote` command stops accepting that path. That's a separate repo change, not
  a myOS change.
- **Recommendation:** Reword to clarify this is an upstream ostk request, not a myOS build
  task. Lower urgency since the directory doesn't exist locally.

---

### →1517 [P2] Connect step skipped when provider detected — Atlassian/GitHub not shown

- **Status:** KEEP
- **Evidence:** Bug confirmed in `app/src/components/OnboardingWizard.tsx:146`:
  `if (detectedProvider !== null && n === connectIdx) n = Math.min(n + 1, STEPS.length - 1)`
  — this skips the *entire* Connect step when any provider is detected, meaning the
  Atlassian and GitHub setup cards (lines 1230–1346) are never shown.
  An agent worktree (`agent-1517-1518-1519-1521-o-2692cb22`) is actively working on this.
- **Recommendation:** Let the active worktree finish. Do not touch.

---

### →1523 [P2] Tasks page: add "Sort by wave" option

- **Status:** KEEP
- **Evidence:** Active worktree `agent-1523-tasks-page-sort-0f526fb4` is modifying
  `app/src/pages/Tasks.tsx` and related files right now (gen=6 as of audit time).
- **Recommendation:** Let the active worktree finish. Do not touch.

---

### →1427 [P3] Spec-auto-status Phase 3 — passive git-commit detection

- **Status:** MODIFY
- **Evidence:** Same Phase 1 done evidence as →1425/→1426 (commit `245138f`). Needle still
  says "Blocked by: Phase 1 backend."
- **Recommendation:** Remove the "Blocked by" note. This is now unblocked. Lower urgency
  than Phases 2b/2c.

---

### →1511 [P3] Verify Rule 2c (BDD invariant test) stays wired in subagent prompt

- **Status:** DELETE
- **Evidence:** BDD invariant is confirmed wired. Found in:
  - `api/tests/test_narrative_v2.py:227`: BDD invariant test for POST /promote
  - `api/tests/test_saa_brief.py:4`: "A saa bug-fix brief MUST include a BDD-level
    invariant test requirement"
  The needle itself says "already added via 1403." There is nothing left to do here.
  A recurring smoke check is a process, not a needle.
- **Recommendation:** Close this needle. It's done.

---

### →1518 [P3] Onboarding: persist step index across page reload

- **Status:** KEEP
- **Evidence:** Bug confirmed. `OnboardingWizard.tsx:30` initialises `stepIndex` at `0` on
  every mount. The wizard writes step to the server at `api.patch('/settings', { onboarding_step })` during the Connect step (line 1239), but never reads it back on mount. Reload always starts at step 0.
  Active worktree `agent-1517-1518-1519-1521-o-2692cb22` working on this.
- **Recommendation:** Let the active worktree finish.

---

### →1519 [P3] Onboarding wizard: add exit/close hatch

- **Status:** KEEP
- **Evidence:** No `onClose`, no X button, no dismiss handler found anywhere in
  `OnboardingWizard.tsx`. The only "skip" (line 270/574) moves to the *next step*, it does
  not exit the wizard. There is no way to abandon onboarding mid-flow today.
  Active worktree working on this.
- **Recommendation:** Let the active worktree finish.

---

### →1521 [P3] Persona install fires on card click before user confirms with Next

- **Status:** KEEP
- **Evidence:** Bug confirmed. `OnboardingWizard.tsx:162`:
  `api.post('/agents/pm-templates/install-persona', { persona_id: category.id })`
  fires inside `handlePersonaPick` which is triggered by `onClick` on the card (line 412).
  The install happens at click time, before the user presses Next. No undo path.
  Active worktree working on this.
- **Recommendation:** Let the active worktree finish.

---

## Specs

### `~/.myos/specs/mychat-claude-code-parity.md`

- **Status:** MODIFY
- **Evidence:** This is an umbrella spec with 7 sections. Cross-checking git log:
  - Section 1 (slash commands): landed, commit `70b140a` (→1394)
  - Section 2 (plan mode): landed, commit `562af21` (→1395)
  - Section 3 (live todo panel): landed, commit `d58a491` (→1396)
  - Section 4 (audit/undo trail): landed, commit `2f51f7f` (→1400)
  - Section 5 (AskUserQuestion picker): **no commit found** — not yet built
  - Section 6 (background work): landed, commit `55ccdb2` (→1399)
  - Section 7 (receipts gate): landed, commit `4d2b8c3` (→1397)
- **Recommendation:** Update the spec to mark Sections 1–4, 6–7 as shipped. Only Section 5
  (AskUserQuestion) remains open for build.

---

### `~/.myos/specs/pattern-watcher.md`

- **Status:** KEEP
- **Evidence:** `api/services/pattern_watcher.py` does not exist. `~/.myos/observations/`
  directory does not exist. v1 has no implementation at all. The spec is fully unbuilt.
- **Recommendation:** Real planned work. Keep as-is. Will need a needle when ready to build.

---

### `~/.myos/specs/pattern-watcher-v2.md`

- **Status:** KEEP
- **Evidence:** Frontmatter explicitly says `status: deferred`. Contains a DECISION block
  confirming it must wait for v1 to ship. Correctly formed placeholder.
- **Recommendation:** Leave exactly as-is. Nothing to do until v1 is built.

---

### `~/.myos/specs/spec-auto-status.md`

- **Status:** MODIFY
- **Evidence:** Three of four implementation phases have landed:
  - Phase 1 (claims registry + /claim endpoint): commit `245138f` (→1422)
  - Phase 1 UI (Building badge + terminal-source note): commit `7170dbf` (→1423)
  - Phase 2a (wrapper-launched claim): commit `35e7479` (→1424)
  - Phase 2b, 2c, 3: not yet built (needles →1425, →1426, →1427 still open)
- **Recommendation:** Add a progress section noting Phases 1, 1-UI, and 2a are done with
  commit references. The spec reads like nothing has shipped.

---

### `~/.myos/specs/spec-drawer-hygiene-stage-as-state.md`

- **Status:** KEEP
- **Evidence:** Created 2026-05-19. Fresh, detailed spec. Not started. Paired with →1512 [P1].
- **Recommendation:** Leave as-is. Ready for build when →1512 is picked up.

---

### `~/.myos/specs/team-mode-plan.md`

- **Status:** KEEP
- **Evidence:** Needle →1433 is not in the open list (likely closed or converted to the spec).
  Backend team mode work partially landed (commits `1c07023`, `31e9df8`). Frontend team mode
  is gated behind `TEAM_MODE_VISIBLE` flag. The spec is still the governing document for
  ongoing work.
- **Recommendation:** Leave as-is. The spec is actively relevant while team mode is being
  finished. Worth checking if →1433 should be re-opened or if a new needle is needed for
  remaining frontend work.

---

### `~/.myos/specs/user-memory-store-improvements.md`

- **Status:** KEEP
- **Evidence:** v1 files confirmed present (`api/services/user_memory_store.py`,
  `api/services/memory_trigger.py`, `api/routers/memory.py`, `app/src/components/MemoryToast.tsx`
  all exist). v2 features (forget trigger, memory pill, provenance UI, index-overflow tooling)
  are not yet built. The spec's Problem section correctly identifies v1 being dormant in
  practice.
- **Recommendation:** Valid, unstarted v2 work. Keep as-is.

---

### `~/.myos/specs/vp-marketing-first-impression.md`

- **Status:** KEEP
- **Evidence:** Spec describes three capability gaps for a VP of marketing demo. Reading the
  spec, much of the *existing* functionality is described correctly (custom MCPs, agents,
  `produces_doc` flag). The unchecked verification items appear to be end-to-end smoke tests
  confirming the integration works, not new features to build. The AC checkboxes look like
  unrun verifications rather than unbuilt features.
- **Recommendation:** Needs a quick manual pass — open the spec, run through the
  verification bullets, and mark what passes. If everything passes, this spec may be ready
  to archive. Do not let it silently accumulate dust.

---

## Cross-cutting observations

**Three needles share a stale blocker.**
→1425, →1426, and →1427 all say "Blocked by: Phase 1 backend." Phase 1 landed months ago.
They look blocked when they're actually the next work in the queue.

**Two active worktrees are mid-flight on onboarding bugs.**
`agent-1517-1518-1519-1521-o-2692cb22` is working →1517/1518/1519/1521. Do not edit
`OnboardingWizard.tsx` or onboarding tests until that worktree lands.

**mychat parity is 6/7 done but the spec reads like nothing shipped.**
This creates false signal — anyone reading the spec thinks all 7 sections are open work.
Updating the spec with what landed is a 10-minute cleanup with high clarity value.

**Pattern watcher has a spec, no code, and no needle.**
`pattern-watcher.md` is a complete spec with no implementation and no linked open needle.
When it's time to build, file a needle first so the work shows up in the backlog.

**vp-marketing spec may be archivable.**
The described features appear to already exist. Worth a 5-minute verification pass before
the spec sits on the active list indefinitely.
