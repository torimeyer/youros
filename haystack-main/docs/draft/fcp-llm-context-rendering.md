# fcp-llm: Context Rendering for LLM Compute

**Date**: 2026-03-25
**Status**: DRAFT
**Author**: @scott + @haystack.prime
**Extends**: kernel-syscall-surface.md, fcp-shared-protocol.md, unix-to-haystack.md
**Evidence**: session-2026-03-25 (demand-paged tools, provider-aware context, agent feedback on cold start + conversation bearing)

## The Insight

Every LLM interface today models the input as a human conversation:

```
User: "do X"
Assistant: "I'll do X. Let me read the file..."
[tool_use: Read] → 500 lines of file content
Assistant: "I see the issue at line 47..."
User: "yes, fix it"
```

This assumes that because LLMs are trained on human language, human conversation
is the optimal input format. This is wrong.

An LLM is a compute unit. When it receives 47 turns of conversation, it:

1. Scans the entire context looking for **state** — what exists, what changed, what's decided
2. Skips narrative filler — "Let me read the file" is output scaffolding, not input information
3. Reconstructs the **work order** from dialogue — what am I supposed to do right now
4. Fights attention diffusion over redundant content — a file read in turn 5 that was edited in turn 12 is noise

The conversation format forces the LLM to do archaeology on its own output.
Every turn, it re-derives state from narrative. This is like asking a CPU to
parse its own core dump to find the program counter.

## The Design: fcp-llm as Rendering Layer

fcp-screen renders kernel state for human eyes (TUI).
fcp-llm renders kernel state for LLM compute (context page).

Same kernel. Same state. Different device drivers. Both CPUs on the same bus,
looking at the same registers, through codecs optimized for their processing model.

```
                 ┌── fcp-screen ──→ Human (TUI: panels, colors, interactive)
Kernel state ────┤
                 └── fcp-llm    ──→ LLM (context page: registers, structured, navigable)
```

### What the LLM receives today

```
system: "You are a development assistant. Read boot.md on startup..."  (~500 tokens of prose)
messages: [47 turns of conversation, tool calls, tool results]          (~50,000 tokens)
tools: [24 tool schemas]                                                (~1,800 tokens)
```

Total: ~52,000 tokens. Most of it is narrative history the LLM must mine for state.

### What the LLM should receive

```
system: [REGISTERS + TOOL_SURFACE + WORKING_STATE]                     (~400 tokens)
messages: [SESSION_SUMMARY + RECENT(5 turns)]                          (~3,000 tokens)
tools: [24 tool schemas + navigation tools]                            (~2,000 tokens)
```

Total: ~5,400 tokens. Same information. 10x compression. The LLM receives
structured state, not a conversation transcript.

## The Context Page

The kernel assembles a **context page** — a structured, navigable view of
session state — instead of raw conversation history.

### Block 1: Registers (~100 tokens)

```
REGISTERS {
  identity:  agent-2578
  project:   ostk v2.1.0
  root:      /Users/scottmeyer/projects/ostk
  laws:      invisible-write | ephemeral | filesystem | OCC
  test:      1767/0/0 (pass/fail/skip)
  build:     clean
  needles:   202 open | P0: →546
  session:   47 turns | 12 tool calls | 8 files modified
  boot:      stamp=4 | confidence=0.00 (restricted)
}
```

This is the kernel's register dump — the same data that `ostk boot`
produces, rendered as structured key-value pairs. The LLM reads its own
`/proc/self/status` at the start of every turn.

Source: `BootContext::render_registers()` — reads live needle counts,
fleet status, test/build state, boot confidence.

### Block 2: Tool Surface (~50 tokens)

Already implemented (demand-paged tool loading):

```
TOOL_SURFACE {
  resident(24): [list]
  deferred(40): ostk_verbs(query)
  devices: fcp-screen(alive) fcp-rust(dead) fcp-llm(alive)
}
```

Source: `build_tool_surface_summary()` in session.rs.

### Block 3: Working State (~150 tokens)

```
WORKING_STATE {
  active_needle: →1916 kernel syscall surface [in_progress]
  modified: [
    src/cpu/params.rs:g7      — context management
    src/cpu/agent_loop.rs:g12 — dispatch + count_tokens gating
    src/cpu/session.rs:g9     — tool surface summary
  ]
  decisions: [
    momentum_threshold=0.45
    boot_snapshot=immutable
    spawn_run_unified=true
  ]
}
```

This is new. The kernel tracks:
- **Active needle**: what the agent is working on (from session metadata)
- **Modified files**: files edited this session + generation counter (from gen_table)
- **Decisions**: key choices made this session (extracted from conversation or explicit)

Source: `BootContext::render_working_state()` — reads gen_table for modified
files, session metadata for active needle, decision log for choices.

### Block 4: Session Summary (~100 tokens)

```
SESSION_SUMMARY {
  Wrote demand-paged tool loading for kernel syscall surface.
  .language verbs auto-generate MCP tool schemas at boot.
  Unified spawn/run/TUI through config_from_agentfile pipeline.
  37 new tests. All 1767 pass.
}
```

This replaces the 47-turn conversation history. Generated by the kernel
from the session JSONL — either at compaction time or incrementally
as the session progresses.

Source: `SessionSummary::compile()` — reads session JSONL, extracts
key events (file edits, tool calls, needle state changes, test results),
compresses into a structured summary.

### Block 5: Recent Turns (~2,500 tokens)

The last 5 user+assistant turn pairs, full fidelity. This is the working
set — the turns the LLM needs to maintain conversational coherence.

Older turns are on disk in the session JSONL, pageable via
`ostk_session_history()`.

### Block 6: Navigation Tools

```
ostk_session_history(n?, from_turn?)  — page in older turns
ostk_context_search(query)            — search full session history
ostk_context_release(before_turn)     — madvise(DONTNEED): signal processed context
```

These give the LLM agency over its own memory. If it needs context from
turn 12, it pages it in. If it's done with old context, it signals
the kernel to release it.

## The Unix Mapping

| Unix | fcp-llm |
|------|---------|
| /proc/self/status | REGISTERS block |
| /proc/self/maps | TOOL_SURFACE block |
| working set (resident pages) | RECENT turns |
| swap (paged out) | Session JSONL on disk |
| page fault | ostk_session_history() |
| madvise(MADV_DONTNEED) | ostk_context_release() |
| /proc/self/mem | ostk_context_search() |
| core dump → gdb | SESSION_SUMMARY |
| fcp-screen | Human display driver (TUI) |
| fcp-llm | LLM display driver (context page) |

## Why This Is Different

### Current paradigm (conversation)

The LLM is treated as a chat partner. Input is modeled on human dialogue.
Context management is "how do we make the conversation fit in the window."
Provider-specific: Claude has compact, others have nothing.

### Kernel paradigm (context page)

The LLM is treated as a CPU. Input is a register dump + instruction.
Context management is "how does the kernel render state for compute."
Provider-agnostic: the kernel builds the right-sized page, every provider
receives the same structured input.

### What each CPU sees

**Human (via fcp-screen):**
- TUI panels with fleet status, needle list, active agent
- Interactive: can type commands, scroll, select
- Optimized for visual scanning and quick decisions
- Natural language for communication, structured layout for state

**LLM (via fcp-llm):**
- Register dump with structured state
- Recent turns for conversational continuity
- Navigation tools for on-demand context
- Optimized for attention efficiency and state reasoning
- Structured format for state, natural language for task description

**Both see the same kernel state. Both can issue the same commands.**
The protocol is symmetric (fcp-shared-protocol.md). The rendering is
device-specific.

## The Rendering Contract

fcp-llm implements the `DeviceDriver` trait:

```rust
pub struct FcpLlm {
    /// Maximum tokens for the context page (computed from model context window)
    budget: u64,
    /// Number of recent turns to keep at full fidelity
    window_size: usize,
    /// Session summary compiler
    summarizer: SessionSummary,
}

impl FcpLlm {
    /// Render kernel state into a context page for the LLM.
    ///
    /// This is the core function — the LLM display driver.
    /// Called by build_params() instead of raw messages.to_vec().
    pub fn render_context_page(
        &self,
        boot: &BootContext,
        session: &AgentSession,
        gen_table: &GenTable,
    ) -> ContextPage {
        ContextPage {
            registers: self.render_registers(boot),
            tool_surface: boot.deferred_summary.clone(),
            working_state: self.render_working_state(session, gen_table),
            summary: self.summarizer.current_summary(),
            recent: session.recent_turns(self.window_size),
        }
    }
}

/// The assembled context page, ready for serialization into messages.
pub struct ContextPage {
    pub registers: String,
    pub tool_surface: String,
    pub working_state: String,
    pub summary: String,
    pub recent: Vec<Message>,
}

impl ContextPage {
    /// Serialize into the messages array for the InferenceRequest.
    pub fn into_messages(self) -> Vec<Message> {
        let mut messages = Vec::new();

        // System-level blocks (registers + tools + working state)
        // These go into preload_context for prompt caching
        // — they change rarely, so cache hits are high

        // Session summary as first user message
        if !self.summary.is_empty() {
            messages.push(Message::user(format!(
                "SESSION_SUMMARY {{\n{}\n}}\n\nRecent turns follow.",
                self.summary
            )));
            messages.push(Message::assistant(
                "Context loaded. Registers, tools, working state, and summary received."
                .into()
            ));
        }

        // Recent turns — full fidelity
        messages.extend(self.recent);

        messages
    }
}
```

## Incremental Summary Compilation

The session summary must be maintained incrementally — not rebuilt from
scratch every turn. This is the `SessionSummary` struct:

```rust
pub struct SessionSummary {
    /// Running summary text, updated after key events
    text: String,
    /// Turn counter at which the summary was last updated
    last_compiled_turn: usize,
    /// Key events since last compilation
    pending_events: Vec<SessionEvent>,
}

enum SessionEvent {
    FileModified { path: String, gen: u64 },
    TestResult { passed: u32, failed: u32 },
    NeedleStateChange { id: String, old: String, new: String },
    DecisionMade { key: String, value: String },
    ToolCallSignificant { name: String, summary: String },
}
```

The summary compiles automatically when:
1. A tool call completes (extract key events)
2. The turn counter exceeds `last_compiled_turn + 5` (periodic)
3. Context pressure rises above 60% (pre-emptive)

Compilation is cheap — it appends delta events to the running summary,
not regenerating from scratch. This is the `append-only log → compiled view`
pattern already used by `.language` momentum decay.

## Provider Interaction

### Claude (Anthropic API)

The context page goes into `messages`. Claude's `compact` beta is a safety
net — if the kernel miscalculates the page size, compact catches the overflow.
But the primary context management is kernel-side via the context page.

`clear_tool_uses` with `exclude_tools` protects the recent turns from
API-side clearing. The kernel has already curated what's in the window;
the API should not second-guess it.

### OpenRouter / Gemini / Mistral

The context page goes into `messages` — same as Claude. No API-side
context management needed because the kernel has already built a page
that fits the model's context window.

The `budget` parameter in FcpLlm is computed from:
```rust
let budget = match model {
    m if m.contains("opus") => 800_000,     // 1M window, 80%
    m if m.contains("sonnet") => 160_000,   // 200k window, 80%
    m if m.contains("gemini") => 800_000,   // 1M window
    m if m.contains("deepseek") => 100_000, // 128k window
    m if m.contains("qwen") => 25_000,      // 32k window
    _ => 50_000,                             // conservative default
};
```

The kernel adjusts the window size (recent turns) and summary verbosity
to fit the budget. Small-context models get tighter summaries and fewer
recent turns. Large-context models get more. The agent's compute
capability shapes the rendering — same state, different resolution.

## Navigation Tools (Model-Mediated Memory Management)

### ostk_session_history(n?, from_turn?)

Page in N turns starting from a specific turn number. The model uses this
when the summary references something it needs to see in full.

```json
{
    "name": "ostk_session_history",
    "description": "Page in older conversation turns from session history. Use when you need details not in the summary or recent turns.",
    "input_schema": {
        "properties": {
            "n": {"type": "integer", "description": "Number of turns to retrieve (default 5)"},
            "from_turn": {"type": "integer", "description": "Starting turn number (default: oldest available)"}
        }
    }
}
```

### ostk_context_search(query)

Search across the full session history for a keyword or pattern. Returns
matching turns with surrounding context.

```json
{
    "name": "ostk_context_search",
    "description": "Search full session history for a keyword. Returns matching turns with context. Use when you need to find a specific past decision, error, or file change.",
    "input_schema": {
        "properties": {
            "query": {"type": "string", "description": "Search query — keyword, file name, needle ID, or error message"}
        },
        "required": ["query"]
    }
}
```

### ostk_context_release(before_turn)

Signal that context before a given turn has been processed and can be
released. This is `madvise(MADV_DONTNEED)` — the model tells the kernel
which pages are no longer needed.

```json
{
    "name": "ostk_context_release",
    "description": "Signal that you have processed context before the given turn and it can be released from your working window. The kernel will compress it into the session summary. Use to manage your own context budget.",
    "input_schema": {
        "properties": {
            "before_turn": {"type": "integer", "description": "Release all turns before this number"},
            "reason": {"type": "string", "description": "Why this context is no longer needed (for audit)"}
        },
        "required": ["before_turn"]
    }
}
```

The kernel responds by:
1. Compiling released turns into the session summary
2. Removing them from the recent window
3. Logging the release event to audit.jsonl
4. The turns remain on disk in session JSONL (recoverable via session_history)

## The Decision Log

Working state includes a `decisions` block. This is new infrastructure:
the kernel tracks key decisions made during the session so they survive
context page rebuilds.

A decision is logged when:
- The agent makes an architectural choice (detected from conversation)
- The agent sets a kernel parameter (threshold, config)
- The human confirms or corrects a direction

```rust
pub fn log_decision(session: &mut SessionMetadata, key: &str, value: &str) {
    session.decisions.push(Decision {
        key: key.into(),
        value: value.into(),
        turn: session.turn_count,
        timestamp: now_iso(),
    });
}
```

Decisions are injected into the working state block. They are the session's
"program counter" — they tell the LLM where the computation is and what
invariants hold.

## Implementation Phases

### Phase 1: ContextPage builder

New file: `src/fcp/llm.rs`

- `FcpLlm` struct with `render_context_page()`
- `ContextPage` struct with `into_messages()`
- `render_registers()` — reads BootContext, formats structured state
- `render_working_state()` — reads gen_table + session metadata
- Wire into `build_params()` — replace `messages.to_vec()` with context page

### Phase 2: SessionSummary compiler

New file: `src/cpu/summary.rs`

- `SessionSummary` struct with incremental compilation
- Event extraction from tool results (file edits, test results, needles)
- Summary maintained per-session in `AgentSession`
- Triggered on tool completion, periodic, and pressure-based

### Phase 3: Navigation tools

In `src/cpu/tool_exec.rs`:

- `handle_ostk_context_search()` — grep session JSONL
- `handle_ostk_context_release()` — madvise, compile released turns into summary

(ostk_session_history already implemented)

### Phase 4: Decision log

In `src/cpu/session.rs`:

- `Decision` struct + `log_decision()`
- Extraction from conversation (pattern matching on "decision:" or explicit tool)
- Injection into working state block

### Phase 5: Provider-aware budget

In `src/fcp/llm.rs`:

- Model-specific context window lookup
- Adaptive window_size (more turns for larger windows)
- Adaptive summary verbosity (tighter for smaller windows)

## What This Eliminates

| Current | Replaced by |
|---------|------------|
| 50k tokens of raw conversation | ~5k tokens of context page |
| LLM archaeologizes its own output | Kernel provides structured state |
| Provider-specific compact/clear | Provider-agnostic context page |
| Conversation grows until OOM | Kernel manages page size to budget |
| No session continuity across boots | Summary persists, decisions persist |
| Human and LLM see different state | Same kernel, different renderers |
| LLM has no memory agency | Navigation tools for self-directed paging |

## The Rule (extended)

fcp-screen renders kernel state for human eyes.
fcp-llm renders kernel state for LLM compute.
Both CPUs issue the same commands through the same kernel.
The protocol is symmetric. The rendering is device-specific.
The conversation paradigm is a rendering choice, not a law.
