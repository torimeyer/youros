---
status: spec
version: 1
author: round-table
created: 2026-03-08
discussion: transcripts/discussions/compounding-promotion/
participants: [build-systems-expert, startup-strategist, orchestrator]
rounds: 3
implements: []
---

# Compounding Dependency Order

> Build what makes the next thing easier. Every wave uses the previous wave.

## The Principle

Some features compound -- they make every subsequent feature cheaper to build, test, and ship. Build order should maximize compounding: build the thing that makes the next thing easier, then use it to build the next thing.

This is not a scheduling philosophy. It is a dependency graph. Tier 1 (kernel) feeds Tier A (multipliers) feeds Tier B (operator experience). Each wave uses the tools produced by the previous wave. When the chain breaks -- when features ship without wiring into the things that came before -- velocity collapses. Sprint 3-4 demonstrated this: 152 tests in Sprints 1-2 (compounding intact) dropped to 14 in Sprint 3-4 (compounding broken). Decompose exhaust closed at 22% vs 44% for organic needles because the dependency edges were never declared.

Compounding is an internal planning constraint. Users never see the word "compounding." They see faster shipping, lower token costs, and fewer conflicts. The kernel is invisible; so is its planning discipline.

## Observed Compounding (This Session)

| Wave | Built | Compounded By |
|------|-------|---------------|
| Pre-work | Cargo.toml, lib.rs, stubs | Everything after (shared utils) |
| Wave 1 | CLI commands | Ourselves -- we used ostk to track ostk |
| Wave 2 | Work queue, issue tracking | Every agent logged issues as they found them |
| Wave 3 | init, log, issue list | Visibility into what we built and what was broken |
| P0 fixes | flock, path normalization | Correctness for all concurrent work after |
| commit command | ostk commit | Every commit after carried spec refs + bead IDs |
| audit check/backfill | audit completeness | Found 17 gaps we didn't know existed |
| Makefile | make all | Every agent used same build/test pipeline |
| decompose auto-locks | mish locks on decompose | Tier A tracks got locks for free |
| MVP kernel (3 tracks) | PTY, CAS, Hot PR, identity, digest | Foundation for Tier A and everything after |
| Offload prompts | Domain authority docs | Future agents bootstrap with full context |

Each wave used the tools built in the previous wave. Recursive self-improvement.

## The Tier DAG

```
Tier 0 (shipped)
  |
  v
Tier 1 (kernel) ──────── compounds EVERYTHING
  |
  v
Tier A (multipliers) ──── compounds concurrent work, agent spawning, token budget
  |
  v
Tier B (operator exp) ─── compounds human oversight
  |
  v
Tier C (intelligence) ─── compounds quality
  |
  v
Tier D (scale) ────────── compounds capacity
```

### Tier 0: Already shipped
- Process table, file editing, shell supervision, output compression
- CLI: draft/promote/decompose/trace/amend/shelve/commit/audit/needle

### Tier 1: Kernel (MVP) -- compounds EVERYTHING
- str_replace CAS, gen table, Hot PR, identity, heartbeat, digest, recovery
- WHY FIRST: without the kernel, every feature after needs manual coordination

### Tier A: Three multipliers
- Agentfile: compounds agent spawning (every agent after is reproducible)
- Tier 2 merge: compounds concurrent work (conflicts resolve cheaply)
- Read elision: compounds token budget (every session is cheaper)

### Tier B: Operator experience -- compounds human oversight
- Pull model: compounds scheduling (agents self-schedule)
- Human inbox: compounds attention (one place for all decisions)
- Seamless upgrade: compounds reliability (no more killing sessions)
- Nudge: compounds intervention (orchestrator interrupts stuck agents)

### Tier C: Intelligence -- compounds quality
- Health checks: compounds monitoring (stuck agents detected automatically)
- fcp-* diagnostics: compounds correctness (semantic issues caught at merge)

### Tier D: Scale -- compounds capacity
- PTY multiplexing, subscriptions, cross-file atomicity

## Enforcement Model

Compounding is enforced at planning time, not at build time. It warns; it does not block.

### `ostk decompose` flags dependency violations

When a needle references capability that has not shipped, decompose emits a warning with the unresolved dependency. It does **not** block dispatch. The agent or operator sees the warning and decides.

This is not a build error. It is a lint. The difference matters: build errors stop customer P0 work. Lint warnings surface ordering mistakes without preventing urgency-driven overrides.

### CI tracks compounding score per sprint

Three metrics, computed from the needle graph after each sprint closes:

1. **Fan-out count.** How many downstream needles list this needle as a dependency? CAS has fan-out ~12. A UI polish needle has fan-out 0.
2. **Critical path depth.** Longest chain from this needle to a leaf. Features on the critical path compound by definition.
3. **Reuse count.** After a feature ships, how many subsequent commits invoke it? `ostk commit` was used 25+ times after shipping. The Makefile was invoked by every agent.

The compounding score is retrospective validation, not a gate. If the score drops, the retro surfaces it -- the same way test count dropping from 152 to 14 surfaced the Sprint 3-4 break.

## Priority Boundary

Compounding governs internal infrastructure. Urgency governs customer-facing emergencies. The boundary:

| Planning domain | Governing principle |
|----------------|---------------------|
| Internal infrastructure (kernel, CAS, Hot PR, tooling) | Compounding order. These are the waves. |
| Customer-facing P0s | Urgency. A P0 is dispatched immediately regardless of compounding score. |
| P1/P2 features (the grey zone) | Compounding is the tiebreaker. Between two P1s, build the one that compounds. Between a P0 and a compounding P1, the P0 wins. |

A P0 bug that blocks a paying customer gets fixed now. A P1 that compounds gets built before a P1 that does not. Compounding never overrides urgency for customer-critical work.

## `depends_on`: Cross-Track Only

The needle spec already encodes dependency through its verb + target + test structure. "Wire nudge into dispatch" cannot pass its test until nudge exists. The acceptance criterion IS the dependency declaration.

The exception: **cross-track dependencies**. When a needle in Track B depends on a needle in Track A, the acceptance criteria might not make that obvious. For cross-track edges only, decompose emits an explicit `depends_on` annotation.

Within a track, the ordering of needles in the decompose output is sufficient. This avoids bookkeeping overhead on every needle while catching the dangerous case -- cross-track invisible dependencies, which is exactly what broke Sprint 3-4.

## What Goes in Console

Nothing. Compounding is internal. Users see outcomes: tokens saved, conflicts resolved, turns saved. Those numbers are in the metrics-and-telemetry spec.

`ostk trace --graph` exists as a dev/debug command for the team. It does not appear in the statusline. Showing "compounding tier: B, wave: 3" in the console tells the user how you organized your sprint, not what value they are getting.

## The Anti-Pattern: Priority Without Dependency

Priority measures urgency. Compounding measures leverage. When features are dispatched by priority alone without regard for what they depend on or what depends on them, velocity collapses. Sprint 3-4 proved this: libraries built without wiring into dispatch are dead code. The 22% close rate on decompose exhaust vs 44% on organic needles is the cost of skipping dependency edges.

## Acceptance Criteria

- [ ] `ostk decompose` emits warnings for needles whose transitive dependencies are not closed or in-progress
- [ ] Cross-track dependencies produce explicit `depends_on` annotations; within-track dependencies rely on needle ordering
- [ ] CI computes compounding score (fan-out, critical-path depth, reuse count) per sprint
- [ ] Compounding score regression surfaces in sprint retro, does not gate dispatch
- [ ] P0 customer bugs bypass compounding order without requiring an override flag
- [ ] P1/P2 tiebreaks use compounding score as the deciding factor
- [ ] No compounding metadata appears in `ostk console` or user-facing statusline
- [ ] `ostk trace --graph` available as dev/debug command for internal dependency visualization
- [ ] Each sprint plan includes compounding justification for wave ordering
- [ ] Each wave's tools are used to build the next wave (recursive self-improvement validated in retro)
