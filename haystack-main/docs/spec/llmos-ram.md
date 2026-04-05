---
status: spec
version: 1
author: scottmeyer + agent
created: 2026-03-16
compounds: llmos-memory-model, llmos-concurrency, cpu-driver, fcp-screen
implements: []
---

# llmOS RAM: Multiplexed Threaded Conversations

> One context window. Many threads. The context IS the RAM. Threads are memory segments.

## 1. Thesis

Separate LLM sessions per thread waste money and lose shared context. Instead: ONE conversation, ALL threads as an append-only log. Thread delimiters partition the address space. The `context_management` API is the garbage collector.

## 2. Memory Layout

```
 0x0000 ┌──────────────────────────────────────────────┐
        │ System prompt (kernel, INIT, .language)       │ ~2k tok, IMMUTABLE, prompt-cached
 0x0800 ├──────────────────────────────────────────────┤
        │ ACTIVE: tui-work  [role=active]              │ Full detail, tool results, ~40-80k
 0x8000 ├──────────────────────────────────────────────┤
        │ BACKGROUND: hoberman  [role=background]      │ Last 5 msgs + compacted older, ~5-15k
 0xC000 ├──────────────────────────────────────────────┤
        │ DORMANT: bench  [role=dormant]               │ Single summary paragraph, ~500 tok
 0xE000 ├──────────────────────────────────────────────┤
        │ Current user input                           │ Recency-biased attention
 0xFFFF └──────────────────────────────────────────────┘
```

Active thread at TOP (after system prompt) exploits primacy bias. Background threads in the middle where attention is weakest — but summaries suffice. Research confirms the U-shaped attention curve: LLMs attend strongest to beginning and end of context, degrading in the middle (Liu et al., "Lost in the Middle"). Claude specifically decays slowest among frontier models, making this layout viable.

## 3. Thread Delimiter Protocol

Delimiters are system-injected. The LLM never generates them.

```
[thread:<name> role=<active|background|dormant>]
...conversation messages...
[/thread:<name>]
```

Roles control compaction: **active** = full history, keep=5 tool results. **background** = last N messages verbatim, older compacted. **dormant** = one summary paragraph.

Thread metadata as JSON annotation on segment open:
```json
{"thread":"tui-work","role":"active","messages":47,"tokens_est":62000}
```

## 4. Append-Only Log

Every message appended with thread tag. History never rewritten by kernel — only by the API's `context_management`.

```json
{"role":"user",      "thread":"tui",      "content":"fix the input handler"}
{"role":"assistant", "thread":"tui",      "content":"Looking at input.rs..."}
{"role":"user",      "thread":"hoberman", "content":"check radius calc"}
```

The kernel appends. The API compacts. Kernel never implements summarization — delegates to `compact_20260112`. This mirrors how Google's ADK and LangGraph handle multi-agent state: session is ground truth, working context is a computed projection.

## 5. Thread Switching

`:thread hoberman` inserts a control message and **reorders** the message array:

1. System prompt (unchanged, prompt-cached)
2. Hoberman messages (now active — expanded from disk if dormant)
3. tui-work messages (now background — compacted)
4. Other dormant threads (summaries only)
5. Current user input

The reorder is a **projection**. The session log on disk stays append-only. The API request is a computed view, rebuilt before each `inference()` call.

## 6. Context Management Mapping

| Operation | API mechanism | Trigger |
|---|---|---|
| Clear old tool results | `clear_tool_uses_20250919` keep=5 | Every API call |
| Compact background thread | `compact_20260112` | Thread demotion |
| Compact dormant thread | `compact_20260112` | Idle >10 turns |
| Emergency shed | `compact_20260112` at 80% | Context pressure |
| Expand dormant to active | Reload from session JSONL | `:thread` switch |

Pressure cascade:
```
<60%   normal:    active=full, background=partial, dormant=summary
60-80% aggressive: active keep=3, background=summary only
80-90% emergency:  compact active older half, shed dormant
>90%   page out:   write to disk, reload as summary
```

## 7. Cost Analysis

Claude prompt cache: reads = 10% of base input price. Writes = 125%. Break-even after 2 hits.

| Scenario | Cost per turn (200k ctx, Opus $15/M input) |
|---|---|
| Separate sessions (no shared cache) | **$3.00** |
| Multiplexed (system prompt cached only) | **$2.97** |
| Multiplexed (system + stable background cached) | **$1.65** |

The multiplexed model wins when background threads are stable — unchanged threads remain in the cached prefix. Separate sessions get zero cache benefit across threads. Compacted dormant threads (~500 tok each) are cheaper than separate contexts each carrying duplicate system prompts. Anthropic's workspace-level cache sharing (Feb 2025) means all threads within one session share the same cache.

## 8. Risks

| Risk | Sev | Mitigation |
|---|---|---|
| Thread bleed (LLM responds to wrong thread) | H | Active at top + delimiter + role tag. Empirical testing required. |
| Lost-in-middle ignores background | M | Acceptable — background is summaries. Active in primacy position. |
| Compaction removes delimiter | H | Delimiters as system messages, re-injected on every projection. |
| Expansion spike on thread switch | M | Expand-then-compact: load from disk, compact demoted thread. Net neutral. |
| Reorder breaks prompt cache | L | System prefix always stable. Mid-context invalidation expected on switch, re-caches in one call. |
| Token estimation drift | L | `count_tokens` API (already in agent_loop) adjusts thresholds dynamically. |

## 9. Integration Tests

**Three-thread isolation**: Create alpha/beta/gamma threads, each remembering a unique word (CHERRY/GRANITE/TELESCOPE). Switch between them, assert each recalls only its own word.

**Compaction preserves identity**: Create "verbose" thread (50 tool-heavy turns, ~80k tokens) and "quiet" thread (3 turns). Force compaction. Verify quiet retains verbatim turns, verbose has coherent summary.

**Cost validation**: Run 10 turns across 3 threads in single session vs 3 separate sessions. Assert single-session total input tokens < sum of separate sessions.

## 10. Implementation

```
agent_loop.rs: ThreadedMessageStore    ← append-only log + per-thread index
  → project_for_api()                  ← reordered message array (the projection)
    → build_params() (existing)        ← unchanged, receives projected messages
      → inference() (CpuDriver)        ← unchanged, sees normal messages
```

The driver never knows about threads. Three new types, one new function, zero driver changes.
