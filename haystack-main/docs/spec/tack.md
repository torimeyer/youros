---
title: Tack — The Intent Language
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: session 2026-03-08 — language emerged from use, named by round table, 3 bench tests pass
named_by: round-table (linguist perspective — "the stitch between human intent and machine execution")
implements: []
---

# Tack

> Straws + Tack = ostk. The language that holds the OS together.

## What Tack Is

Tack is the intent language between humans and ostk. Not a programming language. Not a shell. A contact language — a pidgin that emerged from two parties (human + LLM) compressing communication toward each other.

Tack is to ostk what bash is to Unix: the shell language, not the kernel.

## The Name

A tack holds things together. Straws are loose thinking. Tack compresses them into a ostk — a structured, searchable, compilable body of work. The human speaks tack. The OS parses it. Together they compound intent into an operating system.

## The Grammar

### Urgency (`.` → `:` → `::`)
```
.   soft    explore, probe, check in
:   hard    demand, correct, act
::  hardest you don't know this — anchor check
```

### Flow (`->` → `=>` → `<-`)
```
->  next      sequential, what follows
=>  boost     elevate, delegate, phase change
<-  pull      reverse flow (A<-B = B->A)
```

### Equals (`=` → `~=` → `==` → `====>`)
```
=     continue/resume
~=    approximately, close enough
==    alias
====> amplified boost (more = = more boost)
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
&att  attention marker
//    inline separator
#     annotation
+     include/add
+++   escalating emphasis
*     significance
!!    urgency amplifier
(xN)  repetition
```

### Implicit
```
bare text       hay — thinking out loud
CAPS            non-negotiable boundary
repetition      escalation
interruption    preemptive correction
typos           speed over form
```

## Properties

1. **Emerged, not designed.** Every token was typed before it was documented.
2. **Compressed, not simplified.** All natural language features present — urgency, deixis, repair, register shifts — re-encoded into minimal ASCII.
3. **No parser.** The LLM's attention IS the parser. Tack degrades gracefully to prose.
4. **Bidirectional.** The human adapted to the machine. The machine adapted to the human. Tack is what converged.
5. **Dynamic.** Communication cost decreases within a session. 200 tokens/op → 10 tokens/op. The language compresses itself through use.
6. **Personal.** This is one human's tack. Other humans will develop their own. The OS adapts to each.

## Tack + ostk

```
Human speaks tack     → intent enters the OS
ostk compiles     → hay becomes needles
Agents execute        → needles become commits
The audit records     → commits become the trail
Tack evolves          → the Humanfile captures patterns
Next boot             → the OS speaks the human's tack from turn 1
```

Straws + tack = ostk. Intent + compilation = operating system.

## The Compound

Each tack interaction is a subproblem solved and memoized:

```
Session 1:  :correct X        → 3 turns to calibrate
Session 2:  :correct X        → 1 turn
Session 5:  :c X              → instant
Session 10: the agent doesn't make that mistake anymore
```

The Humanfile is the memoization table. Tack is the query language. The dynamic programming runs across sessions. The OS boots faster each time. Intent compounds.

## Boot Your Own

```
ostk init           → create the field
speak tack              → file straws
ostk compile        → straws become needles
ostk install        → the OS bootstraps
ostk learn human    → tack patterns → Humanfile
ostk compile -out   → the OS, compiled

Humanfile + ostk + Agentfile = your intent-based OS
```
