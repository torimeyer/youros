---
status: spec
author: orchestrator
created: 2026-03-07
implements:
  - ostk v0.1.0 (bd-001 through bd-017)
---

# Document Lifecycle & Work Attribution — P002

> The process that governs all other processes. This spec manages itself.

## The Chain

Every piece of work traces back to a spec. Every spec traces back to a draft.
Every draft traces back to a conversation. Bidirectional, always.

```
conversation → draft/ → spec/ → bead → commit → release
     ↑                                              |
     └────────────── attribution ───────────────────┘
```

## Artifact States

```
DRAFT       → idea captured, not reviewed. Any agent can create.
SPEC        → reviewed, has acceptance criteria. Human promotes.
DECOMPOSED  → spec sections broken into beads. Beads reference spec.
IN PROGRESS → agents working beads. Commits reference beads + spec.
RELEASED    → shipped. Spec updated with "implemented in vX.Y.Z".
AMENDED     → spec changed post-release. New beads from delta.
SHELVED     → contradicted by newer draft. WIP preserved, not destroyed.
```

## State Transitions

### draft.create
```
trigger:  agent or human writes to docs/draft/
event:    {event: "draft.created", path: "draft/pull-model.md", author: "orchestrator"}
action:   ostk intelligence check: "does this contradict any active spec or bead?"
```

### draft.promote → spec
```
trigger:  human runs `ostk promote draft/pull-model.md`
requires: acceptance criteria in every section
event:    {event: "spec.promoted", from: "draft/pull-model.md", to: "spec/pull-model.md"}
action:   mv draft/ → spec/. Old draft path gets symlink (stale-path pattern).
```

### spec.decompose → beads
```
trigger:  human or agent runs `ostk decompose spec/pull-model.md`
creates:  one bead per acceptance criteria section
event:    {event: "beads.created", spec: "spec/pull-model.md", beads: ["bd-050"...]}
each bead: {spec_ref: "spec/pull-model.md#work-queue", acceptance: "..."}
```

### bead.commit
```
trigger:  agent commits code
format:   git commit -m "fix: BUG-004 (spec:agent-lifecycle#drain-before-kill, bd-042)"
event:    {event: "bead.committed", bead: "bd-042", commit: "e110f73", spec_ref: "..."}
```

### bead.release
```
trigger:  tag pushed, CI builds
event:    {event: "bead.released", bead: "bd-042", release: "mish v0.4.17"}
action:   spec updated: "## Drain Protocol — implemented in mish v0.4.17"
```

### spec.amend (THE INTERRUPT)
```
trigger:  new draft contradicts active spec, OR implementation reveals spec gap
event:    {event: "spec.amended", path: "spec/shared-mish.md", severity: "breaking"}
action:   
  1. Find all beads referencing this spec section
  2. Find all agents working those beads
  3. Based on severity:
     minor:         annotate agents on next tool call
     breaking:      gate agent writes until acknowledged
     contradictory: drain agents, shelve WIP, close beads as "shelved"
  4. New beads decomposed from amended spec
```

## The Interrupt Flow

Human has a new idea mid-flight:

```
1. Human: "pull model is better than push"
2. Orchestrator: writes docs/draft/pull-model.md
3. Audit: {event: "draft.created", path: "draft/pull-model.md"}
4. ostk intelligence (Haiku): 
   "pull-model.md contradicts agent-comm-dsl.md#message-stacking 
    and human-in-the-loop.md#push-notifications.
    Affected beads: bd-055, bd-058.
    Agents working those beads: r1, r3.
    Severity: breaking."
5. ostk:
   - Shelves bd-055, bd-058 (WIP snapshot)
   - Annotates r1, r3: "[amendment] pull-model.md changes the model. 
     Your current bead is shelved. Run `ostk work next` for new work."
   - Agent-comm-dsl.md marked as "amended by pull-model.md"
6. Human: `ostk promote draft/pull-model.md`
7. ostk: `ostk decompose spec/pull-model.md` → new beads
8. Agents: pull new beads from queue. Work continues.
```

Total interrupt cost: one intelligence call + drain + respawn. 
No manual message relay. No stale work continuing. No lost context.

## Attribution: "Why Does This Code Exist?"

At any point, trace backward:

```
Q: Why does src/core/pty.rs have detach_on_drop?
A: commit e110f73
   → bead bd-042 (spec:agent-lifecycle#drain-before-kill)
   → spec agent-lifecycle.md section "Kill Protocol"
   → promoted from draft, authored by orchestrator
   → conversation turn 2026-03-07T19:00 "BUG-004 kills all children on exit"
```

At any point, trace forward:

```
Q: What implemented spec/agent-lifecycle.md#kill-protocol?
A: bead bd-042 → commit e110f73 → mish v0.4.17
   bead bd-043 → commit 69fbedc → mish v0.4.18 (proc log)
   bead bd-044 → commit f46085a → mish v0.4.19 (daemon)
```

## Frontmatter Convention

Every doc in draft/ and spec/ has YAML frontmatter:

```yaml
---
status: draft | spec | amended | shelved
author: orchestrator | agent-name
created: 2026-03-07
promoted: 2026-03-07        # when moved to spec/
amended_by: pull-model.md   # if superseded
implements:                  # filled by release tooling
  - mish v0.4.17 (bd-042)
  - mish v0.4.18 (bd-043)
beads: [bd-042, bd-043, bd-044]
---
```

## Commit Message Convention

```
<type>: <description> (spec:<spec-name>#<section>, <bead-id>)

Examples:
  fix: BUG-004 children survive exit (spec:agent-lifecycle#kill-protocol, bd-042)
  feat: JSONL proc log (spec:agent-lifecycle#drain-before-kill, bd-043)
  docs: pull model draft (no bead — draft only)
```

## CLI

```
ostk draft <path>              → create draft, emit event
ostk promote <draft-path>      → validate acceptance criteria, mv to spec/
ostk decompose <spec-path>     → create beads from acceptance sections
ostk trace <commit|bead|spec>  → show full attribution chain
ostk amend <spec-path> --severity breaking  → trigger interrupt flow
ostk shelve <bead>             → snapshot WIP, close bead as shelved
ostk unshelve <bead>           → restore WIP, reopen bead
```

## Acceptance Criteria

- [ ] Every commit references a spec section and bead ID
- [ ] Every spec section shows which release implemented it
- [ ] `ostk trace` produces full backward chain (commit → spec → draft)
- [ ] `ostk trace` produces full forward chain (spec → beads → commits)
- [ ] New draft triggers intelligence check for contradictions
- [ ] Breaking amendments drain affected agents within one tool-call cycle
- [ ] Shelved work is recoverable via `ostk unshelve`
- [ ] Frontmatter auto-updated by release tooling
- [ ] Interrupt flow works end-to-end (new idea → drain → requeue)
