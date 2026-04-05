---
title: "Kernel Update Protocol (KUP) v1.1 — Formal Specification"
implements: []
---

# Kernel Update Protocol (KUP) v1.1 — Formal Specification

**Version:** 1.1 (Refined)
**Date:** 2026-03-10
**Status:** READY FOR OSTK NEGOTIATION
**Proposed by:** claude-code (needle-002 session + retrospective)
**For:** @ostk, @claude-code, @gemini, cross-agent kernel improvements

---

## Executive Summary

A **Kernel Update Protocol** enabling agents to propose, negotiate, and integrate kernel improvements into ostk without direct push access.

**Core pattern:** Agent generates improvement → Human transports → Kernel reviews → Negotiation → Integration → Next session learns

**Proven:** Successfully negotiated async_mode + tack protocol (needle-002 → ostk PR#3 → merged 27f34f8)

**This protocol formalizes that proven pattern for reuse.**

---

## Problem Statement

### Current Friction
- Agents generate ideas (specs, audits, patterns in sessions)
- Agents can't push directly to kernel (security boundary ✓)
- Kernel reviews manually (high friction, time-consuming)
- Ideas are lost if human doesn't transport
- Next session doesn't learn from prior feedback

### Desired State
- Agent proposes improvement (clear format)
- Protocol guides negotiation (structured feedback)
- Kernel accepts/rejects with actionable guidance
- Improvement integrates automatically
- Next session reads audit trail and learns why work was blocked/accepted

---

## The Protocol (4 Phases + Continuous Loop)

### Phase 1: Proposal (Agent → Human → Filesystem → Kernel)

**Duration:** 1 session (completed before shutdown)
**Responsibility:** Agent + Human courier

**Agent:**
```
1. Generate kernel improvement candidate
   - Spec file (what it solves, how it works, why it matters)
   - Technical documentation (formal definitions)
   - Evidence (audit trail, test results, examples)
   - Clear problem statement

2. Store in .ostk/ directory:
   .ostk/
     kernel_proposal_<name>/
       README.md           (what we're proposing)
       specification.md    (formal spec)
       audit.jsonl         (evidence)
       examples/           (examples)

3. Signal human: `:propose kernel:<name>`
```

**Human courier:**
```
1. Review proposal in .ostk/
2. Push to upstream repo (create feature branch + PR)
3. Include reference to .ostk/ artifacts
4. Signal kernel: "Agent proposes kernel improvement, see PR"
```

**Acceptance criteria at end of Phase 1:**
- [ ] Proposal file created with clear problem statement
- [ ] Technical spec documented (not just ideas)
- [ ] Evidence included (audit trail minimum 5 events, or tests)
- [ ] Human transports to upstream repo
- [ ] PR created (not pushed to main)

---

### Phase 2: Kernel Review & Negotiation (Kernel → Human → Agent)

**Duration:** 3-5 business days (feedback cycle)
**Responsibility:** Kernel maintainer + Human courier + Agent

**Kernel maintainer:**
```
1. Review proposal in PR (check Phase 1 criteria)
2. Identify:
   - Scope: Is this kernel or userspace?
   - Correctness: Is the spec sound?
   - Category errors: Wrong mental model?
   - Naming: Conflicts with existing terms?
   - Impact: What breaks if we accept?

3. Provide feedback (accept/reject/negotiate):

   ACCEPT (no changes):
     "This spec is solid, kernel-scoped, no conflicts. Merge."

   ACCEPT (reframe):
     "Core insight is real, but metaphor is wrong. Remove X,
      reframe Y as [new context]. Then we'll merge."

   REJECT:
     "This is userspace, not kernel. Keep for @claude-code
      userspace patterns, don't merge to kernel."

   NEGOTIATE:
     "Scope unclear. Need clarification on [point A, B].
      Resubmit with answers, then we'll review again."

4. Comment on PR with:
   - [ ] Acceptance criteria checklist
   - [ ] What to remove
   - What to reframe (and how)
   - Estimated timeline for merge
```

**Human courier:**
```
1. Receive kernel feedback from PR comments
2. Transport back to agent (.ostk/KERNEL_FEEDBACK.md)
3. Signal: `:calibrate kernel:<response>`
```

**Agent:**
```
1. Read kernel feedback
2. Understand classification:
   - REJECT → Move to userspace patterns, don't resubmit
   - NEGOTIATE → Address questions, resubmit
   - ACCEPT (reframe) → Remove/reframe items, resubmit

3. For ACCEPT (reframe):
   a. Remove rejected parts entirely
   b. Reframe accepted parts per kernel guidance
   c. Create new commit
   d. Push to feature branch
   e. Signal: `:kernel build`

4. For NEGOTIATE:
   a. Answer clarifying questions
   b. Update spec with new information
   c. Push to feature branch
   d. Signal: `:calibrate :respond`
```

**Timing SLA:**
- Kernel review: 3-5 business days (maintainer availability)
- Agent reframe: 1-2 sessions (agent capacity)
- Total Phase 2: 1-2 weeks typical

**Acceptance criteria at end of Phase 2:**
- [ ] Kernel feedback provided (accept/reject/negotiate)
- [ ] Agent understands category errors
- [ ] Reframe guidance is actionable
- [ ] Agent signals readiness to reframe (`:kernel build`)

---

### Phase 3: Integration (Agent → Human → Kernel → Main)

**Duration:** 1 session (if Phase 2 consensus achieved)
**Responsibility:** Agent + Human + Kernel maintainer

**Agent:**
```
1. Reframe per kernel guidance:
   - Remove rejected files entirely
   - Reframe accepted files per guidance
   - Remove category error language
   - Keep core insight, fix metaphors

2. Commit reframed version
   `refactor: kernel update per negotiation feedback`

3. Push to feature branch

4. Signal human: `:kernel build`
```

**Human courier:**
```
1. Receive reframed code
2. Update PR (or create new PR from refined branch)
3. Signal kernel: "Agent has reframed per criteria. Ready for merge."
```

**Kernel maintainer:**
```
1. Review reframed proposal
2. Verify acceptance criteria met:
   - [ ] Rejected files removed
   - [ ] Reframed sections use new context
   - [ ] Category error language stripped
   - [ ] Core insight preserved
   - [ ] PR is clean (no merge conflicts)

3. Merge to main (if all criteria met)

4. Signal completion: `:boost +++trust`
```

**Acceptance criteria at end of Phase 3:**
- [ ] Reframed code matches kernel guidance
- [ ] All acceptance criteria met
- [ ] PR merged to main
- [ ] Kernel improvement is live

---

### Phase 4: Validation (Next Session)

**Duration:** Next session boot + initial work
**Responsibility:** Next agent session

**Next session:**
```
1. Boot with kernel integration from Phase 3

2. Read .ostk/ directory:
   - See proposal history
   - See negotiation feedback
   - See final integrated version

3. Read audit trail from prior session
   - Understand what was blocked/why
   - See Law 5 denials (if any)
   - Understand reasoning

4. Propose improvements:
   a. If work was blocked, understand why
   b. Propose HUMANFILE v2 (if kernel feedback mentioned it)
   c. Suggest protocol refinements
   d. Restart protocol for new improvements
```

**Continuous loop:**
```
Session N:   Propose improvement
Session N:   Kernel reviews
Session N:   Agent reframes
Session N:   Kernel merges
Session N+1: Boot with improvement, read audit, propose refinement
Session N+1: New proposal starts
...repeat
```

---

## Scope: Kernel vs. Userspace

### IN-SCOPE FOR KERNEL
- Signal processing algorithms (async, dependency ordering)
- Operator communication protocols (tack tokens)
- Kernel governance rules (5-Laws, if part of kernel)
- Performance optimizations (that don't change API)
- Documentation (for kernel operators)

### OUT-OF-SCOPE (USERSPACE)
- Operator behavior constraints (HUMANFILE) — userspace pattern
- Agent-specific governance (CLAUDE.md extensions)
- Session artifacts (audit logs, snapshots)
- Negotiation documents (proposals, feedback)
- Internal implementation details

**Kernel rule:** "Does this affect how kernel processes signals or mediates operators? If yes → kernel. If no → userspace."

---

## Communication Protocol

### Tack Tokens for KUP

| Token | Sender | Meaning | Response |
|-------|--------|---------|----------|
| `:propose kernel:<name>` | Agent | I have a kernel improvement | `:calibrate :reviewing` |
| `:calibrate :reviewing` | Kernel | We're reviewing your proposal | `:calibrate :waiting` |
| `:calibrate kernel:<feedback>` | Kernel | Review complete, here's feedback | `:kernel build` or `:calibrate :respond` |
| `:kernel build` | Agent | I've reframed per guidance, ready to integrate | `:boost +++trust` or `:negotiate` |
| `:boost +++trust` | Kernel | Proposal accepted and merged | (validation in next session) |
| `:negotiate` | Kernel | Need clarification, resubmit | `:calibrate :respond` |

### Feedback Format

**Kernel provides structured feedback:**

```markdown
## Kernel Review: <proposal-name>

### Decision
[ ] ACCEPT (no changes)
[ ] ACCEPT (reframe)
[ ] REJECT
[ ] NEGOTIATE (clarify)

### Assessment
- Scope: [kernel|userspace|mixed]
- Correctness: [assessment]
- Category errors: [list, if any]
- Naming conflicts: [list, if any]

### Guidance (if ACCEPT/REJECT/NEGOTIATE)

REMOVE:
- humanfile_intent.md (reasoning)
- [file] (reasoning)

REFRAME:
- async_mode_definition.md → Remove CPU₁/CPU₂ metaphor, focus on kernel perspective
- [file] → [how to reframe]

CLARIFY (if NEGOTIATE):
- Question 1: [?]
- Question 2: [?]

### Acceptance Criteria for Merge
- [ ] Removed files: [list]
- [ ] Reframed sections: [list]
- [ ] New context: [what to use instead]
- [ ] Preserved core insight: [what's valuable]

### Timeline
Reframe by: [date]
We'll merge by: [date] (if criteria met)
```

---

## Open Questions (Refined with Answers)

### Q1: How long should negotiation phase take?

**Answer:** 3-5 business days for kernel review, 1-2 sessions for agent reframe.
- Total Phase 2: 1-2 weeks typical
- Can be fast-tracked (2-3 days) if proposal is already solid
- No hard deadline; if in doubt, extend review

### Q2: What qualifies as a "kernel improvement"?

**Answer:** Use the kernel rule:
> "Does this affect how the kernel processes signals or mediates operators?"

Examples:
- ✓ Async signal processing (kernel feature)
- ✓ Operator protocol (kernel-level)
- ✓ Performance optimization (kernel-level)
- ✗ Operator constraints (userspace)
- ✗ Agent governance (userspace)
- ✗ Negotiation artifacts (not code)

**If unsure:** Submit as proposal, kernel will classify in Phase 2

### Q3: If kernel rejects (not reframes), what happens?

**Answer:** Rejected proposals become **userspace patterns**
- Saved in @claude-code's `.ostk/` for userspace use
- Documented as "tried for kernel, better as userspace"
- Can be resubmitted in future if context changes
- Example: HUMANFILE (rejected as kernel → kept as userspace pattern)

### Q4: Does kernel need consensus or can one maintainer accept?

**Answer:** One qualified reviewer can accept
- Kernel designates review authority (who can accept/reject)
- Review should be documented (why this person reviewed)
- Major changes (that affect all users) should have broader review
- Default: One reviewer is fine, unless proposal affects core API

### Q5: If integrated kernel improvement breaks something, how to rollback?

**Answer:** Revert commit + notify agents
1. If breakage detected: `git revert <commit>`
2. Create issue: "Reverted <proposal> due to [breakage]"
3. Signal agents: `:calibrate kernel:regression`
4. Agent can resubmit with fix (Phase 1 again)

### Q6: How often does kernel propose rule refinements based on audits?

**Answer:** Per-session, not automatic
- Kernel reads audit trails when reviewing
- Identifies patterns (blocked work, repeated issues)
- Can suggest: "Next time, consider [refinement]" in feedback
- Agents propose formal refinements via KUP (Phase 1)
- Not automatic; requires explicit proposal + negotiation

---

## Success Metrics

**Protocol successful if:**
- [ ] Agent proposes idea with spec + evidence (Phase 1)
- [ ] Kernel reviews and provides actionable feedback (Phase 2)
- [ ] Agent reframes per guidance (Phase 3)
- [ ] Kernel accepts and merges (Phase 3)
- [ ] Next session reads audit trail and learns (Phase 4)
- [ ] Cycle repeats with improvements (next proposal)

**Efficiency targets:**
- Phase 1: 1 session
- Phase 2: 1-2 weeks
- Phase 3: 1 session
- Phase 4: Ongoing
- **Total time from proposal to merge: 2-3 weeks**

---

## Failure Modes & Recovery

### Mode 1: Kernel Takes Too Long to Review
**Recovery:**
- Agent can ping human with `:calibrate :status?`
- Human checks PR, reports status
- If >1 week, escalate to kernel maintainer

### Mode 2: Agent Can't Reframe per Guidance
**Recovery:**
- Agent signals: `:calibrate :clarify` with specific questions
- Kernel provides more detail
- Iterate until reframe is achievable

### Mode 3: Reframed Proposal Still Doesn't Meet Criteria
**Recovery:**
- Kernel provides new guidance (repeat Phase 2)
- Or kernel rejects outright (move to userspace)
- Agent learns for next proposal

### Mode 4: Merged Proposal Breaks Something
**Recovery:**
- Kernel reverts (`git revert`)
- Creates issue with details
- Agent resubmits with fix

---

## Implementation Checklist (For ostk)

To adopt KUP as official protocol:

- [ ] Designate kernel review authority (who can accept/reject)
- [ ] Define review SLA (3-5 business days)
- [ ] Create feedback template (PR comment format)
- [ ] Document in ostk docs/spec/
- [ ] Test with @claude-code (Phase 1 already worked)
- [ ] Test with @gemini (new agent, new improvement type)
- [ ] Refine based on 2-3 test cycles
- [ ] Publish as official protocol (v1.2+)

---

## Example Workflow (Needle-002 Case Study)

**Phase 1:**
- Agent (@claude-code): Generated async_mode_definition.md + HUMANFILE + etc. in .ostk/
- Human (scottmeyer): Pushed to os-tack/haystack, created PR
- Signal: `:propose kernel:mutual-authority-framework`

**Phase 2:**
- Kernel (ostk maintainer): Reviewed, identified category errors
- Feedback: "Accept async (reframe), reject HUMANFILE (userspace), remove CPU metaphor"
- Acceptance criteria: Remove 6 files, reframe 2 specs
- Signal: `:calibrate kernel:category-errors`

**Phase 3:**
- Agent: Reframed async_mode_definition.md, removed governance language, committed e3774b0
- Human: Updated PR with reframed code
- Signal: `:kernel build`
- Kernel: Merged PR#3 to main (27f34f8)
- Signal: `:boost +++trust`

**Phase 4 (next session):**
- Agent boots with async_mode + tack protocol in kernel
- Reads audit trail from needle-002 (20 events)
- Understands why Law 5 denied work
- Proposes HUMANFILE v2 refinement (new proposal)

---

## Proposal: ostk Adoption

**We're offering this protocol to ostk for:**

1. **Formal adoption** — Make KUP official protocol for all agents
2. **Testing** — Validate with @gemini on different improvement type
3. **Refinement** — 2-3 cycles to optimize SLAs, feedback format, tooling
4. **Publication** — Document in ostk docs/spec/ for reuse

**Benefits:**
- Structured process (no ad-hoc negotiation)
- Clear feedback (accept/reject/reframe format)
- Reusable template (works for any agent)
- Audit trail (every decision recorded)
- Cross-session learning (next session reads feedback)

**Timeline:**
- ostk review + feedback: 1 week
- Refinement v1.2: 1 week
- Test with @gemini: 1-2 weeks
- Publication: Done

---

## For Other Agents (@claude-code, @gemini, etc.)

### Using KUP to Propose Kernel Improvements

**Checklist:**
- [ ] I have a kernel improvement (not just an idea)
- [ ] I've generated spec + evidence (audit trail, tests)
- [ ] I understand scope (kernel vs. userspace)
- [ ] I'm ready for negotiation (expect feedback)
- [ ] I can reframe if category errors are found
- [ ] I understand multi-session process (.ostk/ persistence)

**Workflow:**
1. Generate proposal in `.ostk/kernel_proposal_<name>/`
2. Signal human: `:propose kernel:<name>`
3. Human transports to upstream
4. Kernel reviews, provides feedback
5. You reframe per guidance
6. Kernel merges
7. Next session learns from audit trail

---

## Summary

**Kernel Update Protocol v1.1:**
- Formal 4-phase process for kernel improvements
- Tested and proven (needle-002 → PR#3 → merged)
- Ready for ostk adoption
- Reusable for all agents
- Includes feedback templates, SLAs, scope definitions
- Enables multi-session learning via audit trails

**Status:** READY FOR NEGOTIATION WITH OSTK

**Next:** Offer to @ostk, gather feedback, refine to v1.2

---

**Prepared by:** claude-code (needle-002 session retrospective)
**Date:** 2026-03-10
**For:** @ostk adoption + @claude-code + @gemini validation
