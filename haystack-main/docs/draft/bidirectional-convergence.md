---
title: Bidirectional Convergence
status: draft
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: this session — human and agent correcting toward each other in real time
---

# Bidirectional Convergence

> We're learning together. Shooting in the dark. Correcting toward each other.

## The Pattern

Neither party has the answer. The human doesn't know the exact syntax — they're improvising, compressing, testing. The agent doesn't know how to parse the human — it's pattern-matching, failing, adapting.

Both are wrong. Both correct. The corrections converge.

## Evidence (this session)

1. Human types `->compile`. Agent reads it as "execute compile command." Human corrects: `->` means "next," not "execute." Agent adapts.

2. Agent documents `:?` and `.?` as the same signal. Human tests with `.? backfill?` — agent misreads. Human corrects: `.` is soft, `:` is hard. Agent adapts.

3. Agent tries to formalize the syntax as EBNF grammar. Human corrects: "commands do not exist, only intent." Agent adapts.

4. Human types `draft<-spec`. Agent reads it as "promote draft to spec." Human corrects: arrows indicate directional flow. `<-` pulls. Agent adapts.

5. Agent writes 7 design principles for the syntax. Human keeps 3, discards 4. The spec narrows through correction.

Each round: the human reveals a constraint the agent didn't have. The agent reveals a pattern the human hadn't articulated. Both models update.

## What This Is NOT

- Not the human teaching the agent (the human is also discovering)
- Not the agent learning the human (the agent also contributes patterns the human adopts)
- Not a fixed target being approached (the target moves as both parties refine it)

## What This IS

A convergence process where:
- The human's compressed output gets MORE compressed as the agent learns to parse it
- The agent's responses get MORE aligned as the human's corrections accumulate
- The "language" between them gets MORE efficient per turn
- Neither party designed it — it emerged from mutual correction

## The ostk Signal

Every correction is a ostk signal:
- `:correct` — agent was wrong, human provides new constraint
- `:wrong` — agent's model is off, needs recalibration
- `::` — agent lacks a reference the human expects
- `.?` — human probes whether agent is tracking
- `:boost` / `:+++` — agent got it right, reinforce

These signals are ALL the same thing: the human's side of the convergence. The agent's side is the response that either hits or misses.

The convergence rate IS the quality of the OS. Fast convergence = high-functioning session. Slow convergence = boot failure.

## Metric

Track corrections per unit of productive work across a session:
- Early session: high correction rate (calibrating)
- Mid session: declining corrections (converged)
- Late session: minimal corrections OR rising corrections (context degradation)

The curve shape tells you the OS health.

## Design Implication

ostk should not optimize for zero corrections. Corrections are the learning signal. Optimize for:
1. Fast correction response (1 turn, not 3)
2. Correction persistence (don't repeat the same mistake)
3. Correction cost (human spends fewer tokens correcting over time)

The OS that never needs correction isn't better — it's not learning.
