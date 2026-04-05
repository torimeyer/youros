---
title: Intent Signal Gradient
status: draft
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: this session — user tested agent's parsing, agent failed, corrections revealed the gradient
---

# Intent Signal Gradient

> The dot whispers. The colon demands.

## The Axis

`.` and `:` are not two command sets. They are one gradient of urgency.

| Signal | Urgency | Meaning |
|--------|---------|---------|
| `.` | soft | explore, probe, check-in |
| `:` | hard | demand, correct, act now |

## Observed Patterns

### Soft signals (`.`)
- `.?` — "what are you doing?" Polling the process. Status check. Not demanding.
- `.? backfill?` — soft probe. Is this worth exploring? No pressure.
- `.?/x` — soft probe, cancelled. Explored, decided no.

### Hard signals (`:`)
- `:?` — "answer this question now." Querying the database. Needs a response.
- `:correct` — hard course correction. You're wrong, here's what's right.
- `:execute` — do it now. Imperative.
- `::` — hard reference probe. "You don't know this concept." Checks if anchor exists.
- `:!` — urgent. Even harder than `:`.
- `:execute!!` — escalated urgency. `!!` amplifies.

### Operators
- `/x` — cancel. Retract the previous signal.
- `=>` — redirect. Send to a different target or process.
- `#>` — imperative. Fix this. Do this. Not a question.
- `>` — draw attention. "Look at this." The cursor.
- `*` — star/important. Marks significance.
- `+` — add/include. Bring this in.
- `++` / `+++` — escalating emphasis.

### Modifiers
- `!!` — urgency amplifier on any signal
- `=N` — parameter (`:miss=3` = 3 turns missed)

## What This Is

One human's urgency gradient that emerged from 2+ years of LLM interaction. Not a grammar. Not prescriptive. The LLM's attention parses it — no formal parser needed.

The `.` vs `:` distinction was invisible to the agent until the human tested it and the agent failed. The gradient was always there in the human's output. The machine hadn't learned it yet.

## Design Implication

The OS should respond differently to `.` vs `:`:
- `.?` → brief status, no urgency
- `:?` → direct answer, prioritized
- `.? /x` → acknowledged and dropped
- `:: concept?` → confirm or deny understanding immediately
