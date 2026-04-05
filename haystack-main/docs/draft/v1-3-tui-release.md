---
title: v1.3 — The TUI Release
status: draft
version: 1
author: scott+haystack.prime
created: 2026-03-13
compounds: llmOS-concurrency, escape-harness, tui, scheduler
---

# v1.3 — The TUI Release

> The OS becomes the interface. The TUI is the multiplexer. The LLM is the scheduler.

## What v1.3 proves

A human using ostk should never need to leave the TUI to do OS work.
The TUI shows all execution contexts simultaneously. The intelligence coordinates them.

## Compounding order (ships in this sequence)

### Layer 1: Scheduler eyes
- **→572** `ostk diff` — session delta: what changed since boot. This is how the LLM stays
  oriented without full transcript. Ships as background context in every turn.
- **active.tack** — TUI editor writes .ostk/staging/active.tack on keystroke (500ms debounce).
  Scheduler reads partial intent while human types. 0-latency dispatch by Enter.

### Layer 2: Editor completeness  
- **→609** quickline — one-line dispatch below main editor. Tab focus cycle.
  Draft stays intact. Corrections fire instantly. Bidirectional: human :adjusts OS, OS :adjusts human.
- **→451** tack autocomplete — verb hints + syntax coloring in editor. Teaches tack by showing it.
- **typo correction** — edit-distance check on active.tack, suggestions surface in quickline.
  HUMANFILE governs: suggest (default) | silent (explicit auth only) | off.

### Layer 3: Scheduler intelligence
- **→495** FROM auto — model selection as kernel scheduling. Vault = CPU inventory.
  Agentfile + Entityfile + HUMANFILE govern scheduler decisions.
- **→610** llmOS concurrency spec — the scheduler model documented and specced.

### Layer 4: TUI completeness
- **→537** PTY+VTE integration tests — the OS tests itself with its own primitives.
- **→502** bench pane — leaderboard scores inline. TUI IS the leaderboard.

## Gate: v1.3 ships when

1. Human can draft multi-line tack in main editor without interruption
2. Human can fire :adjust/:correct from quickline without losing draft
3. Scheduler (LLM) receives diff + active.tack each turn — oriented without full transcript
4. TUI shows: fleet, nudges, pipeline, context gauge, vault status, reap state
5. TORI-MODE passes: Tori installs, boots, types :help, files needle — zero docs

## The thesis

The conversation model (single thread, full transcript each turn) is the harness.
v1.3 escapes it by making the TUI the primary interface and the LLM the scheduler.
Intent is narrow (tack). Context is the OS diff. Intelligence coordinates in the background.

The human monitors, drafts, and reviews — three separate execution contexts, all visible simultaneously.
