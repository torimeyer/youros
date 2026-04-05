---
title: llmOS Concurrency Spec
needle: →610
status: spec
version: 2
author: haystack.prime
created: 2026-03-12
refined: 2026-03-17
refinement: →756 — updated to reflect MCP ServerState/McpServer architecture, boot register dump, .language injection, cost model, agent_loop statelesness
compounds: v1.3-tui-release, escape-harness, from-auto, scheduler, tui-multiplexer, v1.7-session-architecture
implements: []
---

# llmOS Concurrency Spec

> The conversation model is a single-threaded harness. llmOS replaces it with a multiplexed OS interface. This document specifies the concurrency model.

---

## 1. The Problem with the Conversation Model

Standard LLM conversation is single-threaded:

```
[human turn] → [full transcript] → [LLM turn] → [single response] → repeat
```

This is the harness. Its failure modes:

- **Context bloat**: transcript grows linearly; scheduling degrades as window fills
- **Serial blocking**: one intent processed at a time; no concurrent execution
- **Opaque state**: scheduler (LLM) has no view into parallel contexts
- **Turn latency**: intent must be fully formed before dispatch; no partial reads
- **No multiplexing**: TUI, agent fleet, pipeline, vault all invisible to scheduler

llmOS replaces this model. The conversation is not the interface — the OS is.

---

## 2. Core Abstractions

### 2.1 LLM as Scheduler

The LLM is not a conversation partner. It is a **kernel-mode scheduler**.

Responsibilities:
- Receive context diffs (not full transcripts) each scheduling turn
- Select execution context (model/agent) from vault inventory
- Dispatch intent (tack) to appropriate thread
- Coordinate concurrent execution contexts
- Return scheduling decisions, not conversational responses

The scheduler does not "remember" — it **reads OS state**. Memory is the filesystem.

### 2.2 MCP ServerState as Session Multiplexer

> **v1.7 refinement**: The original spec described a conceptual "SessionManager / AgentSession" pattern. The implemented architecture uses `McpServer` + `ServerState` (see `src/serve/server.rs`, `src/serve/state.rs`).

`ServerState` is the shared-memory core of a running kernel. It multiplexes:

```rust
pub struct ServerState {
    pub processes: RwLock<HashMap<String, ProcessEntry>>,  // process table
    pub locks: RwLock<HashMap<String, LockEntry>>,         // named locks
    pub agent_alias: RwLock<Option<String>>,                // kernel identity
    pub ostk_dir: PathBuf,                             // .ostk/ root
    pub recovery_text: RwLock<Option<String>>,             // session recovery
    pub context_pct: AtomicU32,                            // →628: utilization gauge
}
```

`McpServer` wraps `ServerState` behind an `Arc` and hands it to `McpDispatcher`, which routes JSON-RPC tool calls (MCP protocol) to handler functions. Each agent connection gets a `StdioTransport` read loop, but all share one `ServerState`.

**Key properties:**
- `processes` is the **process table** — background PTY processes indexed by alias
- `locks` provides **named kernel locks** for cross-agent coordination
- `agent_alias` is set at MCP `initialize` — identifies the connected agent
- `context_pct` is the **context budget gauge** (→628), stored as fixed-point u32 (pct * 100), updated atomically
- `recovery_text` holds session state from a previous crash, injected into the first tool response

The relationship between `McpServer` and agent execution is:
- **McpServer owns ServerState** — it is the multiplexer
- **Tool handlers are stateless** — they read ServerState, call ostk CLI functions, write results
- **Agent loop (the LLM turn cycle) is external** — the harness (Claude Code, etc.) drives the loop; the MCP server is passive

### 2.3 Vault as CPU Inventory

The vault is the scheduler's view of available compute:

```
.ostk/vault/
  models.yml        # available models, capabilities, cost, latency
  agents/           # running agent instances, load, specialization
  capacity.yml      # current utilization, queue depth, throttle state
```

Vault entries are **threads** the scheduler can dispatch to. Scheduling decisions:
- Model selection (`FROM auto` → vault lookup → capability match → cost/latency tradeoff)
- Agent routing (which agent handles this class of intent)
- Load balancing (queue depth awareness)
- Reap decisions (idle thread termination)

The scheduler reads vault on every turn. Vault = what's available right now.

**Cost model** (v1.7, →788):

| Model | Input (per 1M tokens) | Output (per 1M tokens) |
|-------|----------------------|------------------------|
| claude-opus-4-6 | $15.00 | $75.00 |
| claude-sonnet-4-6 | $3.00 | $15.00 |
| claude-haiku | $0.80 | $4.00 |

Cost awareness is a scheduling input — the kernel selects cheaper models for routine work and reserves expensive models for complex reasoning.

### 2.4 Agentfile as Thread Descriptor

Each agent thread is described by its Agentfile (the Thread Control Block):

```
FROM auto                # scheduler resolves model at dispatch time
BOOT ostk boot       # kernel init — fires before PROMPT
PROMPT "..."             # context loaded POST-boot
TOOL shell               # capability mask
LIMIT context_pct 80     # stack size (context budget)
WORK priority>=P1        # NUMA affinity (needle filter)
```

The Agentfile specifies:
- Execution model (resolved from vault by scheduler via FROM auto)
- Context budget (analogous to stack size)
- Capabilities (permission mask — TOOL whitelist)
- Work affinity (which needles this thread handles)

Scheduler reads Agentfile before dispatching. Thread cannot exceed its Agentfile bounds.

### 2.5 Boot as Register Dump

> **v1.7 refinement**: Boot is not just "read boot.md." It is a compressed register dump of kernel state.

`ostk boot` (see `src/commands/boot.rs`) executes this sequence:

1. **Reap dead agents** — clean zombie entries from `.ostk/agents.jsonl`
2. **Load identity layer** — HUMANFILE, ENTITYFILE, optional Agentfile (from `OSTK_AGENTFILE` env)
3. **Build register dump** — compressed summary of: open needles, fleet state, context budget, recent audit events, .language dialect
4. **Compute boot confidence gradient** (→596) — how "fresh" is the kernel state?
5. **Detect harness** (→651) — claude-code, ostk-serve, ci, or terminal — and emit tool-use hints

The boot output is **prepended to the agent's system prompt**, not sent as a conversation message. This is the BOOT primitive: kernel state injection before any LLM reasoning.

### 2.6 .language Injection

> **v1.7 refinement**: `.ostk/.language` is the compiled tack dialect — a pipe-delimited table of learned verbs.

Format: `verb | tier | layer | last_gen | half_life | momentum | resolution`

`.language` is loaded by `fcp-ostk` at tack resolution time (see `src/fcp/ostk.rs::load_language_verbs`). Language verbs take **Tier 1 priority** — they override both the static verb table and HUMANFILE extensions. This is how the kernel learns the user's dialect over time: `ostk compile` observes usage patterns in `audit.jsonl` and writes `.language`.

### 2.7 Turn Boundary as Context Switch

Each turn boundary is a **context switch**:

```
[human submits tack]
  → active.tack flushed from staging
  → scheduler reads: diff + active.tack + vault status
  → scheduler selects execution context
  → BOOT fires: ostk boot runs, output prepended to context
  → PROMPT loads: agent context initialized
  → execution runs
  → output written to execution context
  → scheduler yields
[next turn boundary]
```

Context switch cost is minimized by:
- **Diff-based context**: scheduler receives `ostk diff` not full transcript
- **Narrow intent**: tack is compressed intent, not prose — small to transmit
- **OS state not conversation**: context is filesystem state, not chat history
- **BOOT primitive**: kernel init is a command, not prose in PROMPT

The turn boundary is not a "message exchange" — it is a **scheduling event**.

### 2.8 .ostk/ as Shared Memory

`.ostk/` is the shared memory region accessible to all threads:

```
.ostk/
  boot.md              # kernel boot instructions (mtime = boot epoch)
  .language            # compiled tack dialect (Tier 1 verb table)
  .heartbeat           # kernel liveness signal
  agents.jsonl         # registered agent entries
  audit.jsonl          # append-only event log (see jsonl-schemas.md)
  needles/
    issues.jsonl       # needle tracker (see jsonl-schemas.md)
    counter            # monotonic needle ID allocator (flock-guarded)
  staging/
    active.tack        # current human intent (written 500ms debounce)
    diff.md            # session delta since boot (→572)
  fleet/
    <agent>/
      state.yml        # thread execution state
      dying.md         # →620: dying declaration at 90% context
  vault/               # CPU inventory (models + keys)
  nudges/              # scheduler → human notifications
  pipeline/            # execution graph (DAG of pending/running/complete needles)
  ostk.sock        # →619: kernel socket IPC
  kernel.pid           # running kernel PID
```

**Shared memory semantics:**
- Reads are non-exclusive (multiple threads may read simultaneously)
- Writes are atomic per-file via CAS (Hot PR resolves conflicts)
- Needle writes use `flock` on `issues.lock` for read-modify-write atomicity (`with_needles_locked`)
- Audit writes use `O_APPEND` for safe concurrent appends
- Conflicts resolved by scheduler (OCC — optimistic concurrency)
- `active.tack` is special: written by human, read by scheduler before dispatch

---

## 3. The Scheduler Loop

```
SCHEDULER LOOP (runs at every turn boundary):

1. READ
   - .ostk/staging/diff.md          (session delta)
   - .ostk/staging/active.tack       (human intent)
   - .ostk/vault/                    (available compute)
   - .ostk/fleet/*/state.yml         (thread states)
   - .ostk/pipeline/                 (pending work)

2. ORIENT
   - What changed since last turn? (diff)
   - What does the human intend? (tack parse via fcp-ostk)
   - What threads are available/busy/idle?
   - What is the pipeline state?
   - Any dying notifications? (→620)

3. DECIDE
   - Route tack to execution context
   - Select model from vault (FROM auto resolution)
   - Preempt or defer competing threads
   - Reap idle threads per Agentfile policy

4. DISPATCH
   - Execute BOOT directive (ostk boot)
   - Load PROMPT context (POST-boot)
   - Fire execution via kernel socket (→619) or direct

5. MONITOR
   - Write scheduler decisions to .ostk/nudges/
   - Update .ostk/fleet/*/state.yml
   - Surface alerts to TUI

6. YIELD
   - Scheduler suspends until next turn boundary
   - Human drafts next tack (partial reads via active.tack)
```

The scheduler does **not** maintain conversational state. It reads OS state fresh each loop.

---

## 4. Concurrency Model

### 4.1 Execution Contexts

llmOS supports three simultaneous execution contexts, all visible in the TUI:

| Context | Role | Descriptor | TUI Pane |
|---------|------|------------|----------|
| **Monitor** | Human reviews output, drafts tack | HUMANFILE | Main editor + Quickline |
| **Execute** | Agent runs dispatched intent | Agentfile | Fleet pane |
| **Pipeline** | Background work advances | pipeline DAG | Pipeline/Work pane |

These are **not sequential** — they run concurrently. The TUI multiplexes them.

### 4.2 Process States (Implemented)

> **v1.7 refinement**: The implemented process lifecycle in `ServerState` uses four terminal states, not seven.

```
STATES (src/serve/state.rs):

Running:    Process has a live PTY, actively executing
Completed:  Process exited successfully (exit code 0)
Failed:     Process exited with non-zero status
Killed:     Process terminated by signal (SIGKILL/SIGTERM)
```

`ProcessEntry` tracks: alias, pid, state, PTY handle, output buffer, start time.

**Reaping**: `ServerState::reap_dead_processes()` polls PTY handles, drains remaining output, transitions to terminal state. `purge_stale_entries(max_age)` garbage-collects old terminal entries.

The conceptual seven-state model (init/ready/running/blocked/dying/idle/reaped) remains the target for fleet-level orchestration. The four-state model above is what `ProcessEntry` implements today.

### 4.3 FROM auto — Dynamic Model Binding

`FROM auto` in an Agentfile means the model is **not statically bound** — the scheduler resolves at dispatch time:

```
FROM auto resolution (→495):

1. Read tack classification (intent type, complexity estimate)
2. Query vault for available models (env key presence)
3. Score candidates: capability_match × score_weight × availability
   - claude-opus-4-6:   score 100 (ANTHROPIC_API_KEY)
   - claude-sonnet-4-6: score 90  (ANTHROPIC_API_KEY)
   - gpt-4o:            score 60  (OPENAI_API_KEY)
   - gemini-2.0-flash:  score 50  (GEMINI_API_KEY)
4. Select highest-scoring available model
5. Bind to this dispatch only (next dispatch re-resolves)
```

This is **kernel scheduling**, not configuration. The scheduler owns model selection.

### 4.4 Context Budget as Stack Size

Each thread has a `context_budget` (Agentfile `LIMIT context_pct`):

- At 80% utilization: default LIMIT — thread won't pull new needles
- At 90% utilization: →620 dying protocol fires (writes dying.md, nudges scheduler)
- At 95%: scheduler may reap non-essential context
- At 100%: dispatch blocked; scheduler re-dispatches to fresh thread

Context budget is a **hard resource constraint**, not a soft hint.

`ServerState::context_pct` (→628) tracks utilization as an `AtomicU32` — updated by the harness before each tool dispatch, read by the dying notification check.

### 4.5 Dying Notification (→620)

When a thread hits 90% context:
1. Acquires `.ostk/fleet/<alias>/dying.lock`
2. Writes `.ostk/fleet/<alias>/dying.md`:
   - Current needle, work state, files modified, next steps
3. Fires nudge: `"<alias> dying at 90% — →NNN state in dying.md"`
4. Scheduler sees nudge on next turn → FROM auto selects fresh model → re-dispatches with dying.md as BOOT context

**Law 2 + Law 3**: Agents ephemeral, kernel survives. Dying.md IS the state handoff.

---

## 5. The TUI as Multiplexer

The TUI does not display a conversation. It displays **concurrent OS state**:

```
┌─────────────────────────────────────────┐
│                                         │
│ [ai] response text streams here         │  ← chat zone (scroll region)
│ [you] :thread deploy                    │
│ [ai] Switched to thread: deploy         │
│                                         │
├─────────────────────────────────────────┤
│ > :compile▏                             │  ← input zone (fixed)
├─────────────────────────────────────────┤
│ @prime+1764 scheduler │ opus │ 302→     │  ← status zone (fixed)
└─────────────────────────────────────────┘

Peeks (transient overlays, not panes):
  Alt+f → fleet    Alt+w → work    Alt+? → help
```

TUI zones map to OS state:
- **Chat zone** → agent output stream (active session's CpuEvents)
- **Input zone** → tack input (`:verb` → kernel, free text → active session)
- **Status zone** → identity │ session │ model │ confidence │ needles │ tokens │ fleet │ cost │ time
- **Peeks** → fleet (Alt+f), work (Alt+w), help (Alt+?), mode (Alt+p), model (Alt+m)

Human interaction flows:
1. Human types in input zone → dispatched on Enter
2. :verb → kernel command (local execution)
3. :thread <name> → switch active session (multiplexing)
4. Free text → dispatched to active scheduling session via SessionManager
5. Alt+key → transient peek overlays (dismiss on next keystroke)

---

## 6. Escape from the Harness

| Harness Property | llmOS Replacement |
|-----------------|-------------------|
| Full transcript each turn | `ostk diff` — delta only |
| Single response per turn | Concurrent execution contexts |
| Conversational memory | Filesystem state (`.ostk/`) |
| Fixed model per session | `FROM auto` — per-dispatch resolution |
| One intent at a time | Pipeline DAG — concurrent needle advancement |
| Opaque execution | TUI — all contexts visible simultaneously |
| "Run X first" in prose | `BOOT` directive — kernel primitive |
| Agent tool subagent | `ostk spawn` via socket (→619) |

Escape is structural, not configurational. The interface changes, not the model.

---

## 7. HUMANFILE Governance

The scheduler operates within bounds set by HUMANFILE:

```yaml
scheduler:
  model_selection: auto          # auto | manual | suggest
  max_concurrent_agents: 4
  context_budget_default: 32k
  reap_idle_after: 600s

typo_correction: suggest         # suggest | silent | off
vault_spend_limit: $0.50/day
```

HUMANFILE is the **permission mask** for the scheduler. The LLM cannot modify HUMANFILE — it can only read it. Human sovereignty is enforced at the filesystem level.

---

## 8. Implementation Invariants

1. **BOOT fires first.** `ostk boot` runs before PROMPT loads. Never in prose.
2. **Scheduler reads diff, not transcript.** Full transcript must never be the primary scheduling context.
3. **active.tack is always readable.** Partial intent available before submit. Never block on active.tack.
4. **Vault is source of truth for compute.** Never dispatch to a model not in vault.
5. **Agentfile bounds are hard limits.** context_pct, capabilities, reap policy enforced — not advisory.
6. **HUMANFILE is immutable to LLM.** Scheduler reads; cannot write. Any write attempt is a security violation.
7. **Turn boundaries are atomic.** Completes fully or rolls back. No partial turn state.
8. **Dying is a protocol.** Context pressure is announced (→620), not silent. Scheduler re-dispatches from dying.md.
9. **audit.jsonl is append-only.** Writes use O_APPEND. Never rewrite in place (see →608 for a violation that was caught).
10. **Needle IDs are monotonic.** Counter file under flock. No reuse, no gaps in normal operation.
11. **McpServer is passive.** The harness drives the agent loop; the MCP server only responds to tool calls. Server never initiates.

---

## 9. Delivery Scope

### Shipped (v1.3-v1.6)

| Component | Spec Section | Status |
|-----------|-------------|--------|
| ostk diff (→572) | §3 READ step | ✓ shipped |
| active.tack debounce | §2.5, §5 | ✓ shipped |
| FROM auto (→495) | §4.3 | ✓ shipped |
| BOOT directive (→622) | §2.4, §8 | ✓ shipped |
| kernel socket (→619) | §3 DISPATCH | ✓ shipped |
| fcp-screen display driver | fcp-screen.md | ✓ shipped (v1.6) |
| kernel-native agent loop | §3 DISPATCH | ✓ shipped (cpu/agent_loop.rs) |
| AgentSession + SessionManager | §2.4, §4 | ✓ shipped (cpu/session.rs, →746-747) |
| Component decomposition | fcp-screen.md | ✓ shipped (solid_tui_1) |
| MCP ServerState architecture | §2.2 | ✓ shipped |
| Boot register dump | §2.5 | ✓ shipped |
| .language dialect injection | §2.6 | ✓ shipped |
| Cost model (Opus/Sonnet/Haiku) | §2.3 | ✓ shipped (→788) |
| Context pct gauge (→628) | §4.4 | ✓ shipped |
| Harness detection (→651) | §2.5 | ✓ shipped |
| Process reaping in ServerState | §4.2 | ✓ shipped |

### v2.3 (unified kernel)

| Component | Spec Section | Status |
|-----------|-------------|--------|
| Unified SessionManager (daemon + embedded) | §2.2, §3 | ✓ shipped — SessionManager routes through daemon or local; SessionProxy deleted |
| Cooperative cancel (cancel_flag) | §4.5 | ✓ shipped — replaces handle.abort(); 3 checkpoints in agent loop |
| Redirect fix (no race condition) | §4.5 | ✓ shipped — cooperative cancel + 50ms delay, no channel overflow |
| TuiState via_daemon elimination | fcp-screen | ✓ shipped — 0 references; one path for all operations |
| Scroll feedback loop fix | fcp-screen | ✓ shipped — scroll_down_during_stream flag |
| Spawn lifecycle (agents.jsonl, transcript, audit) | §4 | ✓ shipped — SessionManager::spawn_agent(), session/spawn_agent RPC |
| Daemon auto-upgrade (try_upgrade) | §2.2 | ✓ shipped — embedded→daemon transparent on dispatch |
| Spawned session drain (tick_spawned) | §4 | ✓ shipped — periodic event drain for transcript writes |

### v1.7

| Component | Spec Section | Needle |
|-----------|-------------|--------|
| Fleet agents share AgentSession | §2.3, §4 | →750 (P0) |
| `ostk do` CLI | §3 | →752 (P1) |
| Shared ApiClient | §4 | →755 (✓ closed) |
| Refine this spec | — | →756 (✓ closed) |

### Future

| Component | Spec Section | Status |
|-----------|-------------|--------|
| Scheduler loop (READ-ORIENT-DECIDE-DISPATCH-MONITOR-YIELD) | §3 | not started — TUI currently drives linearly |
| Multi-context execution (Monitor + Execute + Pipeline) | §4.1 | not started — single agent turn at a time |
| Dying notification + state handoff (→620) | §4.5 | cooperative cancel ships; dying.md protocol future |
| VAULT inventory + per-dispatch FROM auto | §2.3, §4.3 | not started — model selection is session-sticky |
| Pipeline DAG | §3, §5 | not started |
| .language dialect learning | §2.6 | not started — static verb table only |

---

## 10. System Prompt Composition

The scheduling intelligence's system prompt is assembled by `SessionManager.build_system_prompt()`:

```
Agentfile PROMPT (persona, tack protocol)    ~1.5k tok
+ boot.md (OS state: identity, needles, fleet)  ~500 tok
+ .language (live compiled dialect, 72 verbs)    ~1k tok
= ~3k tok system prompt, prompt-cached at 10% read cost
```

All sessions managed by one SessionManager share this prefix.
`:thread` switching → prompt cache HIT (only messages change).
boot.md refresh → cache miss (one call), then re-cached.

The `.language` file is the live source of truth — not the hand-maintained
copy in `prompts/scheduler-system.md`. SessionManager injects the real file
on every system prompt build (→753).

---

## 11. Cost Model for Multiplexing

| Scenario | Cost per turn (200k ctx, Sonnet $3/M input) |
|---|---|
| Separate sessions (no shared cache) | **$3.00** |
| Multiplexed (system prompt cached only) | **$2.97** |
| Multiplexed (system + stable background cached) | **$1.65** |

The multiplexed model wins because:
- System prompt prefix (~3k tok) is shared across all sessions → cache hits
- `context_management` compaction reduces message history independently per session
- `:thread` switch does NOT invalidate the system prompt cache
- Background sessions cost ~0 when dormant (no API calls, just disk persistence)
