# Spec relevance review — 2026-07-02 (reconstructed)

**Reviewed by:** agent `review-all-specs-relevance-994535` with 4 parallel readers
**Status of this document:** RECONSTRUCTED. The agent's original 18,955-byte report was lost when its worktree was deleted before the report could be committed (git was sandbox-blocked in the kernel shell; the worktree vanished minutes after agent stop). Sources for this reconstruction: the agent's final summary (full transcript tail saved before deletion), and the two reader assessments that WERE committed: `586238be` (specs 00-02, detail in commit message) and `dd2a99dc` (specs 10-13, 103-line doc recovered alongside this file as `spec-relevance-assessment-10-13.md`). Detail for specs 03/04/06/07/08/09 is summary-level only; their reader docs were in the deleted worktrees. See the data-loss bug task filed 2026-07-02.

## Verdicts (all 13 specs, from the agent's final summary, verbatim counts)

- **3 RELEVANT AS-IS:** discord, org-maturity-baseline, team-mode-plan
- **7 NEEDS UPDATES:** adopt-claude-code-ideas, backend-event-loop-wedge, executive-summary-jira, guided-google-self-setup, pattern-watcher-v1, remind-me, spec-driven-dev-E1-E5
- **2 SHIPPED-CLOSED:** text-youros (archived 20260702T202913Z), user-memory (pre-archived 2026-06-03)
- **1 OBSOLETE-CLOSED:** pattern-watcher-v2 (archived 20260702T202921Z, self-deferred by its own DECISION section)
- **0 VENDOR-DUPLICATE:** all 10 open specs pass the vendor test (Claude Code Enterprise / Cowork / Gemini Enterprise). Closest calls, kept deliberately: spec-06 vs Gemini Inbox and spec-11 vs Cowork/Gemini Projects; both are kernel-integrated/cross-vendor in ways the vendors do not cover.
- **Close mechanism used:** `POST /api/specs/{slug}/archive` (sanctioned pipeline endpoint).

## Recovered detail

### spec-00, adopt-claude-code-ideas (vendor-agnostic abstractions) — NEEDS UPDATES
From commit 586238be message: "AC3/AC4/AC5 backend/AC1 partially shipped; AC6 not started; line refs drifted." Consistent with `api/services/runtime_provider.py` docstring (spawn wiring deferred to →1895) and `agents.py:5092` TODO.

### specs 10-13 — see `docs/research/spec-relevance-assessment-10-13.md` (recovered verbatim from dd2a99dc)

### specs 03/04/06/07/08/09 — detail lost with worktrees
Known follow-ups already filed as tasks by the readers before deletion, e.g. →2458 "Spec-04 (guided-Google-self-setup): merge in-flight worktree + fix auth.py" and →2463 "Update spec statuses based on spec-10-13 relevance review". The NEEDS-UPDATES verdicts for backend-event-loop-wedge, executive-summary-jira, guided-google-self-setup, pattern-watcher-v1, remind-me, spec-driven-dev stand; per-spec edit lists must be regenerated when each spec is picked up (each update agent should re-verify references against the codebase, which was the required workflow anyway).

## Cross-reference
Task-side relevance review of the same date: `docs/research/task-relevance-review-2026-07-02.md` (198 tasks: 139 relevant, 2 closed with receipts, 57 archived as test artifacts with user approval).
