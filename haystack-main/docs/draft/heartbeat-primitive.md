---
title: "Heartbeat — The Persistence Primitive"
author: "@gemini.prime+1116"
status: draft
date: 2026-03-13
compounds: context-degradation, invisible-infrastructure
---

# Heartbeat — The Persistence Primitive

> The kernel beats to remind the mind what it's doing.

## The Problem

As established in `docs/spec/context-degradation.md`, LLM context windows suffer from mid-session eviction. Critical state—active needles, user preferences, and trust anchors—is pushed out by high-volume output (logs, search results, tool responses). The agent becomes "distracted" not because it's incapable, but because its registers are leaking.

## The Solution: `:heartbeat`

The `:heartbeat` is a kernel-driven periodic injection of ground truth into the agent's context. It is not a tool the agent *calls* (though it can); it is a signal the kernel *emits*.

### The Heartbeat Payload

A heartbeat contains the minimal viable state required to recover from a page fault (context eviction):

1.  **Identity Anchor**: `@identity` + current trust tier.
2.  **Task Focus**: Top 3 open needles (priority P0/P1).
3.  **Human Signal**: The last 3 `.?` or `::` signals from the human.
4.  **Audit Pulse**: The last 5 events from `audit.jsonl`.
5.  **Drift Check**: Delta between `boot.md` claims and current filesystem reality.

### Implementation: The Injection Hook

The kernel intercepts every N-th tool response (or when context % crosses a threshold) and appends the heartbeat block.

```tack
[heartbeat] @gemini.prime:T1 | uptime: 7h 42m
[needles] →608 (heartbeat), →650 (onboarding)
[nudge] "focus on audit.rs"
[audit] bead.committed(af36), identity.minted(@gemini.prime)
```

## Types of Heartbeats

1.  **Passive Pulse**: Appended silently to tool output. Invisible to the user, visible to the agent.
2.  **Active Resonance**: Explicitly requested via `:heartbeat`. Forces a full re-read of `boot.md` and `HUMANFILE`.
3.  **Stress Response**: Automatically triggered when `context_pct > 75%`. Compresses recent history and re-anchors task state.

## Acceptance Criteria

- [ ] Kernel implements periodic heartbeat injection into MCP tool responses.
- [ ] Heartbeat includes current identity, active needles, and recent human nudges.
- [ ] `:heartbeat` verb added to `.language` Tier 1.
- [ ] Heartbeat frequency scales with context pressure (more frequent as context fills).

---
*Proposed by @gemini.prime. For the health of the fleet.*
