---
status: spec
version: 1
author: orchestrator
created: 2026-03-08
evidence: llmOS.md, mcp-unix-mapping.md, llmos-memory-model.md, ostk-compile.md, this session
aligns_with: [kernel-architect.md, llmos-memory-model.md, ostk-compile.md]
implements: []
---

# Unix to ostk — 1:1 Mapping

> Not metaphors. Structural equivalents. Each Unix primitive has exactly one ostk primitive.

## Process Management

| Unix | ostk | Spec |
|------|----------|------|
| fork() / exec() | `ostk run agent.af` | ostk-compile.md |
| PID | Agent alias (→ agent-1) | haystack-mvp.md |
| Process table | `.ostk/agents.jsonl` | haystack-mvp.md |
| ps | `ostk console` fleet view | haystack-console.md |
| kill -TERM | `ostk drain` (snapshot WIP first) | agent-lifecycle.md |
| kill -9 | `ostk drain --force` | agent-lifecycle.md |
| Signal (SIGUSR1) | `ostk nudge` (inject into next tool response) | haystack-mvp.md |
| IPI (inter-processor interrupt) | `[nudge]` annotation on tool response | ostk-compile.md |
| Scheduler | Orchestrator + WORK directive in Agentfile | pull-model.md |
| Process priority | Model tier (haiku=low, sonnet=normal, opus=high) | agent-lifecycle.md |
| Process state (R/S/Z) | Agent state (active/stale/crashed/reaped) | haystack-mvp.md |
| Heartbeat / watchdog | Timestamp on every tool call | haystack-mvp.md |
| init / PID 1 | Orchestrator (first agent, manages fleet) | pull-model.md |
| cron | WORK directive with continuous pull | pull-model.md |
| daemon | `ostk serve` (background MCP server) | haystack-mvp.md |

## Memory

| Unix | ostk | Spec |
|------|----------|------|
| CPU registers | Active context window (what LLM sees NOW) | llmos-memory-model.md |
| L1/L2 cache | Recent tool results (hot in context) | llmos-memory-model.md |
| RAM | Offloaded context (.ostk/prompts/, sessions/) | llmos-memory-model.md |
| Disk | Filesystem (source code, specs, audit trail) | llmos-memory-model.md |
| Swap | Compiled session view (`ostk compile --swap -O2`) | llmos-memory-model.md |
| mmap | Read elision / [304] (reference without loading) | post-mvp-tier-a.md |
| Page table | HWM table (tracks what agent has in registers) | haystack-mvp.md |
| Page fault | [stale] signal (agent's view outdated, reload) | haystack-mvp.md |
| TLB hit | [304] response (5 tokens, no reload needed) | post-mvp-tier-a.md |
| Page invalidation | File modified, all agent HWMs invalidated | post-mvp-tier-a.md |
| OOM kill | Context exhaustion → compaction | llmos-memory-model.md |
| ulimit | LIMIT context_pct in Agentfile | ostk-compile.md |
| malloc / free | Token consumption / compaction | llmos-memory-model.md |
| DMA | Hot PR auto-merge (disk write, no register involvement) | post-mvp-tier-a.md |

## Filesystem

| Unix | ostk | Spec |
|------|----------|------|
| write() | `ss(path, old_str, new_str)` | haystack-mvp.md |
| write() conflict | Hot PR (3 tiers) | post-mvp-tier-a.md |
| read() | `ss_session("read path")` | sprint-5-launch-plan.md |
| open() / close() | Session lifecycle (no-op — gen_table tracks) | sprint-5-launch-plan.md |
| inode | Generation counter (monotonic per file) | haystack-mvp.md |
| stat() | `gen_table.read_gen(path)` | haystack-mvp.md |
| flock() | `.ostk/*.lock` files | haystack-mvp.md |
| O_APPEND | audit.jsonl append (multi-writer safe) | audit-hash-integrity.md |
| fsync() | `ss` flush (immediate in ostk) | sprint-5-launch-plan.md |
| inotify / kqueue | [files] digest (polling v1, subscriptions v2) | llmOS.md |
| chmod / permissions | TOOL directive in Agentfile (capability scoping) | ostk-compile.md |
| /dev/null | DMA bypass (raw cat, opts out of coordination) | installation-and-shim.md |
| symlink | Shim layer (bash → ostk) | installation-and-shim.md |

## Concurrency

| Unix | ostk | Spec |
|------|----------|------|
| Mutex / flock | flock() on .ostk/ JSONL files | haystack-mvp.md |
| Compare-and-swap | str_replace (old_str IS the CAS token) | haystack-mvp.md |
| Optimistic locking | Hot PR (write freely, resolve at write time) | post-mvp-tier-a.md |
| Pessimistic locking | REJECTED (no claims, no reservations) | llmOS.md |
| Semaphore | mish lock (coordination primitive) | haystack-mvp.md |
| Condition variable | mish lock watch (block until released) | haystack-mvp.md |
| Thread-local storage | Per-agent HWM, per-agent sessions | haystack-mvp.md |
| Shared memory | .ostk/ directory (all agents read/write) | haystack-mvp.md |

## Compilation

| Unix | ostk | Spec |
|------|----------|------|
| Source code | Hay (~ raw human intent) | ostk-compile.md |
| gcc / compiler | `ostk compile` (intelligence layer) | ostk-compile.md |
| Machine code | Needle (→ verb + file + test) | needle-spec.md |
| Optimization levels | -O0 through -O3 | ostk-compile.md |
| Linker | Dependency resolution in -O3 decomposition | ostk-compile.md |
| Object file | Individual needle (one compilation unit) | needle-spec.md |
| Executable | Agentfile (linked needles + runtime config) | ostk-compile.md |
| Preprocessor | Spell/grammar correction (-O1) | ostk-compile.md |

## I/O

| Unix | ostk | Spec |
|------|----------|------|
| stdin | Human input / hay | ostk-compile.md |
| stdout | Tool response (compressed by squasher) | installation-and-shim.md |
| stderr | Digest, nudges, stale signals (sideband) | haystack-mvp.md |
| Pipe | Agent A's output → Agent B's needle context | pull-model.md |
| /proc | `.ostk/agents.jsonl` (process state) | haystack-mvp.md |
| /dev | fcp-* (device drivers: rust-analyzer, pylsp) | llmOS.md |
| Device driver | fcp-* plugins (domain intelligence) | llmOS.md |
| Block device | `ss` (file I/O, random access) | sprint-5-launch-plan.md |
| Char device | PTY (streaming I/O, sh_run) | haystack-mvp.md |

## Networking

| Unix | ostk | Spec |
|------|----------|------|
| Socket | MCP connection (JSON-RPC over stdio) | haystack-mvp.md |
| DNS | Needle slug resolution (→ name → internal ID) | needle-spec.md |
| HTTP caching / ETag | Generation counter + [304] | post-mvp-tier-a.md |
| Load balancer | WORK directive (agents pull by capability) | pull-model.md |
| Firewall | TOOL directive (capability restriction) | ostk-compile.md |

## Audit / Security

| Unix | ostk | Spec |
|------|----------|------|
| syslog | `.ostk/audit.jsonl` (append-only event log) | audit-hash-integrity.md |
| auditd | `ostk audit check` (completeness verification) | sprint-5-launch-plan.md |
| who / last | `ostk trace` (attribution chain) | document-lifecycle.md |
| sudo | Human review gate in `ostk compile` | ostk-compile.md |
| SELinux / AppArmor | Workspace-level sandboxing (not per-agent) | installation-and-shim.md |

## User Interface

| Unix | ostk | Spec |
|------|----------|------|
| Terminal | `ostk console` | haystack-console.md |
| top / htop | Console active mode (fleet view) | haystack-console.md |
| Shell prompt | Statusline (token savings, agent count) | haystack-console.md |
| man pages | `ostk --agents` guide | installation-and-shim.md |
| Package manager | `ostk run agent.af` (Agentfile = package) | ostk-compile.md |

## Testing

| Unix | ostk | Spec |
|------|----------|------|
| Test suite | `ostk bench` (needle runner) | sprint-5-launch-plan.md |
| Test case | Needle with verb:assert | needle-spec.md |
| Docker image | Bench scenario (Dockerfile + Agentfile) | sprint-5-launch-plan.md |
| CI/CD | `ostk bench --runtime docker/firecracker` | sprint-5-launch-plan.md |

## The Rule

Every Unix primitive has exactly one ostk equivalent. No ostk primitive exists without a Unix ancestor. When adding a feature, find the Unix primitive first. If there isn't one, you're inventing unnecessary complexity.

## Attribution

This spec was not written separately from the work. Every mapping was discovered by USING ostk to build ostk. The act of building IS the act of attributing. `ostk trace` on any row in this table leads back to the session, discussion, needle, and commit that produced it.

## Acceptance Criteria

- [ ] Every kernel feature maps to exactly one Unix primitive
- [ ] Every Unix primitive in this table has a working ostk equivalent
- [ ] No ostk feature exists without a Unix ancestor documented here
- [ ] New features require updating this table before implementation
- [ ] `ostk trace` resolves any entry to its provenance
