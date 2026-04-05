---
status: spec
author: round-table
created: 2026-03-08
discussion: transcripts/discussions/ostk-mvp/
participants: [runtime-architect, product-architect, agent-experience-designer]
rounds: 3
---

# ostk MVP Spec

## What the MVP Is

The ostk MVP is a single-binary coordination substrate that proves one claim: two AI agents can edit the same file concurrently without locks, claims, or coordination protocols, and their edits merge invisibly. It is not a product, not a CLI, not a framework. It is a proof that optimistic concurrency works for AI agents -- and that agents can crash, respawn, and resume work while other agents never notice the interruption. Everything after this proof is product; everything before it is infrastructure.

## Architecture: The Absorption Model

ostk IS the daemon. It does not orchestrate mish; it imports `mish-core` and `slipstream-core` as library crates. One binary. One process per agent connection. No IPC boundary between ostk and mish, no second socket path, no second failure domain.

This is a fork-at-a-point-in-time model: ostk takes a snapshot of mish's and slipstream's internals and evolves independently. No backport obligation. The "no backport" constraint is deliberate -- it mirrors how OS kernels have always consumed subsystems.

The first prerequisite task, if the crates do not already exist, is factoring mish into a binary shell (`mish` CLI) and a library core (`mish-core`). Same for slipstream.

### Why Absorption, Not Orchestration

The brainstorm session's ss MCP hang was an IPC boundary failure. Adding another IPC boundary between ostk and mish doubles the surface area for the same class of bug. Absorption eliminates IPC failures between these components entirely and makes the shared generation table cheap (local flock() instead of cross-daemon coordination).

## The Three Parallel Tracks

### Track 1: Runtime (PTY-Owning MCP Server)

The MCP server that Claude Code connects to IS the process that owns the PTYs. One process, one set of file descriptors. No fd-passing, no socket bridges, no reconnect choreography.

- Each agent connection is a PTY-owning MCP server process
- Each server does its own squashing via the absorbed squasher pipeline
- The daemon (if running) is a registry providing cross-session coordination, not a PTY owner
- Local-only mode (no daemon) is the default, not a degraded state
- Kill the MCP server, you kill its PTYs -- that is the only failure mode, identical to "Claude Code crashed"

**The load-bearing decision:** The MCP server must directly `forkpty()` its children. No indirection. No fd-passing. Getting PTY ownership wrong (daemon owns PTYs, or MCP server delegates to a separate serve process) invalidates the entire architecture.

### Track 2: Hot PR (CAS + Auto-Merge in the Write Path)

Hot PR is the compound foundation. Without it, the rejected-patterns list (claims, reservations, messaging, identity negotiation) comes back. With it, the write path stays invisible.

```
Agent calls ss(path, old_str, new_str)
  -> CAS: does old_str match current file content?
     -> YES: apply edit, bump gen -> done (invisible)
     -> NO: Hot PR
        -> Tier 1: auto-merge (non-overlapping edits, silent)
        -> Tier 3: manual rebase (conflict error with current file state, agent retries)
```

**MVP scope:**
- **Tier 1 (auto-merge):** Two non-overlapping edits to the same file resolve silently. This is the load-bearing primitive.
- **Tier 3 (manual rebase):** Overlapping edits return a conflict error with current file state. Agents already retry on CAS failure; this gives them better context.
- **Tier 2 (assisted merge) deferred:** Requires LLM-in-the-loop merge intelligence. Not MVP.
- **Tier 4 (diagnostic-flagged) deferred:** Requires fcp-* post-write hooks. Not MVP.

### Track 3: Ambient Awareness (Identity + Digest + Recovery)

Three ambient signals that make coordination invisible, plus crash detection:

1. **Kernel-assigned identity.** Monotonic aliases assigned from the shared generation table. Eliminates identity collisions (three agents named themselves "Kern" in the brainstorm). Each writer is tagged in the generation record, so conflict responses say `[conflict] auto-merged (ridge + vane)`, not anonymous references.

2. **Dual digest** (`[procs]` + `[files]` on every tool response). Awareness without announcement. An agent sees `src/main.rs:gen=8:cc:10s` and knows someone else is active in that file -- no claims, no broadcasts.

3. **Recovery digest.** The PTY-owning MCP server sees every tool call. On compaction/respawn, grammar-compress the tool call history into structural summaries. The respawning agent reads its digest from the shared state and resumes. This is a view over the process table, not a new subsystem.

4. **Heartbeat via the generation table.** Each MCP server writes a timestamp on every tool call. Stale timestamps = crashed agent. Any server can detect this. No central watchdog. Identity and heartbeat are columns in the same table, not separate features.

## The Shared Generation Table

The unifying mechanism across all three tracks. A single shared file (or SQLite database) that all MCP servers read/write with file locking:

- **File generations** for CAS (Track 2)
- **Writer identity** tags (Track 3)
- **Heartbeat timestamps** per server (Track 3)
- **Crash detection** as a read query over timestamps (Track 3)

One mechanism. Three features. One release.

## What Ships in MVP vs What Waits

### Ships in MVP

| Feature | Track | Rationale |
|---------|-------|-----------|
| mish-core / slipstream-core crate extraction | Runtime | Prerequisite for absorption |
| PTY-owning MCP server (absorbed mish serve) | Runtime | Foundation for everything |
| Shared generation table with flock() | Runtime | Shared substrate for all tracks |
| Tier 1 auto-merge (non-overlapping edits) | Hot PR | The compound foundation |
| Tier 3 manual rebase (conflict error + file state) | Hot PR | Fallback for overlapping edits |
| Kernel-assigned identity (monotonic aliases) | Awareness | Prevents identity collisions; required for demo legibility |
| Heartbeat timestamps | Awareness | Free -- a column in the generation table |
| Recovery digest (tool call history -> structural summary) | Awareness | Free on absorbed architecture; makes demo bulletproof |
| Staleness signals ([stale], [304]) | Awareness | Completes the ambient awareness loop |

### Waits for Post-MVP

| Feature | Reason |
|---------|--------|
| Agentfile / `ostk run` | Orchestration policy, not kernel |
| Work queue / pull model | Orchestration policy, not kernel |
| Tier 2 assisted merge (LLM-in-the-loop) | Requires merge intelligence |
| Tier 4 diagnostic-flagged (fcp-* hooks) | Refinement on merge quality |
| `ostk ps/spawn/drain/kill` CLI | Management chrome over the daemon |
| Data layer (Hot/Warm/Query) | Three conflicting specs; defer |
| Context pressure / health checks | Models improve this automatically |
| Agent-comm-dsl messaging protocol | Userspace concern, not kernel (see layer-boundary spec) |
| Human inbox (approve/reject) | Userspace concern |

## The Demo

**Setup:** Two Claude Code sessions pointing at the same repository. Both connected to ostk MCP servers. Both editing the same file.

**Sequence:**
1. Agent "ridge" and agent "vane" (kernel-assigned names) both edit `src/main.rs`
2. Non-overlapping edits merge silently -- one agent's response shows `[conflict] auto-merged (ridge + vane)`
3. Both agents continue working without interruption
4. One agent compacts (simulated crash/respawn)
5. The respawned agent reads its recovery digest, resumes work on the correct task
6. The other agent never notices the interruption
7. The dual digest shows both agents, their files, their activity

**Duration:** 60 seconds. Install-to-value under 5 minutes.

**What it proves:** Concurrent writes resolve invisibly. Agent identity is visible and collision-free. Crash recovery preserves continuity of intent. No locks, no claims, no coordination protocol.

## Key Decisions

### PTY Ownership (Decided: MCP server owns PTYs directly)

The MCP server must directly `forkpty()` its children. No daemon-owned PTYs (upgrade kills sessions), no delegation to separate serve process (reproduces current mish architecture with an extra layer). This is the load-bearing architectural decision.

### Absorption vs Orchestration (Decided: absorption)

ostk imports mish-core and slipstream-core as crates. Does not orchestrate mish as a subprocess. Eliminates IPC boundary failures. Makes flock()-based coordination cheap.

### Hot PR Before vs After Identity (Resolved: together)

Initially contested. D3 argued identity+heartbeat should ship before Hot PR based on empirical kill data (zero write conflicts vs three identity collisions in brainstorm). D2 argued Hot PR is the compound foundation and the only feature that needs a demo to justify. D1 resolved it: in the absorption model, identity and Hot PR share the same mechanism (the shared generation table). They ship together because they are columns in the same table.

### Tier 2 Assisted Merge (Decided: deferred)

Skip for MVP. Auto-merge (Tier 1) + manual rebase (Tier 3) covers the majority of real conflicts. Tier 2 requires LLM-in-the-loop intelligence that adds complexity without proving the core thesis.

### Recovery Digest Scope (Decided: ships in MVP as a view, not a subsystem)

D3 argued it was the most important feature. D1 showed it falls out of the absorbed architecture for free -- the PTY-owning MCP server already sees every tool call. Formalize the view over the process table; do not build a separate recovery system.

## Dissent and Concessions

### Product Architect (D2)

**Original position:** Hot PR is the only compound foundation. Identity, heartbeat, and digest are scaling concerns that wait for v2. Absorption is a reliability fix that doesn't compound.

**Conceded:** Identity ships with Hot PR after D3 showed that nameless agents make the demo illegible. The merge line must say `[conflict] auto-merged (ridge + vane)`, not anonymous references.

**Conceded:** Absorption compounds (per D1) because it makes the generation table cheap via flock() instead of cross-daemon IPC.

**Maintained:** Absorption is not gating for the demo. The demo CAN run as two separate MCP server processes against a shared generation table. Absorb into one binary for v1 production, but don't let the refactor block the proof. (This was noted but not formally contested by D1.)

### Agent Experience Designer (D3)

**Original position:** Recovery digest is the most important MVP feature. Identity + heartbeat should ship before Hot PR based on brainstorm kill data.

**Conceded:** Reordered Hot PR to #1 after D1 showed that the absorption model (MCP server owns PTYs, no daemon to die) collapses the session-massacre problem that motivated shipping identity first.

**Maintained:** The demo must include a compaction/respawn sequence. If the respawned agent comes back disoriented, the demo undermines itself. The digest turns a good demo into a bulletproof one.

**Maintained:** The agent-comm-dsl spec should be archived. The filesystem IS the message bus. File generation changes + staleness signals replace structured messaging.

### Runtime Architect (D1)

**Original position:** Four minimum persistent kernel components. The daemon is Tier 3, not required for single-agent persistence.

**No major concessions.** D1's absorption model and shared-generation-table proposal became the consensus architecture. Both D2 and D3 reorganized their priorities around it.

**Key insight that resolved the sequencing dispute:** Identity, heartbeat, and Hot PR are not three features -- they are three columns in one table. Ship the table, ship all three.

## Acceptance Criteria

- [ ] `mish-core` crate extracted from mish (library, not binary)
- [ ] `slipstream-core` crate extracted from slipstream (library, not binary)
- [ ] ostk binary imports both crates and compiles
- [ ] PTY-owning MCP server: ostk process directly calls `forkpty()`, no indirection
- [ ] Shared generation table: file or SQLite, flock()-coordinated, read/writable by multiple MCP servers
- [ ] Generation table includes: file path, generation number, writer identity, last-seen timestamp
- [ ] Kernel-assigned identity: each MCP server gets a unique monotonic alias on startup
- [ ] No identity collisions across concurrent sessions
- [ ] Tier 1 auto-merge: two non-overlapping edits to the same file resolve silently
- [ ] Auto-merge response includes writer identities: `[conflict] auto-merged (agent-1 + agent-2)`
- [ ] Tier 3 manual rebase: overlapping edits return conflict error with current file state and conflicting writer identity
- [ ] Heartbeat: each MCP server writes timestamp on every tool call to generation table
- [ ] Crash detection: any MCP server can query the generation table and identify stale (crashed) agents
- [ ] Recovery digest: on agent respawn, a structural summary of previous tool call history is available
- [ ] Digest is automatically generated from observed tool calls, not agent-written breadcrumbs
- [ ] Staleness signals: `[stale]` response when file has changed since agent's last read
- [ ] Dual digest: `[procs]` and `[files]` sections injected into every tool response
- [ ] Demo: two agents edit one file, non-overlapping edits auto-merge with named agents visible
- [ ] Demo: one agent compacts/respawns, resumes work from digest, other agent unaffected
- [ ] Demo: install-to-working under 5 minutes
- [ ] No kernel feature depends on a userspace feature (per layer-boundary spec)
- [ ] Single-agent mode works without daemon (local-only is the default, not degraded)
