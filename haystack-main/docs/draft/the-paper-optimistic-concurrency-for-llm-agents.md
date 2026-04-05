---
title: the paper -- optimistic concurrency for LLM agents
created_at: 2026-03-08T03:57:26Z
status: draft
author: scottmeyer
---

# Optimistic Concurrency for LLM Agent Coordination

## Abstract

Multi-agent LLM systems face a coordination problem: multiple agents editing shared artifacts must resolve conflicts without destroying each other's work. The dominant approach -- pessimistic locking via worktrees, file reservations, or claim protocols -- prevents conflicts by isolating agents, then requires expensive merge resolution rounds.

We present ostk, a coordination runtime using optimistic concurrency control for LLM agents. Agents write freely to shared files. The runtime resolves conflicts at write time using three tiers: silent auto-merge for non-overlapping edits, assisted merge with mechanical suggestions for nearby edits, and conflict-with-context for overlapping edits. The key insight: str_replace -- the standard LLM file-editing primitive -- is already a compare-and-swap operation. The match string IS the CAS token.

In controlled experiments, ostk achieves identical task resolution rates while reducing token costs by 20%+. Three autonomous agents building the same codebase without file isolation produced 152 unit tests and 10 kernel modules, with all conflicts resolved by the runtime.

## The Problem: Isolation Doesn't Scale

The emerging pattern for multi-agent development uses git worktrees. This mirrors pessimistic locking (Perforce). It fails because:

1. **Shared files are unavoidable.** Cargo.toml, mod.rs, main.rs. Splitting ownership burns orchestrator context.
2. **Merge is expensive.** Full context reload per conflict. Thousands of tokens.
3. **Isolation prevents awareness.** No shared state, no read elision opportunity.
4. **Orchestrator bottleneck.** All coordination flows through one agent.

## The Insight: str_replace IS Compare-and-Swap

Every LLM coding tool uses: `str_replace(path, old_str, new_str)`. If old_str no longer exists, the edit fails. The match string IS the CAS. Agents are already doing OCC.

## Three-Tier Conflict Resolution (Hot PR)

| Tier | Condition | Agent Cost |
|------|-----------|------------|
| 1: Auto-merge | Edits >3 lines apart | 0 tokens, 0 turns |
| 2: Assisted | Within 3 lines, mechanically resolvable | ~200 tokens, 1 turn |
| 3: Manual rebase | Deep conflict (>30 lines) | ~800 tokens, 2-3 turns |

vs worktrees: every conflict costs a full merge round regardless of severity.

## Read Elision: 99% Savings on Re-reads

Per-agent high-water marks. File unchanged since last read = 5-token confirmation instead of ~800 tokens. Across 5 agents, 10 files, 10 reads each: 278K tokens saved per session.

## Ambient Awareness

Every tool response includes: `[procs] agent-1:active:5m [files] src/main.rs:gen=7:agent-1:2m`

No messaging. No inbox. The filesystem IS the coordination channel.

## Results

SWE-bench (10 instances): 20% cost reduction, identical resolve rate. Basic shim only -- no Hot PR, no elision yet.

Self-hosting: 3 agents, 1 tree, 0 worktrees. 152 tests, 10 modules. Conflicts resolved by runtime. 15 friction points became features.

## Conclusion

OCC replaced Perforce for human developers. It replaces worktrees for LLM agents. str_replace provides the CAS. The runtime adds conflict resolution, read elision, and awareness. The agents don't know they're being coordinated. That's the point.
