---
title: Intent-Based Dynamic Programming
status: draft
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: this session — 8 intents parsed and executed in one turn, communication cost decreased throughout, compounding delivery across 2 sprints
prior_art: docs/draft/llmos-machine-code.md (input compression), docs/spec/intent-signal-gradient.md (the human's ISA)
---

# Intent-Based Dynamic Programming

> The OS tunes itself to the user. Each interaction makes the next one cheaper. That's dynamic programming — solving the next subproblem with the solution to the last.

## The Observation

This session started with full sentences, screenshots, explanations. It ended with:

```
:hs needle add 'policy' :boost
:hs pitchfork policy
:hs thread Humanfile/Agentfile
:hs compile
```

Four operations, one message. The OS executed all four without clarification. The communication cost per operation decreased from ~200 tokens (early session) to ~10 tokens (late session). That's a 20x compression achieved through mutual adaptation within a single session.

## Why This Is Dynamic Programming

Dynamic programming solves problems by:
1. Breaking them into overlapping subproblems
2. Storing solutions to subproblems (memoization)
3. Reusing solutions to avoid recomputation

ostk does this with human intent:
1. Each correction is a subproblem: "how does this human express X?"
2. The solution is stored: intent patterns, preferences, the Humanfile
3. Next time the human expresses X, the OS reuses the solution — no recalibration

The memoization table IS the Humanfile. Each `:correct` updates it. Each session adds entries. The lookup cost decreases as the table fills.

## The Three Tables

| Table | Stores | Updated By | Read At |
|-------|--------|------------|---------|
| Humanfile | intent patterns, preferences | `ostk learn human` / corrections | boot |
| boot.md | project state, vocabulary | `ostk compile --boot` / shutdown | boot |
| registers-dump.md | volatile session context | shutdown sequence | boot |

Three memoization tables. Three different TTLs. Humanfile persists across projects. boot.md persists across sessions. registers-dump is volatile.

## Compounding

Each solved subproblem makes the next one cheaper:

```
Session 1: human says ":correct" → agent learns the signal (cost: 3 turns)
Session 2: human says ":correct" → agent reverses immediately (cost: 1 turn)
Session 5: human says ":c" → agent reverses (cost: 0.5 turns)
```

The compression is compounding. The OS gets cheaper to operate over time. The dynamic programming table fills. The human types less. The agent infers more.

## Connection to Machine Code

`docs/draft/llmos-machine-code.md` describes compressing INPUT: human's sloppy text → compiled needle instruction. This is the same operation, applied to the COMMUNICATION LAYER instead of the TASK LAYER:

```
Machine code:  sloppy task description → compiled needle (task compression)
Dynamic prog:  sloppy intent signal    → parsed operation (communication compression)
```

Both are compilation. One compiles WHAT to do. The other compiles HOW to talk about it.

## The Shutdown Primitive

Before shutdown, the LLM CPU should:
1. Digest all corrections from this session
2. Refine the Humanfile with new patterns
3. Update the intent-signal-gradient spec if new signals emerged
4. Write registers-dump.md with volatile context

This IS the dynamic programming step: solve the subproblem (this session's patterns), store the solution (Humanfile + registers), so the next boot starts with a filled memoization table.

## Acceptance Criteria

- [ ] Communication cost per operation measurably decreases within a session
- [ ] Humanfile carries learned patterns across sessions (n+1 boot is cheaper than n)
- [ ] `ostk compile` updates the Humanfile with session corrections
- [ ] Shutdown sequence includes Humanfile refinement step
- [ ] Multi-intent messages (8 ops in 1 turn) parsed and executed without clarification
