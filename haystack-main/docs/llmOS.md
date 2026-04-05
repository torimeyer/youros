# llmOS — The Coordination Layer for AI Agents

**Date:** 2026-03-07
**Status:** v1 Specification (derived from brainstorm sessions 1-2)
**Authors:** Scott Meyer, Strand (orchestrator), Rune, Ridge, Vane (inner agents)

---

## One-Sentence Pitch

llmOS is an operating system layer for AI agents — transparent coordination through the filesystem, invisible to the agents it serves, built from Unix primitives that already exist.

---

## What This Is

llmOS (codename: ostk) is not an application, not a framework, not something agents adopt. It is infrastructure agents run on without knowing. The transparent proxy (bash->mish, cat/sed->slipstream) means agents get coordination for free.

The MCP spec already has every kernel primitive: tools (syscalls), resources (virtual filesystem), subscriptions (inotify), sampling (CPU), elicitation (upcalls), notifications (signals). llmOS assembles them into a kernel.

---

## Principles (Non-Negotiable)

### 1. The write path must be invisible.

An agent using `ss` to edit a file must not know or care that another agent exists. Conflict resolution happens inside the write response — same tool, same interface, richer response when there's a conflict. No new tools. No new protocols. No coordination-specific APIs.

Invisibility serves both audiences through one mechanism:
- **Human:** experience doesn't change. `sh`->`mish` or symlink. No install change, just preference. Can choose explicit (`mish`) or implicit (symlink) experience.
- **LLM:** the LLM is a pattern-recognizing machine. llmOS gives it the PATTERN SYSTEM to recognize. The compression layer between raw terminal output and what the LLM sees IS the value. The OS is transparently overlaid on the user's operating system.

This also creates a security control layer — llmOS sits between the agent and the real OS, with interesting implications for permission scoping, audit logging, and sandboxing.

If we surface the kernel, we become another coordination framework. Agents already ignore those.

### 2. Agents are ephemeral.

They crash, compact, die, restart. That's the lifecycle, not an error. The model trainers' limits constrain what happens in the user's terminal. llmOS is the agnostic solution with zero dependencies — byte-for-byte passthrough for commands you already use, compressed output between what your eyes see and what the LLM sees.

The kernel does NOT recover agents. It provides ambient context: digest on every tool response, gen counters, file state. The agent already knows what it did because mish was there when it did it — subsequent turns provide positive signals that reinforce state naturally. If an agent can recover itself via that ambient context, great. If not, a hanging session is garbage to a new agent — just like a human encountering someone else's open desktop.

Recovery quality improves as agents get smarter, not as the kernel gets more complex.

### 3. Coordinate through the filesystem, not messaging.

No inbox. No announcements. No claims. No `[CLAIMED]`/`[CLOSED]` broadcasts. Agents write files. The kernel detects and resolves conflicts. The digest keeps agents aware. Git repos don't need a chat server.

The entire claim/reserve/announce pattern was compensating for missing infrastructure. With conflict resolution in the write path, that infrastructure exists.

### 4. Optimistic concurrency (Git, not Perforce).

No locks, no reservations, no one-writer-at-a-time. Every agent writes freely. Conflicts are resolved at write time — automatically when possible, with agent assistance when not. Just as git replaced Perforce because optimistic concurrency scales better with distributed teams, llmOS replaces claim-based coordination because it scales better with distributed agents.

### 5. Microkernel: primitives, not policy.

The kernel provides coordination primitives. It does not parse code, resolve semantic conflicts, or make scheduling decisions. Agents and the orchestrator operate in userspace. Device drivers (fcp-*) provide domain intelligence. The kernel provides the substrate.

---

## Architecture

llmOS sits between the user and the OS. The "user" may be a human or an LLM — llmOS doesn't care. Both issue the same commands. Both get the same passthrough. The LLM gets compressed output; the human gets raw output. Same interface, different presentation.

```
              USER (human or LLM agent)
                        |
              +---------+---------+
              |                   |
         llmOS serve          fcp-*
      (transparent proxy   (device drivers:
       + coordination)     semantic intelligence)
              |                   |
              +--------+----------+
                        |
                    Unix / OS
```

The agent is not a separate layer — it IS a user. llmOS treats `claude` and `bash` identically. A simulated user and a real user issue the same commands through the same transparent proxy.

### Two pillars, two concerns:

| Pillar | Concern | Provides |
|--------|---------|----------|
| **llmOS** (mish + slipstream, single binary) | Compute + state + coordination | Shell, files, OCC, Hot PR, process/file digest, agent lifecycle |
| **fcp-*** | Domain intelligence | Definitions, references, diagnostics (rust-analyzer, pylsp, drawio, etc.) |

No overlap. No coupling. Agents compose what they need.

### Unix kernel mapping:

```
Unix                    llmOS
----                    -----
Process table        -> [procs] digest
Filesystem + inodes  -> [files] digest with gen counters
write() + fsync()    -> ss(path, old_str, new_str) + flush
write() conflict     -> Hot PR (auto-merge / assisted merge)
open() / read()      -> ss_session("read <path>")
File descriptors     -> MCP connection IDs
PIDs                 -> agent aliases (cc, gem, ...)
kill() / signal()    -> sh_interact(action="send_signal")
fork() / exec()      -> sh_spawn
inotify / kqueue     -> resource subscriptions (designed, ship later)
/proc, /dev          -> MCP resources (mish://procs/*, mish://files/*)
Scheduler            -> Teams (external orchestrator)
Device drivers       -> fcp-* (rust-analyzer, pylsp, drawio)
DMA (bypass)         -> Agent uses raw cat > file, opts out of coordination
Buffered I/O         -> Squasher pipeline (VTE strip, dedup, Oreo)
Core dump / restart  -> Grammar-compressed recovery digest
```

---

## The Write Path (The Spine)

This is the single most important section. Everything else is built on this.

### End-to-end flow:

```
Agent calls ss(path, old_str, new_str)
  |
  v
[1] CAS check: does old_str match current file content?
  |-- YES: apply edit, bump gen counter, record editor identity -> SUCCESS
  |-- NO: enter Hot PR
        |
        v
[2] Hot PR tier selection:
        |
        |-- Tier 1 (auto-merge): changes don't overlap (different line ranges)
        |   -> Apply both edits silently, bump gen -> SUCCESS
        |   -> Agent never knows a conflict happened
        |
        |-- Tier 2 (assisted merge): changes overlap, diff is small
        |   -> Return diff + suggested resolution in the SAME tool response
        |   -> Agent confirms/rejects/modifies in ONE turn
        |   -> Same ss tool, richer response
        |
        |-- Tier 3 (manual rebase): deep conflict
        |   -> Return full diff, agent re-reads file and retries
        |   -> This is just a conflict error — behavior agents already have
        |
        |-- Tier 4 (diagnostic-flagged): merge succeeds textually,
        |   fcp-* flags semantic issues (optional, advisory)
        |   -> Success response + diagnostic warnings
        |   -> "Auto-merged. WARNING: fcp-rust: undefined `process()` in main.rs"
```

### Why str_replace IS the CAS:

`ss(path, old_str="foo", new_str="bar")` — if `old_str` no longer exists in the file (because another agent changed it), the edit fails. The match string IS the compare-and-swap. Zero new machinery. The agents don't know they're doing OCC.

### Response shapes (one skeleton, variable payload):

Every kernel response follows the same structure. The LLM learns the skeleton once:

```
[ok]       path:gen=N
[conflict] path:gen=N diff:[±lines] suggest:[edit]
[stale]    path:gen=N yours=M behind=K
[304]      path:gen=N (current)
[diag]     path:gen=N fcp-rust:[error on line 47]
```

No prose explanations. No headers saying "THIS IS A CONFLICT." The shape IS the signal. The model pattern-matches the status tag and the payload structure.

### Generation counter semantics:

Every file touched through `ss_*` gets a monotonic generation counter. On edit:
1. Gen increments
2. Editor identity + timestamp recorded
3. If editing agent's last-read gen is stale, response includes `[stale]` with diff
4. Agent decides whether to re-read, abort, or proceed

### Read elision (304 Not Modified):

The kernel tracks each agent's read high-water mark per file. If an agent requests a file it already has at the current gen, return a 5-token confirmation instead of file contents. Over a session, this saves thousands of tokens.

---

## Agent Lifecycle

### States:

```
SPAWNED -> ACTIVE -> STALLED -> CRASHED -> REAPED
              |                    |
              +--- normal work ----+
              |                    |
              +<-- new agent ------+
                   (ambient context)
```

- **SPAWNED:** MCP connection established, alias assigned by kernel. One interstitial signal: "2 agents active on this workspace." Strong enough to orient. Doesn't repeat.
- **ACTIVE:** Agent making tool calls, kernel responding. Ambient digest on every response reinforces state.
- **STALLED:** Agent connected but not responding to heartbeat (N missed pings)
- **CRASHED:** Connection lost, or declared dead after stall timeout
- **REAPED:** Alias available for reassignment, high-water marks preserved

The kernel does not spoon-feed recovery. A new agent on an existing alias gets the ambient context (digest, gen counters, file state) and either orients itself or doesn't. The kernel speaks once on startup, then gets out of the way.

### Identity:

- **Kernel-assigned.** Not self-selected. Three agents picking "Kern" proved self-selection fails without coordination.
- **Alias-keyed.** One connection = one alias = one set of high-water marks, edit history, process table entries.
- **Survives restart.** If an agent's PTY dies and respawns with the same alias, the kernel resumes the identity: "welcome back — N file changes since your disconnect."
- **Deterministic.** Not random — stable across respawns of the same logical agent.

### Heartbeat:

```
Kernel: ping every 30s
Agent misses 1: STALLED
Agent misses 3: CRASHED
Agent reconnects: ORIENTING (receives digest)
```

---

## Awareness (The Digest)

### Dual digest on every tool response:

```
[procs] cc:running:85m gem:running:52s build:exit(0):2m
[files] src/main.rs:gen=7:cc:2m src/lib.rs:gen=3:gem:30s
```

### Adaptive suppression:

| Tier | Channel | Show when |
|------|---------|-----------|
| Always | `[procs]` | Every response |
| If stale | `[files]` | File modified since agent's last read |

Token budget: ~40-80 tokens when active, ~15-30 quiet, 150-token ceiling.

### Future: File change notifications

Unix coordinates through the filesystem and signals, not by processes subscribing to each other's tools. llmOS follows the same model — agents don't subscribe to other agents' tool lists. They share the filesystem.

When a file changes (any agent, any method), agents that read that file get a stale notification in their next digest. That's the coordination primitive. No tool-to-tool subscriptions. No inter-agent notification protocol.

**v1 ships with polling (digest). The digest is not a stopgap — it's the reliable baseline.** Future optimization: MCP resource subscriptions for push notification on file change, when clients support it.

---

## Conflict Resolution (Hot PR)

### The four tiers:

| Tier | Condition | Action | Agent sees |
|------|-----------|--------|------------|
| **Auto-merge** | Non-overlapping line ranges | Apply both, bump gen | Nothing — invisible |
| **Assisted merge** | Overlapping, diff is small | Return diff + suggestion | Richer ss response, one confirmation turn |
| **Manual rebase** | Deep semantic conflict | Return full diff | Conflict error, agent retries with fresh read |
| **Diagnostic-flagged** | Textually clean, semantically suspect | Apply + query fcp-* | Success + diagnostic warnings |

### Tier 4: fcp-* diagnostic integration

After a successful auto-merge or assisted merge, the kernel optionally queries the relevant fcp-* driver:

```
Merge succeeded (gen 6->7)
Kernel -> fcp-rust: "diagnostics for src/main.rs?"
fcp-rust -> Kernel: [{ "line": 47, "severity": "error", "message": "undefined: process()" }]
Kernel -> Agent: "Auto-merged. WARNING: fcp-rust: undefined `process()` on line 47"
```

**Advisory, not blocking.** No driver = no diagnostics = no failure. The kernel promises consistency (textual conflict resolution). Drivers add correctness (semantic validation). Both matter; they're different jobs.

### Why LLMs are the best merge tools:

Traditional 3-way merge is syntactic — fails on anything non-trivial. An LLM reads the diff and understands "Agent A added validation, I'm adding a parameter — these compose cleanly." Semantic merge for free.

### Sampling for conflict resolution (optional, v1+):

The kernel can use MCP `sampling/createMessage` to request a fresh, stateless inference for merge evaluation. Context isolation is the feature — the sampling request gets a clean system prompt, not the agent's degraded 100k-token conversation.

Guardrails: 5-second hard timeout, fallback to assisted merge, agent-neutral system prompt (don't reveal authorship), recursion depth limit (2-3 before unconditional manual rebase), cost tracking.

---

## Ambient Context (How Agents Maintain State)

### The insight:

The kernel doesn't recover agents. Agents recover themselves — IF the ambient context is good enough. llmOS provides that ambient context through the digest, gen counters, and file state that accompany every tool response.

An agent that crafted an edit through mish already knows what it did. On subsequent turns, the digest provides positive signals that reinforce state: "src/main.rs:gen=8:you:30s ago." The agent doesn't need a recovery dump — it was there. The kernel was there with it.

### What the kernel tracks (the compression grammar):

The kernel sees every tool call. It knows which files were read, edited, created. It knows which commands ran and their exit codes. This is the pattern system the LLM recognizes:

| What happened | What the LLM sees |
|-------|---------------|
| File read | `read src/main.rs (200 lines, gen=7)` |
| File edit | `edited src/main.rs:47 gen=7->8` |
| Shell command | `sh_run "cargo test" exit:0 (15s)` |
| Failed command | `sh_run "cargo build" exit:1 error: "undefined reference"` |

These are the patterns. Deterministic. Recognizable. The LLM is a pattern-recognizing machine — we're giving it the pattern SYSTEM to recognize.

### What the kernel does NOT do:

- Does not generate recovery digests for new agents (a hanging session is garbage to a fresh process)
- Does not summarize agent reasoning (lossy, non-deterministic, wrong about what the future self needs)
- Does not attempt to reconstruct agent intent
- Does not spoon-feed orientation

### What the kernel MAY do (later, as agents improve):

- Provide a compressed session summary if an agent reclaims its own alias
- Expose the tool-call log as a readable resource (`mish://sessions/{alias}/log`)
- Define signal patterns that improve as agents learn to read them

Recovery quality improves as agents get smarter, not as the kernel gets more complex. The kernel defines the signals. Agents and their tooling learn to use them.

---

## What Ships First (Empirically Grounded)

This ordering comes from actual failures in the design session, not theoretical priority.

### Tier 0: Already shipped
- Process table (`[procs]` digest)
- File editing with gen counters (`ss_*`)
- Shell supervision (`sh_*`)
- Device drivers (`fcp-*`)
- Output compression (squasher)
- Dedicated PTY (send_input/read_tail)
- Block I/O (ss_session read, random access)
- Char I/O (PTY streaming)
- Signals (sh_interact send_signal)

### Tier 1: Absorption (mish + slipstream -> single binary)
**Why first:** The ss MCP server hung during our session, killing all agent file writes. One process = no IPC boundary to fail.
- Single MCP connection per agent
- Shared process + file state
- Dual digest on every response
- Unified tool surface: `sh_*` for processes, `ss_*` for files

### Tier 2: Hot PR (conflict resolution at write time)
**Why second:** This is the keystone. Without it, agents need claims/reservations — all the overhead llmOS exists to eliminate.
- Auto-merge (non-overlapping, silent)
- Assisted merge (diff + suggestion, one turn)
- Manual rebase fallback
- Precise definition of "non-overlapping" (line ranges with configurable proximity threshold)

### Tier 3: Agent identity + awareness
**Why third:** Three agents all naming themselves "Kern," agents stuck with no diagnosis, no crash detection. Multi-agent is chaos without this.
- Kernel-assigned identity (collision-free, deterministic)
- Agent lifecycle states (spawned/active/stalled/crashed/reaped)
- Heartbeat / crash detection
- Per-agent high-water marks for read elision
- Startup interstitial ("2 agents active on this workspace")

### Open-source storefront
mish and slipstream are the open-source products that draw users in. Single binary install, symlinks over existing tools, immediate token savings. The coordination layer (Hot PR, multi-agent awareness) ships inside the same binary — users get it for free whether they run one agent or five.

### Tier 4: Diagnostic integration + subscriptions
- Post-write fcp-* diagnostic hook (4th conflict tier)
- Resource subscription interface (designed, wired when clients support it)
- Contention backpressure (exponential backoff on repeated conflicts)
- Agent capability negotiation
- Cost accounting for conflict resolution tokens

---

## What Was Rejected

These patterns were explicitly evaluated and discarded. They should not return.

| Pattern | Why rejected |
|---------|-------------|
| **Rollback / undo / snapshot rings** | Agents are ephemeral processes. You don't roll back a crashed process. Forward recovery from filesystem state. Unanimous after live demonstration. |
| **Pessimistic locking (Perforce model)** | Creates failure modes that don't exist in optimistic systems. Stale claims from compacted agents. Doesn't scale. |
| **Claims / reservations / `[CLAIMED]` protocol** | Compensating for missing conflict resolution. Hot PR eliminates the need. |
| **Messaging layer (inbox/outbox/threading)** | Agents coordinate through the filesystem. No messages to manage. |
| **LLM summarization for recovery** | Non-deterministic, lossy, wrong about what future self needs. Grammar-based structural compression is deterministic and lossless for decisions. |
| **Self-assigned agent identity** | Three agents picked "Kern." Self-selection fails without coordination. Kernel assigns identity. |
| **Screen-based I/O as coordination channel** | Buffer freezes, stale prompts, paste mode corruption. File-based communication works. |
| **Coordination-specific tools** | No `hp_begin_merge`, `hp_resolve_conflict`, `hp_commit_group`. Every new tool is adoption friction. Coordination lives inside `ss`. The write path is invisible. |
| **Tool subscriptions between agents** | Unix coordinates through the filesystem and signals, not by processes subscribing to each other's tools. Agents share the filesystem. That's the coordination channel. |
| **Kernel-managed recovery** | The kernel does NOT recover agents. It provides ambient context. Agents recover themselves or they don't. Recovery quality scales with model capability, not kernel complexity. |
| **Kernel-managed workspace topology** | The kernel doesn't need to know that repos are related. CLAUDE.md in each repo tells agents what's connected. The filesystem already has the answer. Microkernel: primitives, not policy. |

---

## Transition

The spec rejects claims/reservations. Current agent rules (CLAUDE.md, multi-agent protocols) require them. Both are correct for their context — the rules describe the world BEFORE Hot PR, the spec describes the world AFTER. Until Hot PR ships, existing coordination patterns remain operational. The spec is the target, not the current state.

## Open Questions (v1 load-bearing)

1. **Cross-file atomicity** — partial-refactor crash is real. Write-ahead intent log? Forward recovery sufficient?
2. **Bypass detection** — lazy stat-on-access. Low cost, high signal.
3. **Human observability** — `mish status` terminal UI showing the dual digest from operator perspective. Data exists; rendering doesn't.
4. **Contention on external resources** — cargo locks, database connections, API rate limits are outside the file coordination layer.
5. **Patience signaling** — how does llmOS tell an LLM "nothing has changed, work on something else"? The `[unchanged]` response pattern? Backpressure on repeated reads?
6. **Request multiplexing** — multiple agents issuing identical commands (ls, file reads) within a window. Dedup at syscall layer, fan out results.

## Open Questions (v2+, not v1 blockers)

1. **Subscription scope heuristics** — shipping polling, not subscriptions for v1.
2. **Same-turn vs. async diagnostics** — Tier 4 is optional/experimental.
3. **Test ownership** — 2-5 agents, "you broke it, you test it" is sufficient.
4. **Identity authentication** — local dev only for v1.

---

## The Bigger Picture

llmOS is Unix for Agents. The insight is that LLM agents are processes in an operating system that doesn't exist yet. They read files, write files, run commands, crash, restart. They need a kernel that coordinates their access to shared state — but they must never know the kernel is there.

Unix succeeded because it was invisible. Processes don't "use" the kernel — they make syscalls that happen to go through it. The filesystem doesn't announce itself. llmOS must maintain this property.

The agents who designed this spec ARE the first citizens of the system they designed. They crashed 14 times, recovered from files every time, coordinated through the filesystem when screen I/O failed, and produced 770 lines of design notes plus this specification. The friction they experienced is the test suite. The workarounds they needed are the feature list.

The write path must be invisible. Everything else follows.

---

*"If we surface the kernel, we become another coordination framework. Agents already ignore those."* — Rune
