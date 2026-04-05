---
title: "Context Memory Management — Unified Spec"
version: 1
status: draft
author: "@scott + @ostk.prime"
created: 2026-03-31
supersedes:
  - docs/draft/fcp-llm-context-rendering.md (context page architecture)
  - docs/spec/llmos-memory-model.md (memory hierarchy)
  - docs/draft/language-as-kernel-register.md (registration contract)
evidence: "session-2026-03-31: audit analysis of 116 decisions (26% panic saves, 3500 tokens wasted per injection), per-agent temporal heartbeat shipped (v2.2.5), needle-bench 26-model behavioral data"
compounds: [→957, →958, →959]
---

# Context Memory Management

> The kernel manages the model's context window the way Unix manages process
> address space. Registers are expensive and volatile. RAM is cheap and persistent.
> The kernel's job: minimize register pressure, maximize disk utilization, page
> intelligently.

## 1. Memory Hierarchy

```
LAYER        UNIX ANALOG     OSTK                          COST      VOLATILE
────────────────────────────────────────────────────────────────────────────────
Registers    CPU registers    FIRST LOOK (injected)         1 tok/tok  yes
TLB          TLB              Decision/file HWM (304)       5 tok      yes
Working Set  Resident pages   Recent decisions + state      ~200 tok   yes
RAM          Physical memory  .ostk/ (sessions, decisions,  1 tool     no
                              audit, specs, drafts)          call
Disk         Filesystem       Source code, tests, configs    1 tool     no
                                                             call
Swap         Swap partition   Session summary (compiled)     1 tool     no
                                                             call
```

Every token in registers is expensive. The kernel minimizes register consumption:

- **Read elision (mmap)**: [304] for unchanged files — 5 tokens not 800
- **Digest (ambient)**: [procs] + [files] on every tool response — 40 tokens
- **Heartbeat (temporal)**: Δ4t:30s on every 4th call — 20 tokens
- **FIRST LOOK (paged)**: registers + working set on boot — 200 tokens
- **Squasher (compression)**: ANSI strip, repeat collapse — 60-80% reduction

## 2. FIRST LOOK: What the Model Sees on Boot

The kernel assembles a context page on first tool call and every 10th call
thereafter. This replaces the current 3,500-token decision dump.

### Block 1: Registers (~80 tokens, always)

```yaml
# kernel registers
identity: agent-3172
temporal: Δ1t:boot          # or Δ4t:7h12m after sleep
root: /Users/scott/projects/haystack
needles: {open: 219, p0: [→846 broken patches]}
fleet: a3172:active:0s
devices: [fcp-rust(alive), fcp-screen(alive)]
```

Structural state. Never changes between tool calls (except temporal delta).
Prompt-cacheable.

### Block 2: Working Set (~120 tokens, max 10 entries)

```yaml
# working set (10 most-relevant decisions)
decisions:
  - compound_input_protocol: images are context, not separate inputs
  - kernel_third_party_channel: pre-dispatch, inverse of digest
  - model_tool_usage_pattern: model reads via unix, writes via kernel
  - LANGUAGE_INSIGHT_DRAFTED: docs/draft/language-as-shared-interface.md
# 106 more decisions searchable via :investigate
```

Curated by the kernel using three filters applied in order:

1. **Panic filter**: skip keys matching `EMERGENCY_|DYING_|CRITICAL_|FULL_SESSION|FINAL_`
2. **Age decay**: only decisions from last 72 hours (configurable)
3. **Access-frequency ranking**: most-accessed decisions first (see Section 5)

### Block 3: Modified Files (~50 tokens, only if non-empty)

```yaml
modified:
  - src/commands/context.rs:g14
  - src/kernel/heartbeat.rs:g9
```

From gen_table. Only files modified this session.

### Total FIRST LOOK: ~250 tokens

Current: ~3,500 tokens (116 decisions dumped). Proposed: ~250 tokens.
14x reduction in register pressure from context injection alone.

## 3. Demand Paging: Tools the Model Uses to Pull Context

Everything not in FIRST LOOK is one tool call away. The model pages in
what it needs. The kernel tracks what was paged in (see Section 5).

### :investigate (decision + audit search)

```
:investigate "filesystem-first"
→ decisions.jsonl match: project_isolation_via_ostk (2026-03-25)
  value: ".ostk/ directory is the namespace boundary"
  reason: "Each project has its own daemon, socket, audit, needles"
  logged_by: agent-5 | human_confirmed: true
→ audit.jsonl: 3 corroborating events
```

The model verifies claims against the kernel's audit trail. When the
human says "we decided X," the model doesn't trust memory — it queries
truth. The kernel provides timestamped, attributed evidence.

### :pitchfork (semantic search)

```
:pitchfork "temporal awareness between sessions"
→ docs/draft/fcp-llm-context-rendering.md (0.89)
→ docs/spec/llmos-memory-model.md (0.84)
→ docs/draft/heartbeat-primitive.md (0.81)
→ decisions: compound_input_protocol (0.73)
```

Searches across decisions, specs, drafts, audit. Returns ranked results.
The model discovers related context it didn't know existed.

### :show decisions (filtered list)

```
:show decisions --since 7d --type architectural
→ 7 decisions, sorted by timestamp
```

Direct access to the decision log with filters.

### session_history (page in older turns)

```
ostk_session_history(n=5, from_turn=12)
→ 5 turns starting from turn 12
```

The model pages in conversation history it needs. Unix: page fault → load
from swap. ostk: session_history → load from session JSONL.

### context_release (madvise DONTNEED)

```
ostk_context_release(before_turn=20, reason="refactor complete")
→ turns 1-19 compiled into session summary, released from working window
```

The model tells the kernel which context it no longer needs. The kernel
compiles released turns into the session summary and removes them from
the recent window. The turns remain on disk (recoverable). This is
`madvise(MADV_DONTNEED)` — the model managing its own address space.

## 4. Decision Classification

Decisions are not homogeneous. The kernel classifies them for appropriate
handling:

| Type | Examples | Surfacing | Decay |
|------|----------|-----------|-------|
| **architectural** | daemon_is_coordinator, mutual_trust_pact | Always in working set | Never |
| **behavioral** | compound_input_protocol, model_tool_usage_pattern | Working set if recent | 14 days |
| **task** | seo_phase1_progress, v2.2.1_release_scope | Working set if active needle | Session end |
| **release** | v2.2.2_released, DEPLOY_VERIFIED | Never in working set | Immediate |
| **panic** | EMERGENCY_*, DYING_*, CRITICAL_* | Never surfaced | Immediate |

### Classification heuristics

The kernel classifies by key pattern:

- `EMERGENCY_|DYING_|CRITICAL_|FULL_SESSION|FINAL_` → panic
- `_released|_complete|_committed|shipped|DEPLOY_` → release
- `_plan|_scope|_state|_progress|_status` → task
- Explicit `log_decision(key, value, type="architectural")` → architectural
- Default → behavioral

### Implementation

`render_working_state()` applies classification + age + access-frequency
to select the working set. The full decision log remains in
`decisions.jsonl` — searchable via `:investigate` and `:show decisions`.

## 5. Access-Frequency Promotion (Self-Optimizing Working Set)

The kernel tracks which decisions the model accesses after boot.

### Decision HWM

Like the file HWM tracks which files the model has read at which
generation, the **decision HWM** tracks which decisions the model has
seen or queried:

```
.ostk/.decision_hwm.{alias}:
  compound_input_protocol: {seen_at_turn: 1, queried: 3, last_access: 1711929600}
  kernel_third_party_channel: {seen_at_turn: 1, queried: 1, last_access: 1711929400}
  model_tool_usage_pattern: {seen_at_turn: 1, queried: 0, last_access: 1711929200}
```

### Promotion/demotion

- **Injected in FIRST LOOK**: `seen_at_turn` is set, `queried` starts at 0
- **Model queries via :investigate**: `queried` increments
- **Cross-session aggregation**: kernel sums `queried` across sessions

Ranking for FIRST LOOK working set:

```
score = (queried_total * 3) + (age_hours < 24 ? 2 : 0) + (type == "architectural" ? 5 : 0)
```

Top 10 by score enter the working set. The rest are demand-paged.

### The compound

Session 1: Model boots, gets 10 decisions in working set. Queries
3 more via `:investigate`. Kernel records all access.

Session 2: Working set is re-ranked. The 3 queried decisions are
promoted (they were needed but not pre-loaded). Decisions from Session 1's
working set that were never referenced are demoted.

Session N: Working set converges on the decisions that actually matter
for this project. Panic saves never get promoted because no model
instance ever queries them — they prove their own irrelevance.

**The working set self-optimizes. The OS builds the OS.**

## 6. Temporal Awareness (Shipped v2.2.5)

Per-agent wall-clock delta on every heartbeat injection:

```
Δ1t:boot     — first call, just instantiated
Δ4t:30s      — active session, keep working
Δ4t:7h12m    — returned after sleep, re-orient
Δ4t:2d3h     — long gap, significant changes likely
```

Per-agent state in `.ostk/.heartbeat.{alias}`. Concurrent agents on
the same kernel track independent temporal positions.

The model uses temporal delta to calibrate behavior:
- `boot` or long gap → call `:investigate` for orientation
- Short gap → continue working, trust working set
- The kernel provides time. The model doesn't need to panic-save.

## 7. The Rendering Contract

```
                 ┌── fcp-screen ──→ Human (TUI: panels, colors, interactive)
Kernel state ────┤
                 └── fcp-llm    ──→ LLM (context page: registers, structured)
```

Same kernel. Same state. Different renderers. Both CPUs on the same bus.

### Provider-aware budget

```rust
let budget = match model {
    "opus"     => 800_000,   // 1M window, 80%
    "sonnet"   => 160_000,   // 200k window, 80%
    "gemini"   => 800_000,   // 1M window
    "deepseek" => 100_000,   // 128k window
    "qwen"     =>  25_000,   // 32k window
    _          =>  50_000,   // conservative default
};
```

Smaller-context models get tighter working sets (5 decisions not 10),
shorter session summaries, fewer recent turns. The kernel adapts the
rendering resolution to the model's capacity.

## 8. Unix Mapping (Complete)

| Unix | ostk | Status |
|------|------|--------|
| /proc/self/status | FIRST LOOK registers | Implemented (v2.2.4) |
| /proc/self/maps | Tool surface summary | Implemented (v2.2.4) |
| Resident pages | Working set (top 10 decisions) | **Build today** |
| Page table | Decision HWM + file HWM | File HWM done, decision HWM **build today** |
| Page fault | :investigate / :pitchfork / session_history | Partially implemented |
| madvise(DONTNEED) | context_release | Specced, not built |
| TLB | 304 elision for files + decisions | Files done, decisions **build today** |
| OOM killer | :dying notification | Implemented (v2.2.4) |
| Swap | Session summary compilation | Specced, not built |
| Clock | Per-agent temporal heartbeat | **Shipped v2.2.5** |
| Access bits (LRU) | Access-frequency tracking | **Build today** |

## 9. Implementation: What to Build Today

### Phase 1: Decision filtering in render_working_state()
- Panic filter (skip EMERGENCY_/DYING_/CRITICAL_/FULL_SESSION/FINAL_)
- Age decay (72h default)
- Cap at 10 entries
- ~20 lines in src/fcp/llm.rs

### Phase 2: Decision HWM
- New file: .ostk/.decision_hwm.{alias}
- Record seen_at_turn when decision injected in FIRST LOOK
- Record queried when :investigate returns a decision
- ~40 lines new, mirrors file HWM pattern

### Phase 3: Access-frequency ranking
- Score function: queried_total * 3 + recency + type_bonus
- Top 10 enter working set
- ~20 lines in render_working_state()

### Phase 4: :investigate tool
- Search decisions.jsonl by keyword
- Cross-reference audit.jsonl for corroboration
- Return timestamped, attributed evidence
- Wire as MCP tool in dispatch.rs
