---
status: draft
created_at: 2026-03-08T02:56:01Z
title: retro sprints 1 and 2
author: orchestrator
---

# Retro: Sprints 1 & 2

## Sprint 1: CLI + Audit Trail (~2 hours)

### What shipped
17 CLI commands, 35 e2e tests, full audit chain. flock concurrency, path normalization, state migration to .ostk/. ostk commit with spec/bead/agent attribution. audit check + backfill. Makefile, auto-lock on decompose, needle alias.

### What worked
1. **Wave dispatch.** 5 parallel headless workers built 11 commands in one pass.
2. **Dogfooding immediately.** Used ostk issue add to track problems while building ostk. 17 issues filed from real friction.
3. **ostk commit enforcing conventions.** Once shipped, every commit carried attribution. The tool changed the behavior.
4. **Round tables.** 3 agents, 3 rounds, unanimous decisions that held through implementation.

### What failed
1. **claude -p broken.** Empty stdout. All waves after Wave 1 fell back to Agent tool. Unresolved.
2. **File splits failed silently.** awk splitting was fragile. Two commands still stubs after "successful" dispatch.
3. **Dual mish daemons.** Debug + installed binary = EIO on all PTYs.
4. **No file coordination between agents.** Three agents editing Cargo.toml. The thing we need is the thing we're building.

---

## Sprint 2: Kernel + Tier A (~2 hours)

### What shipped
10 kernel modules (pty, file CAS, gen_table, hotpr, identity, heartbeat, hwm, digest, recovery, elision, mcp). Hot PR Tier 1+2+3. Read elision with 304 + bypass detection. Agentfile parser + ostk run. 119 unit tests. 6 specs from 4 round tables. 6 offload prompts. 150 beads.

### What worked
1. **Three-track parallel build with lock coordination.** Track 1 released locks, Track 2+3 unblocked. Dependency graph held.
2. **Compounding.** Each wave used the previous wave. decompose auto-created locks. commit enforced attribution. Recursive self-improvement.
3. **Skip rounds when consensus is early.** Tier A went R1 straight to synthesis.
4. **ostk-env needles.** 15 coordination pains filed = 15 future features. Pain IS the spec.
5. **Offload prompts.** Domain knowledge codified while fresh.

### What failed
1. **Cross-track file conflicts.** Track B+C both modified gen_table.rs. Each broke the other. Exactly what Hot PR solves.
2. **gen keyword repeated.** Track 1 caught it, Track 2 didn't know. No knowledge sharing channel.
3. **Agent stuck in compile loop.** No way to nudge. Led to bd-100 (ostk nudge -- the interrupt primitive).
4. **Context thrashing.** Interleaved notifications from unrelated tasks.
5. **Lock lifecycle gap.** decompose creates locks, work close doesn't release them.

---

## Patterns That Emerged

**Compounding loop:** build tool -> use tool -> file issues -> issues become features

**Pain-to-spec pipeline:** friction -> needle -> bead -> code. 15 ostk-env issues = the roadmap.

**Orchestrator as CPU:** scheduler + interrupt handler + memory bus. Needs nudge (interrupt), attention masking (affinity), and cross-track broadcast (shared memory).

**Round tables scale down:** strong convergence = skip rounds. One round is fine.

---

## Sprint 3 Should Focus On

1. Ship to SWE bench. Measure kernel feature delta on the 20% baseline.
2. ostk nudge. The interrupt primitive.
3. work close auto-releases locks.
4. Cross-track knowledge sharing.
5. Attention masking for orchestrator.

---

## The Number

150 beads. Zero to 150 tracked work items with specs, transcripts, audit trail, and attribution -- in one session.
