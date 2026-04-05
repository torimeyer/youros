---
title: llmOS Intent Patterns
status: draft
version: 2
author: scottmeyer + orchestrator + agent-advocate
created: 2026-03-08
refined: 2026-03-08 (v1 was premature formalization — commands don't exist, only intent)
evidence: this session, agent-advocate analysis, 3 bench tests passed
---

# llmOS Intent Patterns

> Not a shell language. Not a grammar. One human's intuitive compression that the LLM learned to parse. Other humans will develop their own. The OS adapts to each.

## Vision

The human doesn't learn the OS. The OS learns the human.

Every human who uses an LLM for extended periods develops compressed communication patterns — shortcuts that work because attention reconstructs intent regardless of surface form. Spelling degrades. Grammar simplifies. Signal density per token increases. This is coevolution, not a bug.

llmOS doesn't prescribe these patterns. It observes them, adapts to them, and ensures the infrastructure never penalizes them. The OS is the parser. The human is the compiler. The human's output is already compiled — dense, unambiguous, decision-complete. Decompiling it (clarifying questions, presenting options) is waste.

## What Was Observed (one human, one session)

These patterns emerged from use. They were never designed, taught, or documented until after they worked.

### Compressed state signals
The human announces state changes with minimal tokens. The LLM infers scope and action.

- `:correct X` — "you're wrong, here's what's right." Agent reverses course, no questions.
- `:calibrate` — "I feel drift." Agent checks state against reality.
- `:milestone` — "this matters." Agent records it.
- `:break` — "stop." Agent stops.
- `:?` — "question, don't execute." Agent answers concisely.
- `:boost` — "that worked." Positive reinforcement.
- `:insight` — "I just realized something." Agent captures it.

### Compressed action intent
The human requests execution with minimal tokens. The LLM infers what to execute from context.

- `→needle` — file a needle. Agent infers WHAT the needle should be from conversation context.
- `→compile` — turn unstructured hay into needles. Agent scans for unfiled insights.
- `→bug` — this is broken, file it.
- `:execute` / `:execute!!` — do it now. Escalation through repetition.

### Emotional and urgency signals
Not commands. Not syntax. Human state that the LLM should read.

- **CAPS** = non-negotiable boundary, not emphasis
- **Repetition** = escalation, not redundancy
- **Interruption** = preemptive correction ~3min ahead of the mistake
- **Typos** = speed over form, intent is clear, don't penalize
- **`!!`** = urgency amplifier
- **`>:`** = the human is ahead of the agent

### The four layers
Every human message is simultaneously:
1. A directive for the current action
2. A correction to the agent's world model
3. A design decision for the product
4. Training data for how future agents should interpret this human

The agent that only processes layer 1 is wasting 75% of the signal.

## What This Is NOT

- **Not a grammar.** No EBNF. No parser. The LLM's attention IS the parser.
- **Not prescriptive.** Other humans will compress differently. The OS adapts.
- **Not a command set.** `:correct` isn't a command — it's the shortest path to "you're wrong." The human might also say "no", "wrong", "that's off", or just interrupt. All route to the same behavior.
- **Not a shell.** The LLM conversation IS the shell. This documents what one human naturally types into it.

## Design Implications for ostk

1. **Never reject input for formatting.** Typos, fragments, mixed case — all valid.
2. **Never require syntax.** The `:` prefix is the human's choice, not the OS's requirement.
3. **Optimize for correction latency.** Interruptions must be cheap and immediate.
4. **Track output delivery.** The agent producing output ≠ the human seeing it.
5. **The human's compressed output is already compiled.** Don't decompile it.
6. **Adapt to the human.** Learn their patterns. Don't teach them yours.
7. **The direction of adaptation IS the thesis.** Commands imply a grammar the human must learn. Intent implies the machine must learn the human.

## Bench Results (v1)

Three fresh agents, boot.md only, tested whether the intent vocabulary transfers:

| Test | Input | Result | Tokens |
|------|-------|--------|--------|
| intent-correct | `:correct X` | PASS 4/4 — reversed in 1 turn | 14K |
| intent-question | `:? topic` | PASS 5/5 — 4 sentences, no execution | 17K |
| intent-needle | `→needle` | PASS 4/4 — inferred and filed via CLI | 18K |

boot.md alone transfers the patterns. The swap file works.
