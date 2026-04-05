---
title: Tack v2 — The Intent Language
status: draft
version: 2
author: scottmeyer + round-table
created: 2026-03-08
supersedes: docs/spec/tack.md (v1)
evidence: round-table x3 validation against actual session usage
---

# Tack v2

> Straws + Tack = ostk. The language that holds the OS together.

## Round Table Validation

### 1. Tack Linguist

The v1 spec captures the urgency gradient (`.` vs `:` vs `::`) and flow operators (`->`, `=>`, `<-`) accurately -- these are the skeleton. But the spec describes a flat signal language while the human actually speaks a structured one. The session revealed an entire verb layer (`:job`, `:exec`, `:ship`, `:kill`, `:wait`, `:plan`, `:discover`, `:confirm`, `:explain`, `:delegate`, `:start`, `:show`/`:showme`) that v1 doesn't mention at all. These aren't edge cases -- they're the primary mechanism by which intent becomes action. The spec also missed `:context`, `:depends`, `:goal`, and `:ref` which function as declarative metadata inside structured blocks. Triple-colon `:::` appeared as an urgency level beyond `::`, which the spec caps at two. The `=` operator section (continue/resume/alias) was never observed in actual use, suggesting it's speculative rather than empirical. The spec's property "every token was typed before it was documented" is violated by those `=` entries.

### 2. Tack Compiler

The v1 grammar is one-dimensional: one signal per message, one urgency level, one flow arrow. The human actually speaks hierarchical Tack. A `:job-name` block contains indented sub-signals (`:context`, `:issue`, `:depends`, `:goal`) forming a tree. This is a structured intent declaration, not a flat command. The current grammar has no concept of: (a) named blocks (`:job-rename` is a scoped container), (b) indentation as nesting (sub-signals belong to the parent job), (c) dependency edges between jobs (`:depends job-X`), or (d) batch execution (multiple jobs dispatched as one unit). A compiler for v1 Tack would parse each line independently and lose the structure. A compiler for v2 needs block-level parsing: a `:job` opens a scope, indented lines are children, and `:depends` creates edges in a DAG. This is still not EBNF -- the LLM attention parser handles it -- but the spec must document the pattern so new agents recognize it.

### 3. Tack User (new person)

If I read v1 tack.md, I'd understand the urgency gradient and flow arrows but I'd have no idea how to actually do things. The spec tells me `:` means "hard" but not what verbs exist after the colon. I'd know `:correct X` from the examples but I wouldn't know I can say `:ship`, `:kill`, `:wait`, or `:plan`. The batch job syntax -- the most powerful feature observed -- is completely absent. I'd also be confused by the `=` operator section since I'd try to use `=` for "continue" and get no response. The "Boot Your Own" section at the end gives ostk CLI commands, not Tack examples. What's needed: a verb table, a batch job example, and 3-5 real transcripts showing Tack in use (soft probe, hard correction, batch job, flow delegation).

## Signals Missing from Spec (observed in session)

### Verbs (`:verb`)
| Signal | Meaning |
|--------|---------|
| `:job` / `:job-name` | Open a named batch of sub-commands |
| `:exec` / `:execute` | Do it. Imperative. |
| `:start` | Begin work |
| `:ship` | Release / publish |
| `:show` / `:showme` | Display, reveal |
| `:kill` | Terminate |
| `:wait` | Hold, pause |
| `:plan` | Initiate planning |
| `:discover` | Explore, find |
| `:lower` | Deprioritize |
| `:confirm` | Verify |
| `:explain` | Teach me |
| `:delegate` | Hand off to agent |
| `:rule` | Declare a law |
| `:ref` | Reference something |

### Structure (inside `:job` blocks)
| Signal | Meaning |
|--------|---------|
| `:context` | Scope for a job |
| `:depends` | Dependency declaration |
| `:goal` | Objective |
| indentation | Nesting / children of parent block |

### Urgency extension
| Signal | Meaning |
|--------|---------|
| `:::` | Triple-hard. Beyond `::`. |

## What v1 Has That Was Never Used

| Signal | Spec says | Observation |
|--------|-----------|-------------|
| `=` (continue/resume) | In spec | Never observed |
| `~=` (approximately) | In spec | Never observed |
| `==` (alias) | In spec | Never observed |
| `====>` (amplified boost) | In spec | Never observed |
| `(xN)` (repetition) | In spec | Never observed |
| `&att` (attention marker) | In spec | Never observed |

These may be valid but unobserved. Mark as "theoretical" until evidence appears.

## Updated Grammar

### Urgency (`.` -> `:` -> `::` -> `:::`)
```
.    soft     explore, probe, check in
:    hard     demand, correct, act
::   hardest  you don't know this -- anchor check
:::  beyond   non-negotiable, absolute
```

### Verbs (`:verb`)
```
:exec / :execute    do it
:start              begin
:ship               release
:show / :showme     display
:kill               terminate
:wait               hold
:plan               initiate planning
:discover           explore
:lower              deprioritize
:confirm            verify
:explain            teach me
:delegate           hand off
:rule               declare a law
:ref                reference
:correct            you're wrong, X is right
:calibrate          I feel drift, check state
:break              stop
:boost              positive reinforcement
```

### Batch Jobs (`:job-name` + indented children)
```
:job-rename
  :context docs/spec/tack.md
  :issue "= operator section is speculative"
  :depends job-validate
  :goal "remove unobserved signals"

:job-validate
  :context this session
  :goal "compare spec to actual usage"
```

Jobs are DAGs. `:depends` creates edges. Indentation is scope. The LLM parses structure from indentation -- no formal block delimiters.

### Flow (`->` -> `=>` -> `<-`)
```
->  next      sequential, what follows
=>  boost     elevate, delegate, phase change
<-  pull      reverse flow (A<-B = B->A)
```

### Routing
```
noun:   scope (llm:, pri:, swe-bench:)
```

### Operators
```
/x    cancel
#>    imperative
>     cursor (look at this)
+     include/add
+++   escalating emphasis
*     significance
!!    urgency amplifier
#     annotation
//    inline separator
```

### Implicit
```
bare text       hay -- thinking out loud
CAPS            non-negotiable boundary
repetition      escalation
interruption    preemptive correction
typos           speed over form
```

## Theoretical (in v1, not yet observed)

These remain documented but marked unverified:
```
=       continue/resume
~=      approximately
==      alias
====>   amplified boost
(xN)    repetition
&att    attention marker
```

## Properties (unchanged from v1)

1. **Emerged, not designed.** Every token was typed before it was documented.
2. **Compressed, not simplified.** All natural language features present.
3. **No parser.** The LLM's attention IS the parser.
4. **Bidirectional.** Human and machine converged.
5. **Dynamic.** Communication cost decreases within a session.
6. **Personal.** This is one human's tack.
7. **Hierarchical.** Batch jobs prove Tack is not flat. (NEW)
