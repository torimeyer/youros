# Needles + Specs Audit — 2026-05-19

> READ-ONLY audit. No needles were closed or modified. All recommendations
> are for a human or follow-up agent to act on.

---

## Summary

- **Total reviewed:** 11 open needles + 8 specs
- **Recommended actions:** 8 KEEP, 1 MODIFY, 1 UNBLOCK, 1 DELETE-candidate |
  6 KEEP, 1 ARCHIVE, 1 MODIFY
- **Active worktrees in flight:** 2 agents currently editing files for
  →1517/1518/1519/1521 (onboarding) and →1523 (Tasks sort)

### Top 3 highest-value cleanup actions

1. **Archive `mychat-claude-code-parity.md`** — all 7 sections fully shipped
   (commits 70b140a, 562af21, d58a491, 2c0bf38, 4d2b8c3, 55ccdb2, 2f51f7f).
   The spec still shows status=spec in the UI. Archive it so the Specs page
   stops counting it.

2. **Unblock →1441 (Theme C v2)** — the Theme C MVP it was waiting on landed
   in commit f627bd0. The needle is still marked "Blocked by Theme C MVP" but
   that blocker is gone. Remove the block so it can be picked up.

3. **Convert →1511 (Rule 2c smoke check) from a needle to a CI test** — the
   BDD invariant IS wired (confirmed in `api/services/saa_brief.py:57-105`).
   A needle is the wrong home for a recurring smoke check; it should be a
   scheduled test or added to the e2e gate.

---

## Needles

### →1512 [P1] Build spec-drawer hygiene

- **Status:** KEEP
- **Evidence:** Spec promoted 2026-05-19, zero commits referencing 1512 or
  `spec-drawer-hygiene`. `api/services/spec_audit.py` does not exist.
- **Recommendation:** Genuine new work. Ship it.

---

### →1425 [P2] Spec-auto-status Phase 2b: agent self-register claim path

- **Status:** KEEP
- **Evidence:** Phase 1 landed f169a82. Phase 2a (wrapper) landed e019081.
  No commit mentioning 1425 or "agent self-register preamble."
- **Recommendation:** Next logical step after Phase 2a. Still needed.

---

### →1426 [P2] Spec-auto-status Phase 2c: /build-spec slash handler

- **Status:** KEEP
- **Evidence:** Same as →1425. Phase 2a is done; 2c is independent. No
  commits mentioning 1426 or `/build-spec`.
- **Recommendation:** Keep as a sibling to →1425. Both feed into the same
  `/claim` endpoint from `spec-auto-status.md`.

---

### →1441 [P2] Theme C v2 — intel digest + spec promotion

- **Status:** KEEP, but **remove the blocker**
- **Evidence:** Theme C MVP landed f627bd0 (→1440). The needle's "Blocked by
  Theme C MVP" is resolved. No `GET /api/intel/digest` endpoint exists yet.
- **Recommendation:** Remove the blockedBy on Theme C MVP so this can be
  picked up. The work itself is still valid.

---

### →1465 [P2] Plan Waves backend pieces (spawn-preflight + builder conflict check)

- **Status:** KEEP
- **Evidence:** Frontend rebuild landed bd04785 (→1461). No commits mention
  1465, `spawn-preflight`, or `_compute_path_conflicts`. The two backend
  layers described (preflight endpoint + builder-prompt re-check) are missing.
- **Recommendation:** Still needed. The needle itself says to wait for two
  other worktrees to finish editing `api/routers/agents.py` and
  `api/routers/specs.py` — worth checking whether those worktrees have
  since committed.

---

### →1513 [P2] Upstream ostk: drop docs/spec/ as promote target

- **Status:** KEEP
- **Evidence:** Local myOS layer already rejects docs/spec/ writes (commit
  7be5a83, →1377). But the needle is specifically about contributing the
  change *upstream to ostk itself*. That contribution has not happened.
- **Recommendation:** Small task. File as a PR against the ostk repo when
  bandwidth allows.

---

### →1517 [P2] Connect step skipped when provider detected

- **Status:** KEEP (being actively worked)
- **Evidence:** Confirmed in OnboardingWizard.tsx line 143-146: when
  `detectedProvider !== null` and the wizard hits the Connect step index,
  it jumps past it. The whole Connect step (including Atlassian/GitHub
  setup) is skipped. Worktree `agent-1517-1518-1519-1521-o-2692cb22` is
  live with files being edited right now.
- **Recommendation:** Do not touch — active agent.

---

### →1523 [P2] Tasks page: add Sort by wave option

- **Status:** KEEP (being actively worked)
- **Evidence:** Worktree `agent-1523-tasks-page-sort-0f526fb4` has
  Tasks.tsx at gen 6, actively being edited.
- **Recommendation:** Do not touch — active agent.

---

### →1427 [P3] Spec-auto-status Phase 3: passive git-commit detection

- **Status:** KEEP
- **Evidence:** No commits mentioning 1427 or `ostk_post_commit_hook.sh`.
  Phase 3 is independent — can be built without waiting for Phase 2b/2c.
- **Recommendation:** Keep. Low-effort hook install. Could be picked up
  alongside 2b/2c.

---

### →1511 [P3] Verify Rule 2c BDD invariant stays wired

- **Status:** MODIFY — convert to a CI check
- **Evidence:** `api/services/saa_brief.py` lines 57-105 confirm the BDD
  invariant block IS present and wired. It is injected for `kind=bug-fix`
  and `kind=diagnose`. The current needle asks for a "recurring smoke check"
  which a needle cannot do.
- **Recommendation:** Close this needle and add a test to
  `api/tests/test_saa_brief.py` that asserts `generate_saa_brief("bug-fix")`
  contains the BDD invariant block. Wire it to the e2e smoke test suite.

---

### →1518 [P3] Onboarding: persist step index across page reload

- **Status:** KEEP (being actively worked)
- **Evidence:** Commit cbcda1e added `onboarding_step` to backend settings.
  The code at OnboardingWizard.tsx line 102 reads it back on mount. However
  this needle was filed after a fresh audit (→1491 review) identified a
  remaining gap, so the agent for this worktree is investigating the
  specific failure mode. Active.
- **Recommendation:** Do not touch — active agent.

---

### →1519 [P3] Onboarding wizard: add exit/close hatch

- **Status:** KEEP (being actively worked)
- **Evidence:** Confirmed gap — zero results for `onClose/exit/close/dismiss`
  in OnboardingWizard.tsx (outside an oauth error Dismiss button). No X
  button, no skip-all, no escape. Same worktree as →1517.
- **Recommendation:** Do not touch — active agent.

---

### →1521 [P3] Persona install fires on card click before user confirms

- **Status:** KEEP (being actively worked)
- **Evidence:** OnboardingWizard.tsx line 162 shows
  `api.post('/agents/pm-templates/install-persona', ...)` fires inside
  `handlePersonaPick`, which is called by the card's `onClick` at line 412.
  No confirmation step. Same worktree as →1517.
- **Recommendation:** Do not touch — active agent.

---

## Specs

### `~/.myos/specs/mychat-claude-code-parity.md`

- **Status:** ARCHIVE
- **Evidence:** All 7 sections shipped:
  - Section 1 Slash commands: commit 70b140a (→1394)
  - Section 2 Plan mode: commit 562af21 (→1395)
  - Section 3 Live TodoWrite: commit d58a491 (→1396)
  - Section 4 Audit/undo trail: commit 2f51f7f (→1400)
  - Section 5 AskUserQuestion picker: commit 2c0bf38 (→1398)
  - Section 6 Background work: commit 55ccdb2 (→1399)
  - Section 7 Receipts gate: commit 4d2b8c3 (→1397)
  The spec still shows `status: spec` and has all AC unchecked.
- **Recommendation:** Archive to `~/.myos/specs/archive/`. This is the
  largest single cleanup win — it removes 7 "building" rows from the Specs
  page all at once.

---

### `~/.myos/specs/pattern-watcher.md`

- **Status:** KEEP
- **Evidence:** `api/services/pattern_watcher.py` does not exist.
  `~/myos/observations/` does not exist. Feature is unbuilt.
- **Recommendation:** Solid spec with clear AC. Keep as-is.

---

### `~/.myos/specs/pattern-watcher-v2.md`

- **Status:** KEEP (intentional placeholder)
- **Evidence:** `status: deferred` in frontmatter. The spec explicitly says
  "do not build until v1 ships." It is functioning correctly as a backlog
  placeholder, not an active spec.
- **Recommendation:** No action needed. If the Specs page is counting it as
  active, filter on its `deferred` status.

---

### `~/.myos/specs/spec-auto-status.md`

- **Status:** KEEP, but MODIFY to reflect progress
- **Evidence:** Phase 1 shipped (f169a82), Phase 2a shipped (e019081),
  Building badge + terminal note shipped (389d12e). The spec's AC list
  FR-001 through FR-006 are done. FR-007 through FR-014 are still open
  (wrapper CLI, agent preamble, slash handler, commit watcher, UI tooltip).
- **Recommendation:** Tick the completed AC items so the spec's visible
  progress reflects reality. Not urgent, but prevents the spec from
  appearing more behind than it is.

---

### `~/.myos/specs/spec-drawer-hygiene-stage-as-state.md`

- **Status:** KEEP
- **Evidence:** Fresh (2026-05-19). `api/services/spec_audit.py` does not
  exist. No commits referencing this spec.
- **Recommendation:** Genuine new work tied to →1512. Keep.

---

### `~/.myos/specs/team-mode-plan.md`

- **Status:** KEEP
- **Evidence:** Team mode backend skeleton landed 1c07023 (team schemas +
  router + decisions wiring). But the spec is a comprehensive design
  document for the full multi-user rollout (identity model, roles, billing,
  sharing model) — most of which is not built. The spec's `status: draft`
  reflects this.
- **Recommendation:** Keep as the canonical design reference. No action
  needed.

---

### `~/.myos/specs/user-memory-store-improvements.md`

- **Status:** KEEP
- **Evidence:** v1 memory feature landed commit 6510705 (→1393). But the
  spec notes `~/.myos/users/default/MEMORY.md` does not yet exist, meaning
  the trigger has never fired in practice. The v2 improvements (forget
  trigger, memory pill, provenance UI, index-overflow handling) are all
  unbuilt.
- **Recommendation:** Genuine new work. Keep.

---

### `~/.myos/specs/vp-marketing-first-impression.md`

- **Status:** KEEP
- **Evidence:** None of the 3 blocks built:
  - Block 1 (source library + KNOWLEDGE directive): `api/routers/knowledge.py`
    still the 97-line note-taking endpoint; no `~/.myos/sources/` directory.
  - Block 2 (spec-to-artifact chooser): Specs wizard unchanged.
  - Block 3 (per-agent MCP/skill visibility): Agents page unchanged.
- **Recommendation:** Genuine new work. Keep.

---

## Cross-cutting observations

**A cluster of specs just promoted (2026-05-19) are all unbuilt.** Three
specs promoted on the same day (`spec-drawer-hygiene`, `user-memory-store-
improvements`, `pattern-watcher-v2`) came from a single planning session.
None have any code yet. They are healthy backlog — just note they are all
competing for the same build slots.

**mychat-claude-code-parity is the clearest staleness problem.** The spec
has 7 fully-shipped sections but all AC boxes unchecked and status=spec.
It is inflating the Specs page count and obscuring real work.

**Spec-auto-status is partially shipped but the spec doesn't show it.**
Phase 1, 2a, and the UI layer all landed. The spec still looks fully open.
Ticking the done AC items costs 5 minutes and makes the remaining work
clearer.

**→1441 is ready to start** — the only thing blocking it was the Theme C MVP,
which shipped. Removing the blockedBy relationship unlocks it for the
next sprint.

**Two agents are live right now** on the onboarding cluster (→1517/18/19/21)
and the Tasks sort (→1523). No action needed; just FYI.
