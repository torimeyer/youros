---
title: kernel-syscall-surface
author: '@scott'
status: spec
created: 2026-03-24
amended: 2026-03-24
evidence: session-2026-03-24 (TUI FSM, .language register, bidirectional channels, fcp-shared-protocol), roundtable-2026-03-24 (kernel-designer, context-optimizer, unix-purist — 3 rounds)
extends: [unix-to-haystack.md, fcp-shared-protocol.md, language-as-kernel-register.md, inheritable-humanfile.md]
implements: []
---

# Kernel Syscall Surface

> Every Unix primitive has exactly one ostk equivalent. No ostk primitive
> exists without a Unix ancestor. — unix-to-haystack.md, Rule

## Problem

The agent sees a rich OS in its system prompt:

```
.language: 73 verbs | devices: fcp-screen, rust | services: 7/7
```

But its actual tools are: `Bash`, `Read`, `Edit`. Everything routes through
one opaque shell hole. The agent guesses CLI syntax, loses type safety, and
the audit trail shows `Bash("ostk needle add 'fix the bug'")` instead of
`ostk_needle(title="fix the bug")`.

This is like a Unix kernel that has `open()`, `read()`, `write()`, `ioctl()`
implemented — but the only syscall exposed to userspace is `system("command")`.

## Design

### The .language register IS the syscall table

`.language` already defines every capability:

```
:needle | 2 | user    | ... | ostk work add  | (title) → (id)     | file a needle
:hay    | 1 | user    | ... | ostk work hay  | (straw) → ()       | capture intent
:show   | 1 | user    | ... | ostk show      | (query) → (state)  | universal query
:spawn  | 1 | user    | ... | ostk kernel spawn | (cmd) → (pid)   | spawn agent
:audit  | 1 | user    | ... | ostk os audit  | () → (ok/fail)     | verify integrity
```

Each entry has: verb, tier, layer, resolution (the actual command), and
signature (input → output types). This is a syscall table.

### Auto-generate MCP tools from .language

Boot reads `.language` and generates MCP tool schemas for every tier 1-2
user-layer verb:

```rust
fn generate_tool_schema(entry: &LanguageEntry) -> serde_json::Value {
    json!({
        "name": format!("ostk_{}", entry.verb),
        "description": entry.doc,
        "input_schema": parse_signature_to_schema(&entry.signature),
    })
}
```

The agent's tool list becomes:

```
ostk_needle(title, body?)       — file a needle
ostk_hay(straw)                 — capture intent
ostk_show(query)                — universal query
ostk_spawn(agentfile)           — spawn agent
ostk_compile()                  — triage hay → needles
ostk_commit(msg?)               — commit with attribution
ostk_bench(scenario?)           — run benchmarks
ostk_trace(id)                  — trace attribution
ostk_close(id, reason?)         — close a needle
ostk_history(query?)            — show history
ostk_search(query, semantic?)   — search the project
ostk_ask(question)              — LLM query through kernel
```

Each tool call routes through the kernel: audited, compressed, coordinated.
Not `Bash("ostk needle add ...")` — a structured call with typed parameters.

### Device drivers as tools

Tier-0 device entries with momentum > 0 become device tools:

```
fcp_rust(query)          — rust intelligence (rust-analyzer)
fcp_web(url)             — web reading (readability extraction)
```

The agent calls `fcp_web(url="https://docs.rs/...")` and gets structured
markdown. No `Bash("curl ... | ...")`.

### Kernel services as invisible infrastructure

Tier-0 service entries are NOT exposed as tools — they're invisible
infrastructure (Law 1: write path invisible). The agent benefits from
gen-table, elision, Hot PR, approval without knowing they exist.

The one exception: `context_management`. This is the agent's memory syscall.

### Context management as memory syscall (kernel-mediated)

The kernel mediates context editing via the Anthropic API. This is `mmap` /
`munmap` — the agent's memory lifecycle is kernel-managed, not agent-managed.

**Provider awareness**: context editing primitives (`compact`, `clear_tool_uses`,
`count_tokens`) are Anthropic API features. Other providers (OpenRouter,
Gemini, Mistral) do not expose these APIs. The kernel uses a two-layer strategy:

- **Provider-agnostic (kernel-side)**: demand-paged tools, tool surface
  summary, boot state blocks, `ostk_verbs`/`ostk_man` — these control what
  goes INTO the request. Work for all providers.
- **Provider-specific (API-side)**: compact, clear_tool_uses, count_tokens —
  Claude only. For non-Claude providers, the kernel must implement its own
  truncation (future work: kernel-side message summarization).

**Wired in `params.rs` (Claude smart defaults):**

```
context_management: {
    "edits": [
        {
            "type": "compact_20260112",
            "trigger": { "type": "input_tokens", "value": <80% of budget> },
            "instructions": "<kernel state preservation instructions>",
            "pause_after_compaction": true
        },
        {
            "type": "clear_tool_uses_20250919",
            "keep": { "type": "tool_uses", "value": 5 },
            "exclude_tools": ["ostk_verbs", "ostk_man"],
            "clear_tool_inputs": true
        }
    ]
}
```

**Design decisions:**

1. **`exclude_tools`**: never clear `ostk_verbs` results (they contain loaded
   tool schemas the agent needs) or `ostk_man` results (CLI reference being
   used). Clearing these is equivalent to a page table corruption.

2. **`clear_tool_inputs: true`**: ostk_* tool inputs are CLI argument names,
   not valuable context. Clearing them saves tokens without information loss.

3. **Custom compact `instructions`**: the compaction summary preserves
   kernel-critical state — open needle IDs, resident tool names, error
   resolutions, session identity. Drops stale tool results and intermediate
   reasoning. This is domain-aware compaction vs generic summarization.

4. **`pause_after_compaction: true`**: the kernel checkpoints at compaction —
   logs to audit, can refresh boot state. This is the `munmap` + `mmap`
   cycle.

5. **Adaptive trigger**: 80% of `context_budget` (from Agentfile `LIMIT`),
   defaults to 160k for Claude 4.6. Not hardcoded.

6. **`count_tokens` gating**: only called when estimated usage > 60% of
   budget, not every turn. Saves one API round trip per turn when context
   is not under pressure. Tracked via accumulated `input_tokens` from
   API usage responses.

### CLI reference (ostk_man)

Agents cannot discover optional flags, subcommand arguments, or
stdin/stdout contracts from `.language` alone. The resolution string
(`ostk work add`) and signature (`(title) -> (id)`) tell the agent
WHAT to call and with what primary args, but not the full flag surface.

**Solution**: `ostk_man(command)` — the `man(1)` equivalent. Always
resident alongside `ostk_verbs`. Executes `ostk {command} --help`
at runtime and returns the compressed output.

The system prompt includes a 40-token CLI tree index:
```
# CLI (use ostk_man for details)
work: add, close, compile, hay, index, link, list, next, pull, refine
os: audit, clock, diff, history, metrics, status
doc: decompose, draft, promote
kernel: await, init, install, ps, reap, serve, shutdown, spawn
```

This is the deferred summary for CLI — agents know what commands exist,
`ostk_man` gives the full reference. Consistent with demand-paging model:
the index is `/proc/kallsyms`, the manpage is `dlopen`.

### Agent conversation context (fcp-llm + parent context)

Agents launched via `ostk run` start with no parent conversation —
their first message is "Begin." They have no bearing on the conversation
that spawned them.

**Parent context injection**: when a child agent is spawned, the kernel
extracts the parent's last 5 user+assistant turn pairs, truncates each
to 200 chars, and injects them as a preload_context block. The child's
initial message becomes "Continue from parent context above." instead
of "Begin."

This is `fork()` semantics — the child inherits the parent's memory
(last 5 turns) but gets its own address space (fresh tool surface,
own session).

**`ostk_ask(question, model?)`**: the `fcp-llm` device tool. Agents
can query LLMs as a typed tool instead of `Bash("ostk ask '...'")`.
Routes through the existing CpuDriver trait — provider-agnostic. Uses
the parent session's model by default, with optional override.

**`ostk_session_history(n?)`**: reads the agent's previous session
(`.prev.jsonl`) and returns the last N entries. This is `/proc/self/mem` —
the agent reading its own past.

| Syscall | Tool | Description |
|---------|------|-------------|
| fork() | parent context injection | Child inherits last 5 turns |
| exec() | Agentfile dispatch | Child gets own tool surface |
| /proc/self/mem | ostk_session_history(n) | Read own past session |
| ioctl(llm) | ostk_ask(question, model?) | Query LLM through kernel |
| man(1) | ostk_man(command) | CLI reference on demand |

### Session continuity (the conversation memory problem)

The agent reported: "I booted fresh. No memory of what I said to you."

Sessions are preserved as `.ostk/sessions/{name}.jsonl`. The FSM changes
this session made sessions start fresh by default (→383). But the agent
should have access to session history as a kernel service.

Two primitives needed:

**1. `ostk_session_history(n?)` tool** — returns the last N messages
from the current session's `.prev` file (the previous session). Default N=5.
This is the agent's `read()` on its own `/proc/self/mem`.

**2. Boot context summary** — at boot, the kernel reads the `.prev` session
file and generates a one-paragraph summary injected into the system prompt:

```
# Previous session (2026-03-24T06:53:11Z, 47 turns)
Summary: TUI state machine refactor — FSM, bidirectional channels, approval
overlay fix. Shipped 30 commits. Backlog 386→200. Next: kernel register.
```

This is the Unix `core dump` → `gdb` pattern: the previous session's state
is readable by the next session's boot.

### Arrival protocol (the contact problem)

The agent reported: "You said this is @contact, an arrival. I have no tool
or ceremony for that."

An arrival is a kernel event: a new identity connects to the OS. The boot
sequence should include:

```
6. Load HUMANFILE → identity, model, dialect
7. Check session history → is this a continuation or first contact?
8. If first contact: emit kernel event "arrival", inject into system prompt
9. If continuation: inject session summary from .prev
```

The `.language` ceremony verbs already have `:confirm` and `:negotiate`.
The arrival is: the kernel detects a new session with no `.prev` file for
this identity, and injects an arrival context block into the system prompt.

## Syscall table (complete)

### Process management

| Syscall | Tool | .language verb | Description |
|---------|------|---------------|-------------|
| fork/exec | ostk_spawn(af) | :spawn | Spawn agent from Agentfile |
| kill | ostk_cancel(alias) | :halt | Cancel running agent |
| wait | ostk_await(alias) | — | Wait for agent completion |
| getpid | — (in system prompt) | — | Identity in boot context |
| ps | ostk_show("fleet") | :status | Fleet status |

### File I/O

| Syscall | Tool | .language verb | Description |
|---------|------|---------------|-------------|
| open+read | Read (native) | — | File reading (with 304 elision) |
| open+write | Edit (native) | — | CAS file editing (with Hot PR) |
| stat | ostk_show(path) | :show | File generation + metadata |
| readdir | Glob (native) | — | Directory listing |

### Device I/O

| Syscall | Tool | .language device | Description |
|---------|------|-----------------|-------------|
| ioctl(rust) | fcp_rust(query) | :fcp-rust | Rust intelligence |
| ioctl(web) | fcp_web(url) | :fcp-web | Web reading |
| ioctl(screen) | — (implicit) | :fcp-screen | Display driver |

### Work management (unique to ostk)

| Syscall | Tool | .language verb | Description |
|---------|------|---------------|-------------|
| — | ostk_needle(title) | :needle | File executable work |
| — | ostk_hay(straw) | :hay | Capture raw intent |
| — | ostk_compile() | :compile | Triage hay → needles |
| — | ostk_close(id) | :close | Close completed work |
| — | ostk_show(query) | :show | Universal query |
| — | ostk_commit(msg) | :commit | Attributed commit |

### Memory management (transparent)

| Syscall | Mechanism | Description |
|---------|-----------|-------------|
| mmap | prompt cache | System blocks cached 1h TTL |
| munmap | compact | Auto-compact at 80% budget |
| brk | clear_tool_uses | Keep last 5 tools, clear older |
| /proc/self/mem | session_history(n) | Read own conversation history |

### Tool loading (demand-paged)

| Syscall | Tool / Mechanism | Description |
|---------|-----------------|-------------|
| dlopen(3) | ostk_verbs(query) | Load typed tool schema on demand |
| dlsym(3) | schema inline in response | Get function pointer (tool schema) |
| mmap(MAP_FIXED) | verbs.lock | Pin bootstrap tools at cold boot |
| EFAULT | ETOOLNOTLOADED | Deferred tool called without loading |
| ESTALE (NFS) | boot_stamp mismatch | Schema changed since boot |
| /proc/kallsyms | category summary | Deferred verb awareness (50 tokens) |
| getrlimit | TOOL_RESIDENT_THRESHOLD | Tunable momentum cutoff (default 0.45) |
| man(1) | ostk_man(command) | CLI reference on demand (always resident) |
| fork() | parent context injection | Child inherits last 5 turns |
| ioctl(llm) | ostk_ask(question, model?) | Query LLM through fcp-llm |
| /proc/self/mem | ostk_session_history(n) | Read own past session |

### Audit / security

| Syscall | Tool | .language verb | Description |
|---------|------|---------------|-------------|
| syslog | ostk_audit() | :audit | Verify audit integrity |
| who | ostk_trace(id) | :trace | Attribution chain |
| history | ostk_history(q) | :history | Event history |

## Demand-Paged Tool Loading

> Every Unix kernel has more syscalls than any process uses. Linux has ~450.
> libc wraps ~30. The rest are available via `syscall(2)`. The process does
> not load 450 syscall stubs into its address space. — unix-to-haystack.md

### The problem with flat loading

The original Phase 1 generates MCP tool schemas for **every** tier 1-2
user-layer verb. With 83 entries in `.language`, that is ~60 user verbs after
filtering. At ~75 tokens per tool schema, that is **~4,500 tokens** burned on
every system prompt. More critically:

1. **Attention degradation.** LLM tool selection accuracy degrades above ~30
   tools (ToolBench, Gorilla benchmarks). The `.language` register has severe
   semantic overlap: `:add`/`:needle` resolve to the same command, `:find`/
   `:grep`/`:search`/`:pitchfork` all search. The agent WILL confuse them.

2. **Dead tool cost.** 19 user verbs have momentum 0.00. Loading `:align`,
   `:emerge`, `:ultrathink`, `:shelve`, `:unshelve` costs tokens for tools
   the agent never selects.

3. **Prompt caching saves dollars, not attention.** Cached tokens still
   occupy attention slots. The selection confusion persists regardless of
   whether the tokens are served from cache.

### The Unix model: demand-paged virtual memory

`.language` is the virtual address space — the complete capability map.
The system prompt tool list is physical memory — what is actually resident.
Momentum is the reference bit. The kernel pages tools in on demand.

| Unix | ostk |
|------|----------|
| Virtual address space | `.language` register (83 verbs) |
| Physical frames (resident) | MCP tool schemas in system prompt |
| Page table entry | LanguageEntry (verb, tier, layer, momentum) |
| Reference bit | `momentum > threshold` |
| Page fault | `ostk_verbs(query)` call |
| Page-in | Schema materialized, returned inline |
| Clock replacement / LRU | Momentum decay at shutdown (half_life) |
| `dlopen(3)` | Demand-load typed schema on query |
| `libc` | Resident tool set (pinned + high-momentum) |
| `/proc/kallsyms` | Category summary in system prompt |
| `EFAULT` / `ESTALE` | `ETOOLNOTLOADED` + boot_stamp rejection |

### Alias deduplication (prerequisite zero)

Before any threshold analysis, collapse the alias graph. Multiple verbs
that resolve to the same command are **one tool with aliases**, not separate
momentum slots:

```
# These are ONE tool:
:add     → ostk work add    (momentum 0.25)
:needle  → ostk work add    (momentum 0.60)
# Winner: :needle (highest momentum). :add is an alias in the index.

# These are ONE tool:
:find    → ostk search      (momentum 0.20)
:grep    → ostk search      (momentum 0.10)
:search  → ostk search      (momentum 0.10)
# Winner: :find (highest momentum). :grep, :search are aliases.
```

Resolution rule (PATH-order): for each unique `resolution` string, the
verb with the highest momentum becomes the canonical tool name. All other
verbs sharing that resolution are aliases, discoverable via `ostk_verbs()`
but not loaded as separate schemas.

Post-dedup cardinality determines the real working set size.

### The resident set

At boot, the kernel reads `.language` and materializes tool schemas for:

**1. Pinned tools (always resident, unconditional):**

Native I/O tools are not generated from `.language` — they are hardcoded
in `tool_schemas()` (`cpu/mod.rs:570`):

- `Bash`, `Read`, `Edit`, `Write`, `Glob`, `Grep` — file I/O
- `SpawnAgent`, `NudgeAgent` — process management
- `FileNeedle`, `CloseNeedle`, `CompileHay` — work management

These are the kernel's built-in syscalls. They never page out.

**2. High-momentum tools (paged in at boot):**

User-layer verbs with momentum >= `TOOL_RESIDENT_THRESHOLD` (default: 0.45,
kernel parameter, tunable via `HUMANFILE` or env). Post-dedup. From the
current `.language`:

```
:run      0.95   ostk run
:bench    0.85   ostk bench
:audit    0.70   ostk os audit check
:secret   0.65   ostk secret
:needle   0.60   ostk work add        (canonical for :add)
:promote  0.55   ostk doc promote
:ps       0.50   ostk kernel ps
:show     0.45   ostk show
:compile  0.45   ostk work compile    (canonical for :calibrate)
:hay      1.00   ostk work hay        (canonical for :emerge, :note)
:log      0.45   ostk os history
:mode     0.45   [inferred]
:model    0.45   [inferred]
```

~13 additional tools at ~75 tokens each = ~975 tokens. With 11 pinned
native tools, total resident set is ~24 tools / ~1,800 tokens. Well within
the <30 tool accuracy threshold.

**3. Alive device tools (conditional on momentum):**

Device entries with `momentum > 0.0` become `ioctl`-equivalent tools.
Currently only `fcp-screen` (internal, no agent tool) and `fcp-rust`
(momentum 0.00 = dead, no tool). When `fcp-rust` comes alive:

```rust
// Device tool routes through driver socket (mcp_proxy.rs:417-527)
fcp_rust(query) → JSON-RPC tools/call → .ostk/drivers/rust.sock
```

Dead devices (momentum 0.00) cost zero tool slots.

**4. `ostk_verbs(query?)` — the page fault handler (always resident):**

One meta-tool, always loaded. This is the agent's `dlopen(3)`:

```json
{
    "name": "ostk_verbs",
    "description": "Discover available kernel verbs. Returns tool schemas for matching verbs that are not in your current tool set. Use this when you need a capability not in your loaded tools.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — verb name, keyword, or category (e.g. 'search', 'draft', 'admin')"
            }
        },
        "required": ["query"]
    }
}
```

Returns: matching verb entries with full schemas inlined, ranked by momentum.
The agent can call the returned verb on its next turn without a second lookup.

Side effects: calling `ostk_verbs()` increments momentum for matched
verbs (the lookup IS a reference signal). Capped at +0.1 per boot cycle
per verb to prevent gaming.

### Category summary (/proc/kallsyms)

The system prompt includes a context block that orients the agent to the
demand-paging model: what tools are resident, what is deferred, and how
to page in what it needs. This replaces the raw `.language` dump that
currently consumes ~2000 tokens per turn (`session.rs:451-453`).

```
# Tool surface (demand-paged)
#
# Your tools are demand-paged from .language (83 verbs, 2 devices).
# Resident tools (~24) are loaded — high-momentum verbs frozen at boot.
# Deferred tools are available via ostk_verbs(query).
#
# Resident: ostk_needle, ostk_hay, ostk_show, ostk_run, ostk_compile,
#   ostk_bench, ostk_audit, ostk_secret, ostk_promote, ostk_ps, ostk_log,
#   ostk_close + native I/O (Bash, Read, Edit, Write, Glob, Grep) +
#   process (SpawnAgent, NudgeAgent) + work (FileNeedle, CloseNeedle,
#   CompileHay) + web (WebRead, WebLinks, WebStatus) + ostk_verbs
#
# Deferred (use ostk_verbs to load):
#   search: find, grep, search, pitchfork (semantic)
#   drafting: draft, plan, decompose, amend
#   workflow: shelve, unshelve, pull, next, thread
#   admin: purge, import, merge, bail, clock, metrics
#   advanced: tack, ultrathink, diff, refine
#
# If you call a deferred verb directly you get ETOOLNOTLOADED.
# If a schema is stale (another agent updated .language) you get ESTALE.
# Both errors tell you exactly what to do next.
```

This is the agent's awareness of its virtual address space. It replaces
the full `.language` text injection — same information, ~150 tokens
instead of ~2000. The agent knows what exists, what is loaded, and how
to page in what it needs.

### Boot snapshot immutability

The resident set is **frozen at boot**. Momentum changes during a session
(from `record_verb()` calls) take effect on the **next** boot, not live.

Rationale: live tool list mutation during inference creates feedback loops
(agent calls a verb → verb promoted → tool appears → agent calls it more)
and confuses models that cache the tool list internally. The Unix analog:
process page tables are not reorganized mid-computation.

### Schema generation counter (boot_stamp)

Every tool schema carries a `boot_stamp` — a monotonic counter set at boot
time. When the kernel executes a tool call, it validates the stamp:

- **Match:** proceed normally.
- **Stale (`ESTALE`):** the `.language` register was updated by another
  agent since boot. Return a structured error with the current schema.
  The agent re-fetches via `ostk_verbs()`.

This prevents DLL Hell — schema skew between what the agent loaded at
boot and what the kernel will actually accept.

### ETOOLNOTLOADED

When the agent calls a verb that is deferred (not in the resident set),
the kernel returns:

```json
{
    "error": "ETOOLNOTLOADED",
    "verb": "pitchfork",
    "hint": "Use ostk_verbs('pitchfork') to load this tool"
}
```

No silent Bash fallback. No opaque error. The agent gets a structured
signal that forces explicit discovery. If the audit trail shows
`Bash("ostk <verb> ...")` where `<verb>` matches a `.language` entry,
the kernel injects a nudge: "Use `ostk_verbs('<verb>')` for the typed
tool."

### Momentum lifecycle (the decay function)

Momentum is the reference bit. It already has a complete gain/decay cycle
implemented in the codebase (`language.rs`, `shutdown.rs`):

**Gain** — at tack resolution time (`record_verb()`, `language.rs:167`):
- +0.1 per use, capped at 1.0
- Sets `last_gen` to today's epoch day (unix seconds / 86400)
- Flock-protected for concurrent agent writes

**Decay** — at shutdown (`compile_language()`, `shutdown.rs:481`):
- -0.05 for any user-layer verb whose `last_gen` < today (not used this session)
- Floored at 0.0
- Kernel, ceremony, device, service layers never decay

**Effective half-life**: a verb used once (momentum 0.30) and never again
decays to 0.00 in 6 shutdowns. A verb at momentum 1.00 decays to below
the 0.45 threshold in 11 shutdowns without use. This is the clock hand.

**Session normalization**: not needed. The epoch-day granularity means
a verb used any time during a session survives that session's shutdown
decay. Long sessions and short sessions produce the same decay behavior —
one session = one day of use, regardless of duration.

**Threshold as kernel parameter**: `TOOL_RESIDENT_THRESHOLD` defaults to
0.45. Overridable via:
- Environment: `OSTK_TOOL_THRESHOLD=0.50`
- HUMANFILE: `LIMIT tool_threshold 0.50`

The threshold is logged to `audit.jsonl` at boot for observability.

### Alias collapse (boot-time pass)

Alias deduplication runs at boot as part of `tools_from_language()`. It is
NOT a build-time lint or developer CLI verb — it is a kernel-internal
optimization that produces the canonical tool set.

**Algorithm** (`deduplicate_by_resolution()` in `cpu/mod.rs`):

```rust
fn deduplicate_by_resolution(entries: &[LanguageEntry]) -> Vec<LanguageEntry> {
    let mut by_resolution: HashMap<String, Vec<&LanguageEntry>> = HashMap::new();

    for e in entries.iter().filter(|e| e.layer == "user" && e.tier <= 2) {
        by_resolution.entry(e.resolution.clone())
            .or_default()
            .push(e);
    }

    by_resolution.values().map(|group| {
        // Winner: highest momentum. Ties broken alphabetically (stable).
        let canonical = group.iter()
            .max_by(|a, b| a.momentum.partial_cmp(&b.momentum)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.verb.cmp(&b.verb)))
            .unwrap();
        (*canonical).clone()
    }).collect()
}
```

**Alias index**: non-canonical verbs are stored in a lookup table returned
alongside the resident set. When the agent calls `ostk_verbs("add")`, the
kernel resolves `:add` → canonical `:needle` and returns `:needle`'s schema.
The agent never sees duplicate tools.

**Audit**: the boot log records the collapse:
```
alias: :add → :needle (resolution: ostk work add)
alias: :grep → :find (resolution: ostk search)
alias: :emerge → :hay (resolution: ostk work hay)
```

### verbs.lock — deterministic cold boot

New projects have all verbs at momentum 0.00-0.30. The resident set would
be trivially small without a bootstrap guarantee.

**Format**: `.ostk/verbs.lock` — one canonical verb per line, comments with `#`:

```
# verbs.lock — minimum resident tools for cold boot
# Generated by: ostk shutdown (first shutdown writes if absent)
# Updated by: ostk lock --refresh (re-derives from current momentum)
# Committed to repo: yes (deterministic across clones)
hay
needle
show
spawn
run
compile
bench
close
log
ps
```

**Lifecycle**:

1. **First shutdown** writes `verbs.lock` if absent — takes every verb with
   momentum >= threshold and writes the canonical names.
2. **Subsequent shutdowns** do NOT overwrite `verbs.lock` automatically.
   The lock is stable. Manual refresh via `ostk lock --refresh`.
3. **`ostk lock --refresh`** re-reads `.language`, runs alias collapse,
   picks verbs above threshold, rewrites `verbs.lock`. Developer reviews
   the diff before committing.
4. **`ostk init`** on a new project writes a default `verbs.lock` with
   the bootstrap set (hay, needle, show, spawn, run, compile, bench).

**Boot resolution order** (first match wins, like PATH):

1. Pinned native tools (Bash, Read, Edit, Write, Glob, Grep, SpawnAgent,
   NudgeAgent, FileNeedle, CloseNeedle, CompileHay, WebRead, WebLinks,
   WebStatus) — hardcoded in `tool_schemas()`, always resident.
2. `verbs.lock` entries — loaded regardless of momentum. The cold-boot floor.
3. Above-threshold verbs — `momentum >= TOOL_RESIDENT_THRESHOLD` after
   alias collapse. The warm-boot optimization.
4. `ostk_verbs()` meta-tool — always resident. The page fault handler.
5. Alive device tools — `momentum > 0.0` device entries. The ioctl surface.

Duplicates across layers are eliminated: if `:needle` appears in both
`verbs.lock` and above-threshold, it is loaded once.

## Implementation

### Phase 1a: Resident set from .language at boot

In `src/cpu/mod.rs` (replacing the flat `tools_from_language` approach):

```rust
/// Default momentum threshold for tool residency.
const TOOL_RESIDENT_THRESHOLD: f64 = 0.45;

/// Generate the resident tool set from .language at boot.
///
/// Returns: (resident_schemas, deferred_summary)
pub fn tools_from_language(root: &Path) -> (Vec<serde_json::Value>, String) {
    let entries = language::parse_language_file(root).unwrap_or_default();
    let boot_stamp = boot_stamp(root);

    // Step 1: Alias deduplication — collapse by resolution
    let canonical = deduplicate_by_resolution(&entries);

    // Step 2: Filter to resident set
    let threshold = env_threshold().unwrap_or(TOOL_RESIDENT_THRESHOLD);
    let resident: Vec<_> = canonical.iter()
        .filter(|e| e.momentum >= threshold || is_verbs_lock_entry(root, &e.verb))
        .map(|e| generate_tool_schema(e, boot_stamp))
        .collect();

    // Step 3: Build category summary for deferred verbs
    let deferred: Vec<_> = canonical.iter()
        .filter(|e| e.momentum < threshold && !is_verbs_lock_entry(root, &e.verb))
        .collect();
    let summary = build_category_summary(&deferred);

    // Step 4: Always include the meta-tool
    let mut tools = resident;
    tools.push(ostk_verbs_schema(boot_stamp));

    (tools, summary)
}
```

The `tool_schemas()` function in `cpu/mod.rs:570` keeps its hardcoded native
tools. `tools_from_language()` appends the dynamic resident set. Both feed
into `LoopConfig.tools`.

Supporting functions:

```rust
/// Monotonic boot counter — read from .ostk/boot_stamp, incremented each boot.
fn boot_stamp(root: &Path) -> u64 {
    let path = crate::state_dir(root).join("boot_stamp");
    let current = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0);
    let next = current + 1;
    let _ = std::fs::write(&path, next.to_string());
    next
}

/// Generate a single MCP tool schema from a LanguageEntry.
fn generate_tool_schema(entry: &LanguageEntry, stamp: u64) -> serde_json::Value {
    // Parse signature "(title, body?) -> (id)" into JSON Schema properties
    let (properties, required) = parse_signature_to_schema(&entry.signature);
    json!({
        "name": format!("ostk_{}", entry.verb),
        "description": entry.doc,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": false
        },
        "_boot_stamp": stamp  // Kernel-internal, stripped before API call
    })
}

/// Read verbs.lock entries.
fn read_verbs_lock(root: &Path) -> HashSet<String> {
    let path = crate::state_dir(root).join("verbs.lock");
    std::fs::read_to_string(&path)
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.starts_with('#') && !l.trim().is_empty())
        .map(|l| l.trim().to_string())
        .collect()
}

fn is_verbs_lock_entry(root: &Path, verb: &str) -> bool {
    // Cached per-boot — read once, store in lazy_static or OnceCell
    read_verbs_lock(root).contains(verb)
}

/// Read threshold from env or HUMANFILE LIMIT.
fn env_threshold() -> Option<f64> {
    std::env::var("OSTK_TOOL_THRESHOLD").ok()
        .and_then(|s| s.parse().ok())
}

/// Build the 50-token category summary for deferred verbs.
fn build_category_summary(deferred: &[&LanguageEntry]) -> String {
    // Group by semantic category (inferred from resolution prefix)
    // e.g. "ostk search" → search, "ostk doc" → draft
    let mut categories: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for e in deferred {
        let cat = infer_category(&e.resolution);
        categories.entry(cat).or_default().push(e.verb.clone());
    }
    let mut summary = String::from("# Available via ostk_verbs(query)\n");
    for (cat, verbs) in &categories {
        summary.push_str(&format!("{}: {}\n", cat, verbs.join(", ")));
    }
    summary
}
```

**Integration into `into_loop_config()`** (`cpu/mod.rs:444`):

```rust
// Existing: tools: tool_schemas(&self.tools),
// New: append language-derived tools
let mut all_tools = tool_schemas(&self.tools);
if let Some(root) = root.as_ref() {
    let (language_tools, _summary) = tools_from_language(root);
    all_tools.extend(language_tools);
}
// ... rest of LoopConfig
```

**Integration into `build_preload_context()`** (`session.rs:436`):

```rust
// Replace raw .language dump with category summary
if !self.deferred_summary.is_empty() {
    blocks.push(self.deferred_summary.clone());
}
// The full .language text is no longer injected — too expensive.
// Boot state block still includes register dump (needle counts, fleet, etc).
```

### Phase 1b: Deferred loading via ostk_verbs

Tool execution in `agent_loop.rs:882`:

```rust
match name {
    // ... existing native tools ...
    "ostk_verbs" => tool_exec::handle_ostk_verbs(input, root).await,
    _ => {
        // Check if this is a .language verb (resident or deferred)
        if let Some(entry) = lookup_language_verb(name, root) {
            // Validate boot_stamp
            if let Some(stamp) = input.get("_boot_stamp").and_then(|v| v.as_u64()) {
                if stamp != current_boot_stamp() {
                    return (json!({"error": "ESTALE", "hint": "Schema outdated. Re-fetch via ostk_verbs."}).to_string(), false);
                }
            }
            execute_language_tool(&entry, input, root).await
        } else {
            (json!({"error": "ETOOLNOTLOADED", "verb": name,
                "hint": format!("Use ostk_verbs('{}') to load this tool", name)
            }).to_string(), false)
        }
    }
}
```

**`handle_ostk_verbs` handler** (`tool_exec.rs`):

```rust
pub async fn handle_ostk_verbs(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let root = match root {
        Some(r) => r,
        None => return ("no project root".into(), false),
    };
    let query = input.get("query").and_then(|v| v.as_str()).unwrap_or("");
    let entries = language::parse_language_file(root).unwrap_or_default();
    let stamp = current_boot_stamp();

    // Match by verb name, doc, resolution, or category
    let matches: Vec<_> = entries.iter()
        .filter(|e| e.layer == "user" && e.tier <= 2)
        .filter(|e| {
            e.verb.contains(query)
                || e.doc.to_lowercase().contains(&query.to_lowercase())
                || e.resolution.contains(query)
        })
        .collect();

    if matches.is_empty() {
        return (format!("No verbs matching '{query}'. Try a broader query."), true);
    }

    // Build response with inline schemas
    let mut result = format!("Found {} verb(s) matching '{query}':\n\n", matches.len());
    for e in &matches {
        let schema = generate_tool_schema(e, stamp);
        result.push_str(&format!(
            "## ostk_{} (momentum {:.2})\n{}\nSignature: {}\nSchema: {}\n\n",
            e.verb, e.momentum, e.doc, e.signature,
            serde_json::to_string_pretty(&schema).unwrap_or_default()
        ));
        // Increment momentum for lookup (capped per boot cycle)
        let _ = language::record_verb(root, &e.verb, &e.resolution);
    }

    (result, true)
}
```

**`execute_language_tool`** — routes verb calls through the kernel shell:

```rust
async fn execute_language_tool(
    entry: &LanguageEntry,
    input: &Value,
    root: &Path,
) -> (String, bool) {
    let args = build_cli_args(&entry.verb, &entry.signature, input);
    let cmd = format!("ostk {}", args);
    // Route through kernel shell — audited, compressed, coordinated
    let output = crate::cpu::tool_exec::handle_bash(
        &json!({"command": cmd}),
        Some(&root.to_path_buf()),
    ).await;
    // Record verb usage for momentum tracking
    let _ = language::record_verb(root, &entry.verb, &entry.resolution);
    output
}
```

### Phase 1c: verbs.lock generation at shutdown

In `shutdown.rs`, after `compile_language()` (step 1c):

```rust
/// Write verbs.lock if absent (first shutdown) or on explicit refresh.
fn maybe_write_verbs_lock(root: &Path, force: bool) -> Result<(), String> {
    let lock_path = crate::state_dir(root).join("verbs.lock");
    if lock_path.exists() && !force {
        return Ok(()); // Stable — don't overwrite
    }

    let entries = language::parse_language_file(root).unwrap_or_default();
    let canonical = deduplicate_by_resolution(&entries);
    let threshold = env_threshold().unwrap_or(TOOL_RESIDENT_THRESHOLD);

    let verbs: Vec<&str> = canonical.iter()
        .filter(|e| e.layer == "user" && e.momentum >= threshold)
        .map(|e| e.verb.as_str())
        .collect();

    let mut content = String::from(
        "# verbs.lock — minimum resident tools for cold boot\n\
         # Generated by: ostk shutdown\n\
         # Updated by: ostk lock --refresh\n"
    );
    for v in &verbs {
        content.push_str(v);
        content.push('\n');
    }
    std::fs::write(&lock_path, content)
        .map_err(|e| format!("failed to write verbs.lock: {e}"))?;
    Ok(())
}
```

Also stage `verbs.lock` in the shutdown git-add (line 66-73 of `shutdown.rs`):
```rust
format!("{sd_str}/verbs.lock"),
```

### Phase 2: Device tools from live register

Tier-0 device entries with `momentum > 0` become `ioctl`-equivalent tools.
Device tool execution routes through the driver socket (`mcp_proxy.rs:417`):

```rust
fn execute_device_tool(verb: &str, input: &Value, socket_path: &str) -> (String, bool) {
    // JSON-RPC tools/call to driver socket
    send_to_driver(socket_path, verb, input)
}
```

Dead devices (momentum 0.00) generate no tool schema. When a device
comes alive mid-session, the tool appears on next boot (snapshot immutability).

### Phase 3: Session continuity

1. `ostk_session_history(n)` tool — reads `.prev` session file
2. Boot summary — one-paragraph summary injected into system prompt
3. Arrival detection — first contact vs continuation

### Phase 4: Memory management visibility

Add to the system prompt context block (after the tool surface block above):

```
# Memory (automatic, kernel-managed)
#
# Your context is demand-paged like your tools.
# compact: auto at 80% context budget (compact_20260112)
# tool_history: last 5 tool calls retained, older cleared
# cache: system blocks cached 1h (ephemeral TTL)
# boot_refresh: boot state block refreshed every 30s
# tool_surface: frozen at boot — momentum changes take effect next session
#
# The kernel manages your memory. You do not need to track token counts
# or request compaction. If context pressure rises, the kernel compacts
# automatically. If you need a tool not in your resident set, fault it
# in via ostk_verbs(query). The kernel handles the rest.
```

This block, combined with the tool surface block, replaces the current
`build_preload_context()` output (`session.rs:436-455`). Total context
injection: ~300 tokens (boot state + tool surface + memory model) instead
of ~2500 tokens (boot state + full `.language` text).

## What this eliminates

| Current | Replaced by |
|---------|------------|
| `Bash("ostk needle add 'title'")` | `ostk_needle(title="title")` |
| `Bash("ostk show status")` | `ostk_show(query="status")` |
| `Bash("curl url \| head")` | `fcp_web(url="...")` |
| Agent guessing CLI syntax | Typed tool schemas from .language |
| Opaque audit trail | Structured tool calls with typed params |
| No session memory | `ostk_session_history(5)` |
| No arrival protocol | Kernel detects first-contact at boot |
| Agent unaware of memory | System prompt documents memory model |
| 70 tools flat-loaded | ~24 resident + deferred via ostk_verbs |
| Dead tools burning tokens | Momentum gates residency |
| Semantic overlap confusion | Alias dedup collapses to canonical verbs |
| Silent Bash fallback | ETOOLNOTLOADED forces explicit discovery |
| Schema skew mid-session | boot_stamp + ESTALE rejection |
| Non-deterministic cold boot | verbs.lock pins bootstrap set |

## The Rule (extended)

Every Unix primitive has exactly one ostk equivalent. Every ostk
primitive is registered in `.language`. Every registered capability with a
user-facing signature is exposed as a structured MCP tool — either resident
(high momentum, libc equivalent) or deferred (low momentum, available via
`ostk_verbs`, the dlopen equivalent). The agent's resident tool list IS
the process's physical memory. The `.language` file IS the virtual address
space. `ostk_verbs()` IS the page fault handler. One source of truth,
from register to prompt, demand-paged.
