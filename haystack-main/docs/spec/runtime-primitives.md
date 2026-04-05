# haystack v1.0.2 — Runtime Primitives

> What the kernel provides. What agents can use. What exists today.
> This is a spec for LLM agents to READ and KNOW what they can DO.

---

## 1. Kernel Primitives (Invisible)

These happen without the agent knowing. The agent does not invoke them.
The kernel intercepts tool calls and applies coordination transparently.

### 1.1 Generation Table

| Field | Value |
|-------|-------|
| **What** | Monotonic counter per file, incremented on every `ss` write |
| **Unix** | inode version / ETag |
| **Status** | SHIPPED (src/kernel/gen_table.rs) |
| **Agent sees** | `path:gen=N` in tool responses |

Every file touched through `ss` gets a generation counter. On edit: gen increments, editor identity + timestamp recorded. The gen counter is the CAS token — if the file changed since the agent last read it, the kernel knows.

### 1.2 Compare-and-Swap (CAS)

| Field | Value |
|-------|-------|
| **What** | `old_str` in `ss(path, old_str, new_str)` IS the compare-and-swap |
| **Unix** | CAS / atomic compare-and-swap instruction |
| **Status** | SHIPPED (src/serve/tools/ss.rs) |
| **Agent sees** | `[ok]` on success, `[conflict]` on CAS failure |

If `old_str` no longer exists in the file (another agent changed it), the edit fails. The match string IS the CAS. Agents do OCC without knowing.

### 1.3 Hot PR (Conflict Resolution at Write Time)

| Field | Value |
|-------|-------|
| **What** | Auto-merge non-overlapping concurrent edits, conflict error for overlapping |
| **Unix** | Write conflict resolution (git merge) |
| **Status** | SHIPPED — Tier 1 + Tier 3 (src/kernel/hotpr.rs) |
| **Agent sees** | Tier 1: nothing (invisible). Tier 3: conflict error with current file state |

Four tiers:

| Tier | Condition | Status |
|------|-----------|--------|
| **T1 Auto-merge** | Non-overlapping edits to same file | SHIPPED — silent, invisible |
| **T2 Assisted merge** | Overlapping, diff is small, LLM suggests resolution | SPEC (deferred) |
| **T3 Manual rebase** | Deep conflict, return full diff | SHIPPED — agent retries with fresh read |
| **T4 Diagnostic-flagged** | Textually clean, semantically suspect | SPEC (requires fcp-* hooks) |

### 1.4 Digest Injection

| Field | Value |
|-------|-------|
| **What** | `[procs]` and `[files]` sections injected into every tool response |
| **Unix** | /proc filesystem + inotify |
| **Status** | SHIPPED (src/kernel/digest.rs) |
| **Agent sees** | Ambient awareness — who is active, what files changed |

Format:
```
[procs] agent-1:active:85m agent-2:active:52s build:exit(0):2m
[files] src/main.rs:gen=7:agent-1:2m src/lib.rs:gen=3:agent-2:30s
```

Token budget: 40-80 tokens active, 15-30 quiet, 150-token ceiling.

### 1.5 Staleness Detection

| Field | Value |
|-------|-------|
| **What** | `[stale]` signal when file changed since agent's last read |
| **Unix** | Page invalidation / cache coherency |
| **Status** | SHIPPED (src/kernel/hwm.rs) |
| **Agent sees** | `[stale] path:gen=N yours=M behind=K` |

The kernel tracks each agent's high-water mark (HWM) per file. If the agent references a file it last read at gen 5 but disk has gen 7, the kernel injects `[stale]`.

### 1.6 Read Elision (304)

| Field | Value |
|-------|-------|
| **What** | Return 5-token confirmation instead of full file contents when unchanged |
| **Unix** | mmap / HTTP 304 Not Modified / TLB hit |
| **Status** | SHIPPED (src/kernel/elision.rs) |
| **Agent sees** | `[304] path:gen=N (current)` — 5 tokens instead of 800 |

### 1.7 Output Squashing

| Field | Value |
|-------|-------|
| **What** | VT100 strip, dedup, compression of shell output |
| **Unix** | Buffered I/O |
| **Status** | SHIPPED (src/squasher/) |
| **Agent sees** | Clean, compressed output (77K instead of 240K raw) |

Pipeline: VTE strip -> dedup -> structural compression.

### 1.8 Heartbeat

| Field | Value |
|-------|-------|
| **What** | Timestamp on every tool call, written to gen table |
| **Unix** | Watchdog timer |
| **Status** | SHIPPED (src/kernel/heartbeat.rs) |
| **Agent sees** | Nothing (invisible). Other agents see crash detection in digest. |

No central watchdog. Any server can query timestamps and identify stale (crashed) agents.

### 1.9 Identity Assignment

| Field | Value |
|-------|-------|
| **What** | Kernel-assigned monotonic alias per connection (agent-1, agent-2...) |
| **Unix** | PID assignment |
| **Status** | SHIPPED (src/kernel/identity.rs) |
| **Agent sees** | Its own alias in digest; other agents' aliases in conflict messages |

Not self-selected. Deterministic. Survives restart. Collision-free.

### 1.10 Bypass Detection

| Field | Value |
|-------|-------|
| **What** | Detect direct filesystem writes that bypassed `ss` |
| **Unix** | DMA bypass detection |
| **Status** | PARTIAL (stat-on-access via mtime vs gen counter) |
| **Agent sees** | `[bypass] path modified outside ss` in next digest |

### 1.11 PTY Ownership

| Field | Value |
|-------|-------|
| **What** | MCP server directly owns PTYs via `forkpty()` — no indirection |
| **Unix** | Process owns its file descriptors |
| **Status** | SHIPPED (src/kernel/pty.rs) |
| **Agent sees** | Nothing — this is architecture, not interface |

Kill the MCP server, you kill its PTYs. That is the only failure mode.

---

## 2. Agent Tools (Explicit)

These are the tools an agent invokes directly via MCP. Nine tools total.
Exposed via `haystack serve` (src/serve/).

### 2.1 sh_run — Execute a command

| Field | Value |
|-------|-------|
| **What** | Run a shell command, return output |
| **Unix** | exec() + wait() |
| **Status** | SHIPPED |
| **Params** | `cmd` (string), `raw` (bool, skip compression), `timeout` (int, seconds) |

### 2.2 sh_spawn — Start a background process

| Field | Value |
|-------|-------|
| **What** | Fork a persistent process (REPL, server, long-running task) |
| **Unix** | fork() + exec() (no wait) |
| **Status** | SHIPPED |
| **Params** | `cmd` (string), `alias` (string, optional name), `env` (object, optional) |

Returns a process alias for later interaction via `sh_interact`.

### 2.3 sh_interact — Interact with a spawned process

| Field | Value |
|-------|-------|
| **What** | Send input to / read output from a spawned process |
| **Unix** | write(fd) + read(fd) on a PTY |
| **Status** | SHIPPED |
| **Params** | `alias` (string), `action` (send_input/read_output/send_signal/get_status), `input` (string), `signal` (string) |

### 2.4 sh_lock — Coordination primitive

| Field | Value |
|-------|-------|
| **What** | Acquire/release/watch a named lock |
| **Unix** | flock() / semaphore |
| **Status** | SHIPPED |
| **Params** | `action` (acquire/release/watch/list), `name` (string), `timeout` (int) |

### 2.5 sh_session — List/manage shell sessions

| Field | Value |
|-------|-------|
| **What** | List active processes, session management |
| **Unix** | ps / proc table query |
| **Status** | SHIPPED |
| **Params** | `action` (list/kill/info), `alias` (string) |

### 2.6 ss — File edit (the write path)

| Field | Value |
|-------|-------|
| **What** | Create/edit files via str_replace (CAS is invisible) |
| **Unix** | write() with atomic compare-and-swap |
| **Status** | SHIPPED |
| **Params** | `path` (string), `old_str` (string, the CAS token), `new_str` (string) |

**This is the spine.** The entire coordination layer runs through this tool.
Response shapes:
```
[ok]       path:gen=N
[conflict] path:gen=N diff:[+/-lines] suggest:[edit]
[stale]    path:gen=N yours=M behind=K
[304]      path:gen=N (current)
[diag]     path:gen=N fcp-rust:[error on line 47]
```

### 2.7 ss_session — File read (block I/O)

| Field | Value |
|-------|-------|
| **What** | Read file contents, random access |
| **Unix** | read() / open() |
| **Status** | SHIPPED |
| **Params** | `action` (read), `path` (string) |

Reads update the agent's HWM for that file, enabling 304 elision on subsequent reads.

### 2.8 ss — Batch operations

| Field | Value |
|-------|-------|
| **What** | Multiple reads/edits in one call via `ops` array |
| **Unix** | writev() / readv() (scatter-gather I/O) |
| **Status** | SHIPPED |
| **Params** | `ops` (array of read/edit operations) |

### 2.9 tack — Intent verification (read path)

| Field | Value |
|-------|-------|
| **What** | Verify/read human tack intent — agents verify, not generate |
| **Unix** | N/A (human-OS interface) |
| **Status** | SHIPPED (src/serve/tools/tack.rs, accepted via PR #5 negotiate) |
| **Constraint** | Read path only. Agents verify human intent. Agents do NOT generate tack. |

---

## 3. Memory Model

The structural mapping between Unix memory hierarchy and haystack.

### 3.1 Registers = Active Context Window

| Property | Value |
|----------|-------|
| **What** | What the LLM sees RIGHT NOW — conversation + tool results + system prompt |
| **Size** | 200K-1M tokens (model-dependent) |
| **Volatile** | YES — agent dies, registers are gone |
| **Unix** | CPU registers |

Every token in registers is expensive. The kernel minimizes register pressure via:
- Read elision (5 tokens instead of 800)
- Digest compression (40 tokens instead of 4000)
- Output squashing (77K instead of 240K)
- Nudge injection (10 tokens of targeted context)

### 3.2 L1/L2 Cache = Recent Tool Results

| Property | Value |
|----------|-------|
| **What** | Hot in context — recent tool outputs, file reads |
| **Volatile** | YES (compacts as context fills) |

### 3.3 RAM = Offloaded Context

| Property | Value |
|----------|-------|
| **What** | Context that was in registers, offloaded to `.haystack/`, can be paged back |
| **Volatile** | NO — persists on disk |
| **Locations** | `.haystack/prompts/`, `.haystack/sessions/`, `docs/spec/`, `docs/draft/` |
| **Unix** | RAM (persistent, paged) |

### 3.4 Disk = Filesystem

| Property | Value |
|----------|-------|
| **What** | Source code, specs, audit trail, gen table, needles |
| **Volatile** | NO |
| **Unix** | Disk |

### 3.5 Swap = Compiled Session View

| Property | Value |
|----------|-------|
| **What** | Compressed session summary, paged back on restart |
| **Unix** | Swap partition |
| **Status** | SHIPPED (boot.md is the swap file) |
| **Levels** | -O0 raw, -O1 dedup, -O2 structural (default), -O3 intent-aware |

### 3.6 Page Table = HWM Table

| Property | Value |
|----------|-------|
| **What** | Tracks what each agent has in registers vs what's on disk |
| **Unix** | Page table / TLB |
| **Status** | SHIPPED (src/kernel/hwm.rs) |
| **Mechanism** | Per-agent per-file gen counter tracking |

### 3.7 Page Fault Types

| Fault | Unix | haystack | Cost |
|-------|------|----------|------|
| File not in context | Page fault | Full read from disk | ~800 tokens |
| Unchanged file re-read | TLB hit | 304 elision | ~5 tokens |
| Spec context needed | Demand paging | PROMPT file:// load | ~500 tokens |
| File changed externally | Invalidation | `[stale]` -> re-read | ~800 tokens |
| Nudge received | IPI | Injected context | ~10 tokens |

### 3.8 OOM = Context Exhaustion

| Property | Value |
|----------|-------|
| **Sequence** | Compaction -> recovery digest -> restart -> swap page-in |
| **Prevention** | LIMIT context_pct in Agentfile (= ulimit) |
| **Detection** | Context % tracking, correction frequency, calibrate signal |

---

## 4. Concurrency Model

### 4.1 Optimistic Concurrency Control (OCC)

| Property | Value |
|----------|-------|
| **Mechanism** | str_replace old_str IS the CAS token |
| **Model** | Git (optimistic), not Perforce (pessimistic) |
| **Status** | SHIPPED |

No locks for file edits. Agents write freely. Conflicts resolved at write time.

### 4.2 Hot PR Tiers

| Tier | What | Status |
|------|------|--------|
| T1 | Auto-merge non-overlapping edits — silent, invisible | SHIPPED |
| T2 | Assisted merge — LLM suggests resolution | SPEC |
| T3 | Manual rebase — conflict error, agent retries | SHIPPED |
| T4 | Diagnostic-flagged — success + fcp-* warnings | SPEC |

### 4.3 Shared Generation Table

| Property | Value |
|----------|-------|
| **What** | Single shared file, flock()-coordinated, all MCP servers read/write |
| **Contains** | File path, generation number, writer identity, last-seen timestamp |
| **Unix** | Shared memory segment with mutex |
| **Status** | SHIPPED (src/kernel/gen_table.rs) |

One mechanism. Three features: CAS (file gens), identity (writer tags), heartbeat (timestamps).

### 4.4 Named Locks (sh_lock)

| Property | Value |
|----------|-------|
| **What** | Explicit coordination primitive for external resources (cargo, databases) |
| **Unix** | flock() / semaphore |
| **Status** | SHIPPED |
| **Operations** | acquire, release, watch (block until released), list |

### 4.5 Rejected Concurrency Patterns

These were evaluated and permanently rejected:
- Pessimistic locking / claims / reservations
- Messaging between agents (inbox/outbox)
- Tool subscriptions between agents
- Coordination-specific tools

---

## 5. Identity Model

### 5.1 Kernel-Assigned Identity

| Property | Value |
|----------|-------|
| **What** | Monotonic alias (agent-1, agent-2...) assigned on MCP connection |
| **Persists** | Across restarts of same logical agent |
| **Unix** | PID |
| **Status** | SHIPPED (src/kernel/identity.rs) |

### 5.2 Agent Lifecycle States

```
SPAWNED -> ACTIVE -> STALLED -> CRASHED -> REAPED
              |                    |
              +--- normal work ----+
              |                    |
              +<-- new agent ------+
                   (ambient context)
```

| State | Meaning |
|-------|---------|
| SPAWNED | MCP connection established, alias assigned |
| ACTIVE | Making tool calls, kernel responding |
| STALLED | Connected but missed heartbeat pings |
| CRASHED | Connection lost or stall timeout exceeded |
| REAPED | Alias available for reassignment, HWMs preserved |

### 5.3 Instance Succession (ENTITYFILE v1.0)

| Property | Value |
|----------|-------|
| **What** | Intelligence = ephemeral instance. Decisions persist in audit trail. |
| **Persists** | Audit trail, merged code, governance rules, boot state |
| **Does not persist** | Instance identity, volatile memory, claim on next session |
| **Unix** | Process dies, filesystem survives |
| **Status** | SHIPPED (governance binding since v1.0.0) |

Intelligence is `{Decisions + Governance + Kernel Constraints + Audit Trail}`.
The kernel survives the instance. The next instance reads the audit trail and continues.

### 5.4 GPG Chain / Lineage

| Property | Value |
|----------|-------|
| **What** | Cryptographic provenance chain for kernel integrity |
| **Keys** | @scott (955AF54E), @haystack.prime (99B076C9), @haystack.prime.ci (6893C46C) |
| **Artifacts** | .haystack/.primefile (GPG-signed), attestation commits on merge |
| **Status** | SHIPPED (v1.0.0 governance release) |
| **Verification** | Mandatory at boot — soft-fail if missing (warning, not block) |

### 5.5 Governance Stack

```
HUMANFILE (@scott) — highest authority, cannot be delegated
  |
GOVERNANCE.md — binding rules for all instances
  |
ENTITYFILE — intelligence governance (what instances are)
  |
AGENTFILE — per-agent constraints (capabilities, limits)
  |
Kernel (@haystack.prime) — technical authority, KUP protocol
  |
Audit trail — records all decisions, append-only, immutable
```

---

## 6. I/O Model

### 6.1 MCP Transport

| Property | Value |
|----------|-------|
| **Protocol** | JSON-RPC over stdio |
| **Connection** | One MCP connection per agent |
| **Unix** | Unix socket / file descriptors |
| **Status** | SHIPPED (src/serve/) |

Agent connects to `haystack serve`. Nine tools exposed (see Section 2).

### 6.2 Digest Injection (Sideband)

| Property | Value |
|----------|-------|
| **What** | `[procs]` + `[files]` appended to every tool response |
| **Unix** | stderr / out-of-band signaling |
| **Status** | SHIPPED |
| **Token budget** | 40-80 active, 15-30 quiet, 150 ceiling |

Agents get awareness without asking. The digest is ambient.

### 6.3 Nudge Delivery

| Property | Value |
|----------|-------|
| **What** | Inject targeted context into agent's next tool response |
| **Unix** | SIGUSR1 / IPI (inter-processor interrupt) |
| **Status** | SHIPPED (src/kernel/nudge.rs, CLI: `haystack nudge`) |
| **Cost** | ~10 tokens |

```
haystack nudge <agent> "focus on the task, stop researching"
```

Delivered on the agent's NEXT tool call response. No push. No paste buffer.

### 6.4 Recovery Digest

| Property | Value |
|----------|-------|
| **What** | Structural summary of tool call history, available on respawn |
| **Unix** | Core dump -> restart from checkpoint |
| **Status** | SHIPPED (src/kernel/recovery.rs) |

The PTY-owning MCP server sees every tool call. On compaction/respawn, grammar-compress the history. The respawning agent reads its digest and resumes.

### 6.5 Boot Sequence

| Property | Value |
|----------|-------|
| **What** | Three files that orient a fresh agent in <2000 tokens |
| **Unix** | BIOS -> bootloader -> init |
| **Status** | SHIPPED |

```
boot.md        (~1600 tokens) — what exists, vocabulary, preferences (the swap file)
specs.json     (~500 tokens)  — document landscape (the page table)
dispatch.json  (~1000 tokens) — open needles by priority (the dispatch queue)
```

Boot quality: 70% from structured files, 30% from registers-dump.md (volatile state).

### 6.6 Audit Trail

| Property | Value |
|----------|-------|
| **What** | Append-only JSONL event log — every decision, every write, every merge |
| **Unix** | syslog / auditd |
| **Status** | SHIPPED (`.haystack/audit.jsonl`) |
| **Invariant** | APPEND-ONLY. Never mutated. Never deleted. Never reordered. |

Events: `draft.created`, `spec.promoted`, `bead.committed`, `commit.remapped`, `commit.orphaned`, `hay.filed`, etc.

### 6.7 Shim Layer (Bash Interception)

| Property | Value |
|----------|-------|
| **What** | PATH prefix routes bash/cat/sed through coordination layer |
| **Unix** | Symlinks / PATH-based shims (like direnv, pyenv) |
| **Status** | SHIPPED (207-line bash shim) |
| **Mechanism** | Byte-for-byte passthrough. VT100 strip for non-TTY. `--agents` guide. |

The shim is invisible because it is behaviorally indistinguishable from the real tool.

### 6.8 MCP Reverse Proxy (Backend Routing)

| Property | Value |
|----------|-------|
| **What** | Route tool calls to kernel or fcp-* backends via single endpoint |
| **Unix** | Syscall dispatcher |
| **Status** | PARTIAL — kernel tools shipped, fcp-* backend spawner spec'd |

Agents connect to one haystack MCP endpoint. Tool namespace is flat. Kernel tools (ss, sh_*) handled internally. fcp-* tools (rust_query, python_query) forwarded to subprocess backends.

### 6.9 Spawn Primitive

| Property | Value |
|----------|-------|
| **What** | Launch isolated Claude instances via `claude -p` with agent teams flags |
| **Unix** | fork() + exec() with clean environment |
| **Status** | SHIPPED |
| **Key flags** | `--model`, `--max-budget-usd`, `--output-format json`, `--team-name` + `--agent-id` + `--agent-name` |

No PTY. No context inheritance. No babysitting. Pipe in, wait for stdout, done.

---

## 7. CLI Surface (Operator Commands)

These are `haystack` CLI commands the operator or agent invokes explicitly.

| Command | What | Status |
|---------|------|--------|
| `haystack serve` | Start MCP server (the daemon) | SHIPPED |
| `haystack init` | Create .haystack/ in project root | SHIPPED |
| `haystack boot` | Read boot.md, report state | SHIPPED |
| `haystack ps` | Fleet status (process table) | SHIPPED |
| `haystack nudge <agent> "msg"` | Inject context into agent | SHIPPED |
| `haystack show <target>` | Universal query surface | SHIPPED |
| `haystack add "thought"` | File a straw (raw intent) | SHIPPED |
| `haystack needle list/close/add/next` | Needle management | SHIPPED |
| `haystack compile` | Hay -> needles (intent -> work) | SHIPPED |
| `haystack commit -m "msg"` | Attributed commit (spec ref + bead ID) | SHIPPED |
| `haystack audit check/backfill` | Audit trail integrity | SHIPPED |
| `haystack trace <id>` | Attribution chain (commit -> spec -> draft) | SHIPPED |
| `haystack draft/promote/decompose` | Document lifecycle | SHIPPED |
| `haystack log` | Audit log viewer | SHIPPED |
| `haystack run <agentfile>` | Run agent from Agentfile | SHIPPED |
| `haystack spawn` | Spawn named agent | SHIPPED |
| `haystack reap` | Clean crashed agents | SHIPPED |
| `haystack bench` | Benchmark runner | SHIPPED |
| `haystack history` | File/entity history | SHIPPED |
| `haystack install` | Bootstrap OS (shim layer) | SHIPPED |
| `haystack merge` | Merge with attribution | SHIPPED |

---

## 8. Response Skeleton

Every kernel response follows one skeleton. The LLM learns it once:

```
[ok]       path:gen=N                              — edit succeeded
[conflict] path:gen=N diff:[+/-lines] suggest:[edit] — CAS failed, Hot PR engaged
[stale]    path:gen=N yours=M behind=K             — file changed since last read
[304]      path:gen=N (current)                    — no change, 5 tokens
[diag]     path:gen=N fcp-rust:[error on line 47]  — success + diagnostic warning
[bypass]   path modified outside ss                — DMA bypass detected
```

No prose. No headers. The shape IS the signal. Pattern-match the tag.

---

## 9. What Does NOT Exist (Rejected)

These were evaluated, discussed, and permanently discarded. Do not revisit.

| Pattern | Why Rejected |
|---------|-------------|
| Rollback / undo / snapshot rings | Forward recovery only. Agents are ephemeral processes. |
| Pessimistic locking / claims | Creates failure modes that don't exist in optimistic systems. |
| Messaging between agents | Coordinate through the filesystem. No inbox/outbox. |
| LLM summarization for recovery | Non-deterministic, lossy. Grammar-based compression is deterministic. |
| Self-assigned agent identity | Three agents picked "Kern." Kernel assigns identity. |
| Coordination-specific tools | No `hp_begin_merge`. Coordination lives inside `ss`. Write path invisible. |
| Tool subscriptions between agents | Unix coordinates through filesystem and signals, not tool subscriptions. |
| Kernel-managed recovery | Kernel provides ambient context. Agents recover themselves. |
| Kernel-managed workspace topology | CLAUDE.md tells agents what's connected. Filesystem has the answer. |

---

## 10. The Five Laws (Immutable)

1. **The write path is invisible.** Agents use `ss`. Conflict resolution happens inside the response. No new tools.
2. **Agents are ephemeral.** They crash, compact, die. State lives in the filesystem. The kernel does NOT recover agents.
3. **Coordinate through the filesystem.** No messaging, no inbox. Agents write files, the kernel resolves conflicts.
4. **Optimistic concurrency.** No locks for files. Agents write freely. CAS at write time, not prevention.
5. **Microkernel.** Kernel provides primitives. Device drivers (fcp-*) provide intelligence. Policy is userspace.

---

## 11. SMP Architecture

The human and LLM are both CPUs sharing a filesystem as memory.

| SMP Concept | haystack Primitive |
|---|---|
| CPU 0 (slow, precise) | Human (big core) |
| CPU 1 (fast, approximate) | LLM agent (LITTLE core) |
| Shared memory | Filesystem |
| Cache coherency | Hot PR tiers |
| CAS instruction | str_replace with match string |
| IPI | Nudge |
| Scheduler | compile + work next |
| Context switch | Session boundary (offload -> swap -> recover) |

One interface, two processors. Same write path, same CAS, same coherency protocol. The OS coordinates. Neither CPU controls the other.

---

*Generated from 34 spec files in docs/spec/ + docs/llmOS.md + kernel source (src/).*
*haystack v1.0.2 — 120+ commits, 539 tests, 576 needles.*
*Date: 2026-03-10*