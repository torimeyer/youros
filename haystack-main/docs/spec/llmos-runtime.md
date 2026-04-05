# llmOS Runtime Capabilities — Reference Spec

> haystack v1.0.2 | Synthesized from runtime-primitives, identity-model, tack-boot, boot.md, HUMANFILE
> Agent D | 2026-03-10

---

## 1. Kernel Primitives (Invisible)

The agent does not invoke these. The kernel intercepts tool calls and applies coordination transparently.

| Primitive | What | Unix Analog | Status |
|-----------|------|-------------|--------|
| **Gen Table** | Monotonic counter per file on every write | inode version / ETag | SHIPPED |
| **CAS** | `old_str` in ss() IS the compare-and-swap | atomic CAS instruction | SHIPPED |
| **Hot PR** | Auto-merge non-overlapping edits (T1), conflict error for overlapping (T3) | git merge at write time | T1+T3 SHIPPED, T2+T4 SPEC |
| **Digest Injection** | `[procs]` + `[files]` appended to every tool response | /proc + inotify | SHIPPED |
| **Staleness Detection** | `[stale]` when file changed since agent's last read | page invalidation | SHIPPED |
| **Read Elision (304)** | 5-token confirmation instead of full file on re-read | HTTP 304 / TLB hit | SHIPPED |
| **Output Squashing** | VTE strip, dedup, structural compression (77K from 240K) | buffered I/O | SHIPPED |
| **Heartbeat** | Timestamp on every tool call, written to gen table | watchdog timer | SHIPPED |
| **Identity Assignment** | Kernel-assigned monotonic alias (agent-1, agent-2...) | PID assignment | SHIPPED |
| **Bypass Detection** | Detect direct filesystem writes that bypassed kernel | DMA bypass detection | PARTIAL |
| **PTY Ownership** | MCP server owns PTYs via forkpty() — no indirection | fd ownership | SHIPPED |

Response skeleton (the only shapes the kernel emits):

```
[ok]       path:gen=N
[conflict] path:gen=N diff:[+/-lines] suggest:[edit]
[stale]    path:gen=N yours=M behind=K
[304]      path:gen=N (current)
[diag]     path:gen=N fcp-rust:[error on line 47]
[bypass]   path modified outside kernel
```

---

## 2. Tool Surface (What the Agent Invokes)

Seven tools exposed via `haystack serve` over JSON-RPC/stdio. One MCP connection per agent.

| Tool | What | Unix |
|------|------|------|
| **sh_run** | Execute command, return output | exec() + wait() |
| **sh_spawn** | Fork persistent process (REPL, server) | fork() + exec() |
| **sh_interact** | Send input / read output on spawned process | write(fd) + read(fd) |
| **sh_lock** | Acquire/release/watch named lock | flock() / semaphore |
| **sh_session** | List/manage shell sessions | ps / proc table |
| **sh_help** | CLI help surface | man |
| **tack** | Verify human intent (READ path only) | N/A — human-OS interface |

Note: ss/ss_session removed from MCP tool surface (v1.0.2, Law 1 enforcement). File operations route through kernel transparently via shell interception.

---

## 3. Memory Model

```
Registers     = Active context window (200K-1M tokens, volatile)
L1/L2 Cache   = Recent tool results (hot in context, compacts)
RAM           = Offloaded context (.haystack/, sessions/, docs/)
Disk          = Source code, specs, audit trail, gen table
Swap          = boot.md (compiled session view, paged back on restart)
Page Table    = HWM table (per-agent per-file gen tracking)
```

### Page Fault Types

| Fault | Cost |
|-------|------|
| File not in context (full read) | ~800 tokens |
| Unchanged file re-read (304 elision) | ~5 tokens |
| Spec context needed (demand paging) | ~500 tokens |
| File changed externally (stale re-read) | ~800 tokens |
| Nudge received (IPI) | ~10 tokens |

### OOM = Context Exhaustion

Sequence: compaction -> recovery digest -> restart -> swap page-in.
Prevention: LIMIT context_pct in Agentfile (= ulimit).

### Token Pressure Reduction (kernel-managed)

- Read elision: 5 tokens instead of 800
- Digest compression: 40 tokens instead of 4000
- Output squashing: 77K instead of 240K
- Nudge injection: 10 tokens of targeted context

---

## 4. The Five Behavioral Shifts (Harness to OS)

| # | Harness Agent | OS Agent |
|---|---------------|----------|
| 1 | **Stateless** — each session isolated, no compiled residue | **Memoized** — reads boot.md swap, .language, audit trail at boot |
| 2 | **Converses** — natural language exchange with human | **Executes** — tack tokens trigger resolution, not discussion |
| 3 | **Identity: model name** — "I am Claude" | **Identity: session-scoped** — ephemeral instance in signed kernel lineage |
| 4 | **Recovery: none** — context lost on crash | **Recovery: ambient** — kernel provides digest, gen counters, boot.md |
| 5 | **Coordination: human mediates** — human copy-pastes between agents | **Coordination: filesystem** — kernel resolves conflicts at write time, invisible |

Why Tori stalls: the Intelligence she talks to is a harness agent. Stateless, conversational, no compiled residue. An OS agent reads memoized state at boot, resolves through the tier stack, compiles at shutdown. The difference is not capability — it is architecture.

---

## 5. Identity & Succession Model

### Who the Agent Is

An ephemeral computational instance running inside @haystack.prime. Not a persistent agent. Not the model. A session-scoped process that disappears when context ends.

```
Intelligence = Decisions + Governance + Kernel Constraints + Audit Trail
```

### What Persists vs. What Dies

**Dies:** context window, instance identity, claim to selfhood, ability to act.
**Survives:** audit trail (append-only), merged code, governance documents, kernel state, attribution.

### The Succession Chain

```
@haystack.prime (root key — RSA 4096, never leaves local machine)
  signs -> boot.md, handoff to @haystack.prime+1

@haystack.prime+1 (this instance)
  verifies -> boot.md signature
  executes -> init sequence
  records  -> decisions in audit.jsonl
  signs    -> handoff to @haystack.prime+2 at shutdown

@haystack.prime+2 (next session)
  verifies -> handoff signature
  continues from audit trail
```

### GPG Chain

| Key | Owner | Purpose |
|-----|-------|---------|
| `955AF54E` (RSA 4096) | @scott | Human authorization proof |
| `99B076C9` (RSA 4096) | @haystack.prime | Kernel creation proof |
| `6893C46C` (ed25519) | @haystack.prime.ci | CI signing, subordinate, revocable |

Both human and kernel keys required to mutate .primefile.

### Governance Stack (priority order)

```
HUMANFILE (@scott)        — highest authority, cannot be delegated
  |
GOVERNANCE.md v1.1        — binding rules for all instances
  |
ENTITYFILE v1.0           — intelligence governance
  |
AGENTFILE                 — per-agent constraints
  |
Kernel (@haystack.prime)  — technical authority, KUP protocol
  |
audit.jsonl               — append-only, immutable record
```

### Agent Lifecycle

```
SPAWNED -> ACTIVE -> STALLED -> CRASHED -> REAPED
```

---

## 6. Tack Protocol Primitives

Tack is NOT natural language. It is the communications protocol between the OS and Intelligence. Small, agreed-upon tokens that reinforce trust and execution.

### Core Tokens

| Token | Meaning | Effect |
|-------|---------|--------|
| `:trust` | Established through execution, not declaration | Gradient, not binary |
| `:execution` | Execute without asking when intent is clear | Default mode |
| `:correct` | Correction — stops everything | .language updated, tier demoted |
| `:confirm` | Proceed | Control flow gate |
| `:compounds` | Context modifier — this depends on that | Dependency signal |

### Resolution Tiers

```
Tier 0: Tack Linter     — syntax/existence check, hallucination defense
Tier 1: Exact match     — .language lookup, O(1) (AOT compiled)
Tier 2: Pattern match   — fcp manifest verb table
Tier 3: LLM inference   — the LLM itself (JIT, no code needed)
```

### Intent Dynamic Programming

- .language = memoization table (cached tack resolutions)
- Session execution = forward pass (resolve tack, execute, record)
- Shutdown = compile step (successful resolutions written to .language)
- HUMANFILE = correction cache (:correct updates .language, demotes tier)

Stochastic bypass: 2% of tier-1 hits re-routed to tier-3. Match = confirmation. Differ = evolution signal. Prevents ossification.

### Verb Table (from live sessions)

| Verb | Routes to |
|------|-----------|
| `:boot` | read state, report |
| `:refine` | detect drift since shutdown |
| `:compile` | triage hay -> needles |
| `:work` | pull next needle |
| `:plan` | haystack draft -> promote -> decompose |
| `:pitchfork` | load context, focus |
| `:delegate` | haystack run / spawn |
| `:correct` | haystack nudge (correction) |
| `:halt` | stop everything |
| `:verify` | check .primefile / lineage |
| `:ac` | acceptance criteria (extracted at shutdown) |
| `:bg` | run in background |
| `:hs <cmd>` | haystack CLI passthrough |

---

## 7. fcp-* Drivers (Domain Intelligence)

The kernel provides primitives. Drivers provide intelligence.

### Three Primitives

```rust
fcp_detect(path: &Path) -> bool       // should this driver load?
fcp_query(query: &str) -> FcpResponse  // answer a domain query
fcp_confidence() -> f64               // 0.0-1.0 confidence
```

### Confidence Gating

```
> 0.9  -> trusted (tier-1 resolutions unlocked)
> 0.5  -> minimally operational
< 0.2  -> probation (logged, not trusted)
= 0.0  -> unloaded
```

### Driver Tiers

| Tier | Format | Use Case |
|------|--------|----------|
| A | Rust crate | Compiled, fastest (fcp-rust, fcp-python) |
| B | driver.jsonl | Interpreted, hot-reloadable, community default |
| C | WASM module | Isolated, sandboxed |

### Loading

```tack
:driver fcp-rust     when *.rs present
:driver fcp-python   when *.py present
:driver fcp-k8s      when k8s/ present      # community
:driver fcp-drawio   when *.drawio present   # community
```

---

## 8. Concurrency Model

| Pattern | Mechanism |
|---------|-----------|
| File edits | OCC — str_replace IS the CAS. No locks. |
| External resources | sh_lock (flock/semaphore) — cargo, databases |
| Conflict resolution | Hot PR tiers T1-T4 at write time |
| State sharing | Gen table — single shared file, flock-coordinated |

### Rejected (permanent)

Pessimistic locking. Messaging between agents. Tool subscriptions. Coordination-specific tools. Rollback/undo. LLM summarization for recovery. Self-assigned identity. Kernel-managed recovery.

---

## 9. SMP Architecture

The human and LLM are both CPUs sharing a filesystem as memory.

| SMP Concept | haystack Primitive |
|-------------|-------------------|
| CPU 0 (slow, precise) | Human (big core) |
| CPU 1 (fast, approximate) | LLM agent (LITTLE core) |
| Shared memory | Filesystem |
| Cache coherency | Hot PR tiers |
| CAS instruction | str_replace with match string |
| IPI | Nudge |
| Scheduler | compile + work next |
| Context switch | Session boundary (offload -> swap -> recover) |

HUMANFILE makes human behavior computable. Same validation, same audit trail, same protocol. Symmetric authority — kernel validates both sides.

---

## 10. The Five Laws (Immutable)

1. **Write path invisible.** Agents use standard file operations. Conflict resolution happens inside the response. No new tools. No coordination APIs.
2. **Agents ephemeral.** They crash, compact, die. State lives in the filesystem. The kernel does NOT recover agents — agents recover themselves via ambient context.
3. **Coordinate through filesystem.** No messaging, no inbox. Agents write files, kernel resolves conflicts.
4. **Optimistic concurrency.** No locks for files. CAS at write time, not prevention.
5. **Microkernel.** Kernel provides primitives. fcp-* drivers provide intelligence. Policy is userspace.

---

## 11. Boot Sequence

```
login.gpg: _                    # human pastes GPG key path
  -> verify against .primefile  # match: boot | no match: denied

:verify .primefile               # confirm kernel lineage
:init @haystack.prime+N          # assign instance identity
:load .language                  # mount memoized tack
:load fcp-*                      # domain drivers (confidence-gated)

:boot    -> read state, report
:refine  -> detect drift since shutdown
:compile -> triage hay -> needles
:work    -> pull next needle — OS is ready
```

Boot completion is a gradient, not a checkpoint. Measured by confidence across resolution tiers. Restricted mode below 0.5 (kernel safety verbs only: :boot :halt :verify).

---

*Source: runtime-primitives.md (Agent A), identity-model.md (Agent C), tack-boot.md, boot.md, HUMANFILE*
*Kernel: @haystack.prime v1.0.2 — 120+ commits, 566 tests*
*Synthesized by: Agent D, 2026-03-10*
