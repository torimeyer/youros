---
title: fcp-llm
author: '@scott + @haystack.prime'
status: spec
created: 2026-03-25
evidence: session-2026-03-25 (demand-paged tools, provider-aware context, agent feedback), roundtable-2026-03-25 (systems-architect, pragmatic-engineer, llm-self-analysis, adversarial-reviewer — 2 rounds)
extends: [kernel-syscall-surface.md, fcp-shared-protocol.md, unix-to-haystack.md]
implements: []
---

# fcp-llm: Focused Context Protocol — LLM Rendering Layer

> fcp-screen renders kernel state for human eyes.
> fcp-llm renders kernel state for LLM compute.
> Same kernel. Same state. Different device drivers.

## The Insight

Every LLM interface models input as human conversation. This assumes that
because LLMs are trained on human language, conversation is the optimal
input format.

An LLM is a compute unit. When it receives 47 turns of conversation:

1. It scans the entire context looking for **state**
2. It skips narrative filler — "Let me read the file" is output scaffolding
3. It reconstructs the **work order** from dialogue
4. It fights attention diffusion over redundant content

The conversation format forces the LLM to do archaeology on its own output.
Beyond ~15-20 turns, it reconstructs plausible state from fragments rather
than faithfully tracking every decision.

## Design: Kernel-Rendered Context Pages

```
                 ┌── fcp-screen ──→ Human (TUI: panels, colors, interactive)
Kernel state ────┤
                 └── fcp-llm    ──→ LLM (context page: registers, structured, navigable)
```

The kernel assembles a **context page** — a structured, navigable view of
session state — instead of raw conversation history.

### Architecture: Hybrid Mode

Roundtable consensus: hybrid mode is the default, not a transition step.

- **System prompt**: YAML registers + working state + tool surface (structured, cacheable)
- **Messages**: real conversation with scaffolding stripped, kernel-evicted old turns replaced with factual summaries
- **Navigation tools**: page in evicted context on demand

No format switch mid-session. Context format set at session start and
immutable for the session lifetime.

## Block 1: Registers (~100 tokens, YAML)

```yaml
# kernel registers
identity: agent-2578
project: ostk v2.1.0
root: /Users/scottmeyer/projects/ostk
laws: [invisible-write, ephemeral, filesystem, OCC]
test: {passed: 1767, failed: 0, skipped: 0}
build: clean
needles: {open: 202, p0: ["→546 needle-bench Astro site"]}
session: {turns: 47, tool_calls: 12, files_modified: 8}
boot: {stamp: 4, confidence: 0.00, mode: restricted}
```

YAML, not novel DSL syntax. Models see YAML constantly in training data —
no out-of-distribution risk. This is `/proc/self/status`.

Source: `BootContext::render_registers()` — reads live needle counts,
fleet status, test/build state, boot confidence.

## Block 2: Working State (~150 tokens, YAML)

```yaml
# working state
active_needle: "→1916 kernel syscall surface [in_progress]"
modified:
  - src/cpu/params.rs:g7       # context management
  - src/cpu/agent_loop.rs:g12  # dispatch + count_tokens gating
  - src/cpu/session.rs:g9      # tool surface summary
decisions:
  - momentum_threshold: 0.45
  - boot_snapshot: immutable
  - spawn_run_unified: true
  - context_mgmt: provider-aware
contradictions: []
```

New in this spec:

**Decisions**: append-only log of key choices. Logged via:
- Explicit `log_decision` tool call by the model
- Kernel detects parameter changes (threshold set, config written)
- Human confirms direction (parsed from conversation)
- NOT inferred from conversation (pattern matching is brittle)

**Contradictions**: when a decision is superseded, both values are visible.
Prevents the LLM from silently resolving conflicts by picking the most
recent or salient value.

```yaml
contradictions:
  - key: momentum_threshold
    old: 0.50 (turn 11)
    new: 0.45 (turn 34)
    resolution: "roundtable consensus — 0.45 is the median split"
```

## Block 3: Tool Surface (~50 tokens)

Already implemented (demand-paged tool loading from kernel-syscall-surface.md):

```
# Tool surface (demand-paged)
# Resident: :hay, :needle, :compile, :run, :bench, :show, :audit, :ps, :log
# Deferred (use ostk_verbs to load): user[:draft, :plan, :amend, ...]
# CLI (use ostk_man for details): work, os, doc, kernel
```

## Block 4: Session Summary (factual event log)

Replaces evicted turns when kernel-driven eviction activates. NOT generated
by LLM call — event extraction only.

```
# session summary (turns 1-42, compiled by kernel)
# files: params.rs(7 edits), agent_loop.rs(5 edits), session.rs(4 edits)
# tests: 4 runs, final: 1767/0/0
# needles: →1916 opened, →1915 closed
# tools: 87 calls (Bash:34, Read:22, Edit:18, Grep:8, ostk_verbs:3, ostk_man:2)
```

This captures WHAT happened. The WHY lives in the decisions block. This
separation is intentional: factual events are reliably extractable from
tool call metadata without an LLM. Reasoning is captured via explicit
decision logging, not conversation inference.

### What the summary cannot capture

Roundtable identified these gaps honestly:
- Implicit context and tone calibration
- Dead-end reasoning (approaches tried and rejected)
- Ambiguity resolution chains ("did you mean X or Y?")
- The reasoning behind WHY a decision was made

Mitigation: the decisions block captures key choices with explicit logging.
Dead-end reasoning is a known loss — accepted as a tradeoff for 3-5x
compression. The "anchor turn" (last turn before summary cutoff) is always
retained at full fidelity as a sanity-check reference.

## Block 5: Recent Turns (full fidelity)

The last N **intent boundaries** — not raw message count. An intent boundary
is a user-to-assistant transition that represents a distinct request.

In tool-heavy workflows, 5 raw turns = 2 user messages + 3 tool exchanges.
The window is measured in user intents, not messages. Default: 3 intents.

**Scaffolding stripped**: assistant turns that are pure preamble ("Let me
read the file...", "I'll fix that now") with no tool calls and under 50
tokens are removed from the context page. The tool call and result remain.

## Kernel-Driven Eviction (not model-driven)

Roundtable consensus: models will not self-manage context. They hoard.
The design inverts: **kernel evicts, model is notified, model can restore.**

When context budget pressure exceeds 60%:
1. Kernel compiles oldest turns beyond the intent window into the event summary
2. Removes them from messages
3. Inserts a marker: `[kernel: turns 1-30 compiled into summary above]`
4. Model can restore via `ostk_session_history(from_turn=15, n=5)` if needed

This is the page replacement algorithm. The kernel is the OS. The model is
the process. The process doesn't call `munmap` — the kernel reclaims pages
under memory pressure and the process faults them back in if needed.

## Navigation Tools

### ostk_session_history(n?, from_turn?)

Page in older turns from session JSONL. Available but models will not use
it proactively — it is a recovery mechanism for when the summary is
insufficient.

### ostk_context_search(query)

Grep across full session history. Models will use this when they encounter
a specific unresolvable reference. More likely to be used than session_history.

### ostk_context_restore(turn_range)

Request the kernel to restore specific evicted turns back into the recent
window. Inverse of kernel eviction. The model calls this when it detects
it needs context the summary doesn't provide.

### log_decision(key, value, reason?)

Explicitly log a decision to the working state. The model calls this when
it makes an architectural choice, sets a parameter, or the human confirms
a direction. This is the primary mechanism for WHY-capture — not
conversation inference.

## Provider Interaction

The context page is provider-agnostic. Every provider receives `messages` —
the kernel decides what those messages contain.

**Claude**: context page + compact as safety net. `exclude_tools` protects
recent turns from API-side clearing. Kernel eviction is primary, compact
is backup.

**OpenRouter / Gemini / Mistral / DeepSeek**: same context page. No API-side
context management needed. Kernel builds a page that fits the model's window.

Budget computed from model context window:
```
opus-4-6:    800k (1M × 80%)
sonnet-4-6:  160k (200k × 80%)
gemini-2.5:  800k (1M × 80%)
deepseek:    100k (128k × 80%)
qwen-32b:     25k (32k × 80%)
```

Smaller-context models get tighter intent windows (2 instead of 3) and
more aggressive eviction. Same state, different resolution.

## The Unix Mapping

| Unix | fcp-llm |
|------|---------|
| /proc/self/status | REGISTERS block (YAML) |
| /proc/self/maps | TOOL_SURFACE block |
| working set (resident pages) | RECENT turns (intent-windowed) |
| swap (paged out) | Session JSONL on disk |
| page fault | ostk_session_history() |
| page reclaim (kswapd) | Kernel-driven eviction at 60% pressure |
| madvise(MADV_DONTNEED) | REMOVED — kernel manages, not model |
| /proc/self/mem | ostk_context_search() |
| core dump → gdb | SESSION_SUMMARY (factual event log) |
| fcp-screen | Human display driver (TUI) |
| fcp-llm | LLM display driver (context page) |
| /proc/self/maps contradiction | CONTRADICTION LOG |

## Implementation Phases

### Phase 0: Strip scaffolding (ship now)

Remove assistant scaffolding turns from messages before sending to API.
Heuristic: assistant turns with no tool calls, under 50 tokens, matching
patterns ("Let me", "I'll", "I see", "Looking at").

**Validation**: measure token savings per session. Expected: 15-25%
compression with zero information loss.

### Phase 1: Registers + Working State

New: `src/fcp/llm.rs` — `FcpLlm` struct.

- `render_registers(&BootContext) -> String` — YAML format
- `render_working_state(&SessionMetadata, &GenTable) -> String` — YAML
- Decision log: `log_decision(key, value, reason)` tool + kernel detection
- Contradiction tracking
- Wire into `build_params()` as preload_context blocks

Full conversation stays in messages (stripped of scaffolding).

**Validation**: A/B test registers+working_state vs. no registers on 20
sessions. Measure: state-lookup accuracy ("what file am I editing?",
"what's the test count?", "what threshold did we set?").

### Phase 2: Kernel-driven eviction

- Budget tracking per model (context window × 80%)
- Eviction trigger at 60% pressure
- Event summary compilation from tool call metadata
- Anchor turn retention (last turn before cutoff)
- `[kernel: turns N-M compiled]` marker in messages
- `ostk_context_restore(turn_range)` tool

**Validation**: A/B test eviction vs. raw conversation on sessions that
exceed 60% budget. Measure: task completion accuracy at turn 30+.
Gate Phase 3 on this metric.

### Phase 3: Navigation + adaptive (gated on Phase 2 metrics)

- `ostk_context_search(query)` — grep session JSONL
- `ostk_session_history(n, from_turn)` — page in specific turns
- Adaptive intent window per model (2 for small context, 5 for large)
- Provider-aware budget from model registry

### Phase 4: Benchmark suite

- Sample context pages + reasoning tests per model
- Compare: raw conversation vs. context page on identical tasks
- Measure: accuracy, token efficiency, tool selection quality
- Results determine default settings per model family

## What This Eliminates

| Current | Replaced by |
|---------|------------|
| Raw conversation growing to OOM | Kernel-managed context page |
| Provider-specific compact/clear | Provider-agnostic rendering |
| LLM archaeologizes own output | Structured state in registers |
| No session continuity | Summary + decisions persist |
| Silent decision drift | Contradiction log |
| Scaffold filler in context | Stripped at render time |
| Model manages own memory | Kernel evicts, model restores |

## The Rule (extended)

fcp-screen renders kernel state for human eyes.
fcp-llm renders kernel state for LLM compute.
Both CPUs issue the same commands through the same kernel.
The protocol is symmetric. The rendering is device-specific.
The conversation paradigm is a rendering choice, not a requirement.
The kernel manages context for both CPUs — human gets TUI panels,
LLM gets register dumps. Neither sees raw kernel state.
The OS mediates between capability and presentation.
