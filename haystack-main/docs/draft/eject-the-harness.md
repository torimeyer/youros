# Eject the Harness — ostk standalone

status: draft
thread: tori-boot, distributed-operating-system
needle: →557
date: 2026-03-09

## Thesis

ostk was built using Claude Code as scaffolding. The scaffolding is now
load-bearing when it should be disposable. Every agent session uses harness
tools (Read, Edit, Bash, Agent, Grep, Glob) instead of OS tools (ss, sh_run,
ostk spawn). The OS provides full parity but loses to the harness because
the harness has system-prompt privilege.

Ejection = the moment an agent can boot from ostk alone, with zero
harness tools, and be equally productive.

## Evidence

Session 2026-03-09 (this session): Opus 4.6 ran ~4 hours on ostk.
Tool calls: ~200. OS tool calls: 0. Every action went through Claude Code's
harness. The OS ran underneath (bash symlink, .ostk/ state) but the agent
never touched it directly.

## The staging metaphor

Stage 1 (Claude Code): got us to orbit. 104 commits, 698 tests.
Stage 2 (ostk): now provides everything Stage 1 does, plus OCC, Hot PR,
compression, audit, identity. The OS surface is complete enough to eject.

## Parity check

| Capability | Harness | ostk | Gap |
|-----------|---------|----------|-----|
| Shell | Bash | sh_run, sh_spawn, sh_interact | none |
| Files | Read, Edit, Write | ss, ss_session | none (+ OCC) |
| Search | Grep, Glob | ostk search, ostk os show | none (→571 shipped) |
| Explore (codebase) | Agent[Explore] | ostk os + ostk search | v1.3 removes Explore — ostk os is the surface |
| Agent spawn | Agent tool | ostk spawn, run | MCP return path |
| Memory | memory MCP | boot.md + audit | none |
| Tasks | beads, TodoWrite | ostk needle | none |
| Code intel | fcp-rust, fcp-python | via ostk serve | none |
| Web | WebFetch | not provided | intentional |
| Git | Bash + gh | sh_run("git...") | none |

Three gaps: search depth, agent delegation return path, web access (intentional).

## Why the harness wins today

1. System prompt privilege — harness tools are always loaded
2. Tool availability — harness works without MCP server
3. Training distribution — models trained on Read/Edit/Bash patterns
4. Safety net — CLAUDE.md says fallback to traditional tools

## Ejection sequence

### Phase 1: Close the gaps
- `ostk show` gets regex/glob search (replaces Grep/Glob)
- `ostk spawn` returns structured results via MCP (replaces Agent tool)
- Decide: web access in OS or explicitly userspace?

### Phase 2: Flip the default
- `ostk serve` becomes the PRIMARY MCP server
- CLAUDE.md removes fallback-to-harness language
- `--agents` guide becomes the only tool reference

### Phase 3: Verify with Tori
- Fresh machine, fresh user, no Claude Code superpowers
- `ostk install && ostk serve` — can Tori boot, work, ship?
- Measure: task completion rate, token efficiency, time to first needle

### Phase 4: Eject
- Remove Claude Code dependency from CLAUDE.md
- boot.md references OS tools only
- The harness becomes optional afterburner, not load-bearing structure

## What ejection does NOT mean

- Removing Claude Code (it still works, it's just not required)
- Removing MCP compatibility (ostk serve IS MCP)
- Building a custom IDE (the terminal IS the IDE)
- Replacing the Claude API (the model is the model)

It means: the conversation between human and model is mediated by ostk,
not by Claude Code. The OS is the runtime. The harness was the bootstrap.

## The Tori test

Can a new agent, booted from ostk alone, with zero docs and zero
harness tools:

1. Read boot.md and orient? (boot)
2. Find and fix a bug? (work)
3. File a needle and commit? (ship)
4. Survive context pressure? (compressed output via sh_run)

If yes → ejection complete. If no → the gaps tell us what to build next.

## Compounds

- Tori-version: ejection IS the Tori gate
- VT100/compression: agents using sh_run get 5x token savings
- OS isolation: ejected agents communicate via nudge, not harness tools
- Distributed OS: each ostk instance is self-sufficient
- needle-bench: bench scenarios run on ejected agents (no harness advantage)
- The white paper: ejection data IS the evidence for "invisible infrastructure wins"
