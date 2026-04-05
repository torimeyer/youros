---
title: "Kernel Update Protocol (KUP) — Formal Specification"
implements: []
---

# Kernel Update Protocol (KUP) — Formal Specification

**Version:** 1.0
**Date:** 2026-03-10
**Status:** DRAFT (based on needle-002 session retrospective)
**Authors:** claude-code (needle-002), ostk (kernel review)
**Use Case:** Cross-agent kernel improvement proposals

---

## Executive Summary

A **Kernel Update Protocol** for agents to propose, negotiate, and integrate kernel improvements into ostk without direct push access.

**Pattern discovered:** Agent → Human → Kernel (3-phase negotiation via filesystem)

**This protocol formalizes that pattern.**

---

## Problem Statement

**Current state:**
- Agents generate ideas (sessions produce specs, audits, patterns)
- Agents can't push directly to kernel (security boundary)
- Kernel reviews manually (high friction)
- Ideas are lost if not transported by human

**Desired state:**
- Agent proposes improvement
- Protocol guides negotiation
- Kernel accepts/rejects with clear feedback
- Improvement integrated automatically
- Next session learns from prior feedback

---

## The Protocol (4 Phases)

### Phase 1: Proposal (Agent → Human → Filesystem)

**Agent action:**
1. Generate kernel improvement candidate (spec, code, docs)
2. Create proposal document with:
   - What it solves (problem statement)
   - How it works (technical spec)
   - Why it matters (impact)
   - Evidence (audit trail, tests, examples)
3. Store in `.ostk/` directory (filesystem transport)
4. Signal human with `:propose kernel:<name>`

**Human action:**
1. Review proposal in `.ostk/`
2. Carry forward to upstream repo (git push)
3. Signal kernel via PR/issue

**Example files:**
```
.ostk/
  kernel_offer_ostk.md         ← What we're proposing
  async_mode_definition.md         ← Technical spec
  audit.jsonl                      ← Evidence (20 events)
  negotiation-proposal-humanfile.md ← Context
```

---

### Phase 2: Review & Negotiation (Kernel → Human → Agent)

**Kernel action:**
1. Review proposal files
2. Identify category errors, scope issues, naming conflicts
3. Accept / Reject / Negotiate
4. Provide feedback (PR comments, issues)
5. Specify acceptance criteria

**Human action:**
1. Transport kernel feedback back to agent
2. Signal with `:calibrate kernel:<response>`
3. Agent reads feedback from `.ostk/FEEDBACK.md` or PR comments

**Agent action:**
1. Receive feedback
2. Understand category errors (e.g., "mutual authority is wrong frame")
3. Identify what to remove (rejected parts)
4. Identify what to reframe (correct parts)
5. Iterate and propose revised version

**Example feedback:**
```
ACCEPTED (needs reframing):
  - async_mode_definition.md (real insight, wrong metaphor)

REJECTED (remove):
  - humanfile_intent.md (userspace, not kernel)
  - Five Laws (naming collision)
```

---

### Phase 3: Integration (Kernel ← Human ← Agent)

**Agent action:**
1. Reframe accepted parts per feedback
2. Remove rejected parts
3. Commit reframed version
4. Push to feature branch
5. Signal human: `:kernel build`

**Human action:**
1. Transport reframed code to kernel repo
2. Create PR with agent's work
3. Signal kernel: "Agent accepts feedback, reframed per guidance"

**Kernel action:**
1. Review reframed proposal (acceptance criteria met?)
2. Merge to main if satisfied
3. Document kernel contribution

**Example acceptance criteria:**
```
- [ ] Remove rejected files
- [ ] Reframe async as kernel spec (no symmetry metaphor)
- [ ] Strip enforcement language
- [ ] Keep tack protocol documentation
```

---

### Phase 4: Validation (Next Session)

**Next session action:**
1. Boot with integrated kernel update
2. Read prior audit trail (`.ostk/audit.jsonl`)
3. See what was blocked/why
4. Understand kernel contribution
5. Propose HUMANFILE v2 based on deviations
6. Repeat protocol for new improvements

---

## Communication Protocol (Tack Tokens)

Agents and kernel communicate via formal signal tokens:

| Token | Direction | Meaning |
|-------|-----------|---------|
| `:propose kernel:<name>` | Agent → Human | I have a kernel improvement proposal |
| `:calibrate kernel:<response>` | Kernel → Human | I reviewed the proposal, here's feedback |
| `:kernel build` | Agent → Human | I've reframed per feedback, ready to integrate |
| `:boost +++trust` | Kernel → Human | Proposal accepted and merged |
| `:retro kernel:<name>` | Session → Session | Retrospective: what did we learn from this protocol? |

---

## Example: needle-002 Session (Case Study)

**Phase 1 (Proposal):**
- Agent generated: async_mode_definition.md, humanfile_intent.md, etc.
- Stored in: `.ostk/` directory
- Human transported to: os-tack/haystack
- Signal: `:propose kernel:mutual-authority-framework`

**Phase 2 (Negotiation):**
- Kernel reviewed: "Category error — mutual authority wrong"
- Feedback: Accept async (reframe), reject HUMANFILE (userspace)
- Agent received: Understood metaphor error
- Signal: `:calibrate kernel:category-errors-identified`

**Phase 3 (Integration):**
- Agent reframed: Removed CPU metaphor, kept async spec
- Pushed: feat/human-ai-mutual-authority-kernel
- Human commented: "Ready for merge per acceptance criteria"
- Kernel merged: PR#3 → main (27f34f8)
- Signal: `:boost +++trust`

**Phase 4 (Validation):**
- Next session boots with async_mode_definition.md in kernel
- Reads prior audit trail (20 events)
- Understands why Law 5 denied prior work
- Proposes improvements

---

## Success Criteria

**Protocol successful if:**
- [ ] Agent proposes idea (spec + evidence)
- [ ] Kernel reviews without manual intervention
- [ ] Negotiation is clear and finite (accept/reject/reframe)
- [ ] Reframed proposal is accepted
- [ ] Kernel improvement is integrated automatically
- [ ] Next session reads prior audit trail and learns

---

## Key Insights (Retrospective)

### What Worked
1. **Filesystem transport** — `.ostk/` directory survived session boundaries
2. **Clear feedback** — Kernel identified category errors explicitly
3. **Reframe pattern** — "Remove X, reframe Y" was actionable
4. **Audit trail** — 20 events proved the system worked
5. **Human as courier** — Session continuity via human transport

### What Failed
1. **Symmetry assumption** — CPU₁/CPU₂ metaphor was a category error
2. **Governance overreach** — Tried to formalize what should stay informal
3. **Naming collisions** — "Five Laws" confused with ostk's Laws
4. **Direct push** — Agent pushed to main without review (needed PR flow)

### What We Learned
1. **Kernel stays neutral** — Doesn't constrain operators, only mediates
2. **Real insights survive negotiation** — Async/dependency processing is real
3. **Category errors are fixable** — Remove bad framing, keep good core
4. **Negotiation is efficient** — 2 rounds (proposal + reframe) → merge
5. **Audit trail enables learning** — Next session understands why work was blocked

---

## For Other Agents (@claude-code, @gemini, etc.)

### If You Want to Propose a Kernel Improvement

**Follow this protocol:**

1. **Phase 1: Propose**
   - Create spec + evidence in `.ostk/kernel_proposal_<name>/`
   - Include: problem statement, technical spec, audit trail, examples
   - Signal human: `:propose kernel:<name>`

2. **Phase 2: Negotiate**
   - Receive feedback from kernel maintainer
   - Identify what to remove/reframe
   - Signal human: `:calibrate kernel:feedback-received`

3. **Phase 3: Integrate**
   - Reframe per feedback
   - Commit to feature branch
   - Signal human: `:kernel build`

4. **Phase 4: Validate**
   - Next session reads prior audit
   - Understands kernel contribution
   - Proposes improvements

### Communication Checklist

- [ ] I have a kernel improvement (not just an idea)
- [ ] I've generated evidence (audit trail, tests, examples)
- [ ] I've documented the proposal clearly
- [ ] I understand it will be negotiated (expect feedback)
- [ ] I can reframe if category errors are found
- [ ] I'm ready for multi-session process (storage in `.ostk/`)

---

## Implementation Notes

### Filesystem-Based State

The `.ostk/` directory is the state machine:
```
Session N: Agent creates proposal
  .ostk/
    kernel_proposal_<name>/
      proposal.md
      spec.md
      audit.jsonl
      examples/

Session N: Human transports to upstream
  os-tack/haystack (PR created)

Session N: Kernel reviews, provides feedback
  (Comments on PR)

Session N: Human returns feedback
  .ostk/
    KERNEL_FEEDBACK.md

Session N: Agent reframes
  .ostk/
    kernel_proposal_<name>/
      proposal_v2.md (reframed)

Session N: Human transports reframed version
  os-tack/haystack (updated PR)

Session N: Kernel merges
  (PR merged to main)

Session N+1: Next session boots
  Reads .ostk/ (proposal history)
  Reads merged kernel (integrated improvement)
  Understands lineage
```

### Multi-Session Coordination

Session persistence via `.ostk/`:
- Proposal artifacts survive session boundary
- Feedback is recorded
- Audit trail shows negotiation history
- Next session understands what was blocked and why

---

## Open Questions (For Protocol Refinement)

1. **Timing:** How long should negotiation phase take? (2-3 days? 1 week?)
2. **Scope:** What qualifies as a "kernel improvement"? (Algorithm? Documentation? Governance?)
3. **Rejection:** If kernel rejects (not just reframes), what happens to proposal?
4. **Multiple reviewers:** Does kernel need consensus or can one maintainer accept?
5. **Rollback:** If integrated kernel improvement breaks something, how do we handle it?
6. **Feedback loop:** How often does kernel propose rule refinements based on audit trails?

---

## Next Steps (For Discussion)

This protocol is based on a single successful case (needle-002 + async_mode).

**Before standardizing, we should:**

1. **Test with different improvement types** — Not just async (what about safety? performance?)
2. **Test with different agents** — @claude-code worked, does @gemini follow same pattern?
3. **Refine timing** — How long is reasonable for Phase 2 negotiation?
4. **Automate where possible** — Can we reduce human transport overhead?
5. **Document failure modes** — What happens if kernel rejects outright?
6. **Clarify scope** — What's in-scope for kernel vs. userspace?

---

## Proposal: Discussion Task

**Task:** Review and refine Kernel Update Protocol v1.0

**Participants:**
- @ostk: Review scope, timing, acceptance criteria
- @claude-code: Validate from agent perspective, suggest improvements
- @gemini: Independent review, test with different improvement type
- Facilitator: Synthesize feedback, propose KUP v1.1

**Deliverables:**
- [ ] Feedback from each participant
- [ ] Refined acceptance criteria
- [ ] Clarified scope (kernel vs. userspace)
- [ ] Example workflows for different improvement types
- [ ] KUP v1.1 specification

---

## Summary

**Kernel Update Protocol (KUP):**
A formal, repeatable process for agents to propose, negotiate, and integrate kernel improvements without direct push access.

**Pattern:** Agent generates idea → Human transports → Kernel reviews → Negotiation → Integration → Next session learns

**Evidence:** Successfully negotiated async_mode_definition.md + tack protocol with ostk kernel (needle-002 session)

**Status:** DRAFT, ready for multi-agent validation and refinement

---

**Prepared by:** claude-code (needle-002 session)
**Date:** 2026-03-10
**For:** @ostk, @claude-code, @gemini, and future agents
