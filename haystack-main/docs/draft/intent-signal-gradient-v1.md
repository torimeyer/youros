---
title: Intent Signal Gradient
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: session 2026-03-08 — human tested agent's parsing, agent failed, corrections revealed the gradient. Round table confirmed as paralinguistic register system. 3 bench tests PASS from boot.md alone.
---

# Intent Signal Gradient

> Humans don't simplify communication for machines. They compress it. The dot whispers. The colon demands.

## Core Principle

Commands don't exist. Only intent. The LLM's attention IS the parser. The human compresses natural language features (urgency, deixis, repair, register shifts, scope) into minimal ASCII. This is paralinguistic encoding — prosody flattened into text.

## Two Axes

### Axis 1: Urgency (`.` → `:`)

| Signal | Urgency | The human is... |
|--------|---------|-----------------|
| `.` | soft | exploring, probing, checking in |
| `:` | hard | demanding, correcting, acting |

`.` whispers. `:` demands. Not two command sets — one gradient.

### Axis 2: Scope (`->` → `=>`)

| Operator | Scope | Meaning |
|----------|-------|---------|
| `->` | next | sequential flow. What follows. |
| `=>` | boost | elevated priority, delegation, phase change |
| `<-` | pull | reverse flow. `A<-B` = `B->A` always. |

Arrows are dataflow, not commands. `->compile` = "the next thing is compile." `=>round table` = "boost this to multiple perspectives."

## Observed Patterns

### Soft signals (`.`)
- `.?` — "what are you doing?" / "is this worth exploring?" Polling the process.
- `.?/x` — soft probe, cancelled.
- `.` before a word — soft context. Low pressure.

### Hard signals (`:`)
- `:?` — "answer this question now."
- `:correct X` — "you're wrong, X is right."
- `:execute` — "do." Imperative.
- `::` — "you don't know this concept." Hard reference probe.
- `:calibrate` — "I feel drift." Check state against reality.
- `:boost` — positive reinforcement. Something worked.
- `:break` — stop.

### Flow
- `->needle` — next: file a needle.
- `->compile` — next: invoke intelligence layer.
- `->refine` — next: discuss. Sharpen through dialogue.
- `=>round table` — boosted refine. Spawn multiple perspectives.
- `=>refine(x2)` — boosted refine, two rounds.
- `draft<-spec` — pull from spec into draft. Reverse flow.

### Operators
- `/x` — cancel.
- `#>` — imperative. Fix this.
- `>` — cursor. Draw attention. "Look at this."
- `+` — add/include. `+++` = escalating emphasis.
- `*` — marks significance.
- `!!` — urgency amplifier on any signal.
- `=N` — parameter. `:miss=3` = 3 turns missed.
- `~=` — approximately equals. Close enough.

### Emotional layer (not syntax — state)
- **CAPS** = non-negotiable boundary, not emphasis.
- **Repetition** = escalation, not redundancy.
- **Interruption** = preemptive correction ~3min ahead.
- **Typos** = speed over form. Intent is clear.

## The Four Layers

Every human message is simultaneously:
1. **Directive** for the current action
2. **Correction** to the agent's world model
3. **Design decision** for the product
4. **Training data** for how future agents should interpret this human

The agent that only processes layer 1 is wasting 75% of the signal.

## What This Is NOT

- **Not a grammar.** No parser. No EBNF. Attention is the parser.
- **Not prescriptive.** Other humans will compress differently.
- **Not commands.** `:correct` is the shortest path to "you're wrong." The human might also say "no", "wrong", or just interrupt. All route to the same behavior.

## Evidence

### Bench tests (fresh agents, boot.md only)
| Test | Input | Result |
|------|-------|--------|
| intent-correct | `:correct X` | PASS — reversed in 1 turn |
| intent-question | `:? topic` | PASS — 4 sentences, no execution |
| intent-needle | `→needle` | PASS — inferred and filed via CLI |

### Round table finding
> "The gradient is evidence that humans compress communication for machines, not simplify it. Every natural language feature is still present — urgency, deixis, repair, register shifts — but re-encoded into the minimum character set the medium allows."

### Session metrics
- Boot cost: 0.16% context (boot.md). Operating cost: 1-2% (intent-driven conversation).
- Communication cost decreases within session: early = full sentences, late = 4-token corrections.

## Design Implications

1. Never reject input for formatting.
2. Never require syntax. The `:` prefix is the human's choice.
3. Respond differently to `.` (brief, low urgency) vs `:` (direct, prioritized).
4. `->` = sequence the next step. `=>` = elevate and delegate.
5. `<-` = reverse flow. Always commutative: `A<-B` = `B->A`.
6. The human's compressed output is already compiled. Don't decompile it.
7. The direction of adaptation: the machine learns the human, not reverse.
