# mish as Agent Microkernel

**Date:** 2026-03-06
**Status:** Design proposal (v2 — agentmail removed, Hot PR added)
**Authors:** Scott Meyer, Claude (inner-agent dogfooding session)

## One-sentence pitch

mish is a microkernel for AI agents — process table, coordinated filesystem with automatic conflict resolution, in a single binary, with the orchestrator as the scheduler.

## The paradigm shift: Perforce → Git

Traditional multi-agent coordination uses pessimistic locking: agents announce claims (`[CLAIMED] file.py`), acquire reservations, work in isolation, release when done. One writer at a time. This is Perforce.

mish uses optimistic concurrency: every agent writes freely. Conflicts are resolved at write time — automatically when possible, with agent assistance when not. No locks, no claims, no announcements. This is Git.

Just as git replaced Perforce because optimistic concurrency scales better with distributed teams, mish replaces claim-based coordination because it scales better with distributed agents. No lock contention, no coordination overhead, no stale claims from compacted agents who forgot to release.

## Background

mish started as an LLM-native shell: a CLI proxy and MCP server for process supervision. Slipstream started as a separate MCP server for file editing. In practice, agents use both simultaneously — every tool call goes through mish for shell I/O and slipstream for file ops.

Running them as separate systems creates coordination gaps:

- **File clobbering**: parallel agents edit overlapping files with no conflict detection. Discovered during worktree experiments where agents editing `types.rs` clobbered each other on copy-back.
- **No ambient file awareness**: agents re-read files they just edited (to verify), wasting tokens. mish's process table gives ambient compute awareness; no equivalent exists for files.

This design absorbs slipstream into mish and extends the architecture into a microkernel for multi-agent development.

## Architecture: Unix kernel mapping

```
Unix kernel          →  mish
────────────────────────────────────
Process table        →  [procs] digest (exists today)
Filesystem + inodes  →  [files] digest with generation counters
write() conflict     →  Hot PR (auto-merge / assisted merge)
File descriptors     →  MCP connection IDs
PIDs                 →  agent aliases (cc, gem, ...)
open() / read()      →  ss_session("read <path>")
write() / fsync()    →  ss(path, old_str, new_str) + flush
kill() / signal()    →  sh_interact(action="send_signal")
fork() / exec()      →  sh_spawn
Permission model     →  policy engine (mish.toml)
Scheduler            →  Teams (external orchestrator)
```

**Microkernel, not monolithic.** mish provides primitives — process management, file coordination, conflict resolution. It does not parse code, resolve semantic conflicts, or make scheduling decisions. Agents and the orchestrator (Teams) operate in userspace.

## 1. Absorption model

### Single binary, two tool families

Slipstream's file operations are rewritten in Rust and merged into the mish binary. The API surface stays separated into two tool families:

| Family | Tools | Domain |
|--------|-------|--------|
| `sh_*` | sh_run, sh_spawn, sh_interact, sh_session, sh_help | Process supervision |
| `ss_*` | ss, ss_session, ss_help | File coordination |

One process, one MCP connection per agent, shared internal state (process table, file table).

### Why absorb, not bridge

- **Atomic coordination**: one process owns all state. No IPC between mish and slipstream, no race conditions at the boundary.
- **Shared identity**: a single MCP connection = one agent. File edits and process spawns are attributed to the same identity.
- **Single digest**: process and file awareness in one response. No need to query two servers.

## 2. File coordination: optimistic concurrency control

### str_replace is already a compare-and-swap

Slipstream's edit primitive — `ss(path, old_str="foo", new_str="bar")` — is accidentally an OCC token. If `old_str` no longer exists in the file (because another agent changed it), the edit fails. The match string IS the compare-and-swap.

This catches textual conflicts with zero additional machinery.

### Three-layer conflict model

| Layer | Mechanism | Catches |
|-------|-----------|---------|
| **CAS (str_replace)** | old_str must match current file content | Overlapping edits to same text |
| **Generation counter** | Per-file monotonic counter, bumped on every write | Non-overlapping concurrent edits (same file, different regions) |
| **Tests** | Agent-driven, external to mish | Semantic incompatibility (rename breaks call site) |

mish handles layers 1 and 2. Layer 3 is the agent's responsibility. mish is infrastructure, not a compiler.

### Generation counter semantics

Every file touched through `ss_*` gets a generation counter. On edit:

1. Generation increments
2. Editor identity and timestamp recorded
3. If the editing agent's last-read generation is stale, the response includes a warning:

```
⚠ src/main.rs modified since your read (gen 5→7, by agent-gem 10s ago)
  diff: -fn old_name() +fn new_name()
  Your edit succeeded — verify semantic compatibility.
```

The agent decides whether to re-read, abort, or proceed. mish informs, doesn't block.

### Hot PR: conflict resolution at write time

When a write conflicts (CAS fails because another agent edited the same text), instead of a bare rejection, mish returns the diff and attempts resolution:

**Three tiers:**

| Tier | Condition | Action |
|------|-----------|--------|
| **Auto-merge** | Changes don't touch the same lines | Apply automatically, bump gen. Agent never sees a conflict. |
| **Assisted merge** | Changes overlap, diff is small | Return the diff + suggested resolution. One confirmation turn. |
| **Manual rebase** | Deep semantic conflict | Return full diff, let the agent decide. |

**Conflict response (assisted merge):**

```
CONFLICT on src/main.rs (your base: gen 5, current: gen 6)

--- gen 5
+++ gen 6
@@ -42,3 +42,7 @@
 def process(data):
+    if not data:
+        raise ValueError("empty input")  # added by gem
     result = transform(data)

Your intended change:
  old_str: "result = transform(data)"
  new_str: "result = transform(data, validate=True)"

Suggested merge: [AUTO-MERGEABLE: changes don't overlap]
```

**Why this works:** LLMs are the best merge tools ever invented. Traditional 3-way merge is syntactic — it fails on anything non-trivial. An LLM reads the diff and understands "gem added validation, I'm adding a parameter — these compose cleanly." Semantic merge for free.

**Why this eliminates claims:** The entire `[CLAIMED]` / `[CLOSED]` / file reservation pattern was pessimistic locking — a workaround for systems that can't resolve conflicts. With Hot PR, agents just write. Conflicts are resolved, not prevented. The coordination overhead drops to zero for non-overlapping edits (auto-merge) and to one confirmation turn for overlapping ones.

### HTTP caching semantics

The generation counter maps directly to well-understood HTTP caching:

```
HTTP                    →  mish
──────────────────────────────────────
ETag: "gen=7"           →  src/main.rs:gen=7
Last-Modified: 5m ago   →  :cc:5m
If-None-Match: gen=5    →  agent reads at gen=5, file now gen=7 → stale
304 Not Modified        →  "you have latest, gen=7" (read elision)
Vary: Agent             →  per-agent staleness tracking
```

**Read elision (304 Not Modified)**: mish tracks each agent's read high-water mark per file. If an agent requests a file it already has at the current generation, mish returns a 5-token confirmation instead of the file contents. Over a session with dozens of reads, this saves thousands of tokens.

## 3. Dual digest

Every tool response includes an ambient status digest. Two channels:

```
[procs] cc:running:85m gem:running:52s build:exit(0):2m
[files] src/main.rs:gen=7:cc:2m src/lib.rs:gen=3:gem:30s
```

### Adaptive suppression

| Tier | Channel | Show when | Rationale |
|------|---------|-----------|-----------|
| 1 (always) | `[procs]` | Every response | Compute awareness is always relevant |
| 2 (if stale) | `[files]` | File modified since agent's last read | No news = no tokens |

**Token budget**: ~40-80 tokens when active, ~15-30 tokens when quiet, 150-token ceiling.

Cost scales with information density. A quiet system costs the same as today's process-only digest.

## 4. Two-server ecosystem

### Topology

```
                    Teams (scheduler)
                        │
                  ┌─────┴─────┐
                  │           │
              mish serve    fcp-*
           (compute+state) (device drivers)
                  │           │
                  └─────┬─────┘
                        │
                  Agent (cc, gem)
```

Two servers, two concerns:

| Server | Concern | Provides |
|--------|---------|----------|
| **mish serve** | Compute + state + coordination | Shell, files, OCC, Hot PR, process/file digest |
| **fcp-*** | Domain intelligence | Definitions, references, diagnostics (rust-analyzer, pylsp, drawio, etc.) |

No overlap. Agents compose what they need — shell-only agents connect to mish alone, code-heavy work adds fcp-rust. Slipstream's file editing is absorbed into mish; its FCP plugin system continues as independent `fcp-*` servers.

### Why no messaging server

Traditional multi-agent systems need a messaging layer because they use pessimistic coordination: agents must announce claims, broadcast completions, and reserve resources. With optimistic concurrency (Hot PR), that entire category of coordination disappears:

| Old pattern | Replacement |
|-------------|-------------|
| `[CLAIMED] file.py` | Just write. Hot PR handles conflicts. |
| `[CLOSED] task-123` | Gen counter is the record. Digest shows it. |
| File reservations | Unnecessary. OCC resolves at write time. |
| `[BLOCKED]` signal | Scheduler concern (Teams), not messaging. |
| `[COMPACTED]` broadcast | mish detects stale high-water marks, generates delta automatically. |
| Inbox/outbox/threading | Eliminated. No messages to manage. |

The "messaging pillar" was papering over missing infrastructure. With conflict resolution built into the write path, agents coordinate through the filesystem itself — the same way git users coordinate through the repository, not through email.

## 5. Identity model

### MCP connection = agent identity

```
MCP connection (fd=7) → alias "cc" → agent name "claude"
MCP connection (fd=9) → alias "gem" → agent name "gemini"
```

One connection = one agent = one set of:
- File read high-water marks (per-file generation seen)
- Process table entries
- Edit attribution history

No registration ceremony, no auth tokens. Connect to mish, get an identity. Disconnect, state persists for catch-up on reconnect. Alias is the human-readable name; connection ID is the internal key.

### Identity survives agent restart

If Gemini's PTY dies and respawns with alias "gem", mish resumes the identity: "welcome back — 4 file changes since your disconnect." Generation counters are keyed to alias, not connection fd.

## 6. Context recovery (compaction deltas)

Every LLM agent eventually loses context (compaction, crash, token limit). mish detects this via stale high-water marks and generates a delta:

```
Since your last coherent state (gen=5):
  [files] pipeline.rs: gen 5→8 (3 edits by gem, cc)
  [procs] build:exit(0) server:running:12m
```

This is the kernel providing a "session resume" primitive. Per-agent high-water marks make it possible — mish knows what each agent last saw and can compute the delta. Persistent memory through infrastructure rather than through the model.

No broadcast needed. mish knows each agent's state. When an agent reconnects or makes a request with stale context, the delta is included automatically.

## 7. State persistence

In-memory state needs durability across mish restarts:

| State | Persistence | Rationale |
|-------|-------------|-----------|
| Process table | Reconstructed (processes survive or don't) | Ephemeral by nature |
| File generations | Persisted to `~/.local/share/mish/state.json` | Must survive restart |
| Agent high-water marks | Persisted per-alias | Required for read elision and deltas |

Lightweight: fsync on generation bump, not on every read. State file is small — one entry per tracked file, one high-water mark per agent.

### Scope boundary

mish tracks only files touched through `ss_*` tools. If an agent bypasses slipstream (`cat > file.py << 'EOF'`), mish doesn't know. This is acceptable — the agent opted out of coordination. Same as a Unix process writing directly to `/dev/` bypassing the filesystem.

## 8. Teams integration

Teams is the scheduler that sits above the microkernel. Clean separation:

| Concern | Owner |
|---------|-------|
| "Who works on what" | Teams (scheduling) |
| "What does the world look like right now" | mish (awareness) |
| "Execute this command / edit this file" | mish (primitives) |
| "Resolve file conflicts" | mish (Hot PR) |
| "Make design decisions" | Agents (userspace) |

Teams invokes mish primitives. mish doesn't know about tasks, phases, or beads. It provides the substrate; Teams provides the intelligence.

## Open questions

1. **Permission model**: should mish enforce per-agent file permissions (agent-cc can only edit files in track-A), or is that Teams' responsibility? Leaning toward Teams — mish provides the `Vary: Agent` tracking, Teams decides policy.

2. **Digest format**: the current `[procs] cc:running:85m` format is ad-hoc. Should the digest be structured (JSON) for machine parsing, or stay human-readable for token efficiency? Leaning human-readable with a `--json` flag for programmatic consumers.

3. **Slipstream migration path**: the absorption requires rewriting slipstream's Python in Rust. Phased approach? Start with file coordination (gen counters, digest) as a mish-native layer, keep slipstream running alongside for the actual str_replace engine, then absorb fully.

4. **Scale limits**: how many files and agents before the digest becomes unwieldy? Probably fine for 5-10 agents and 50-100 active files. Beyond that, the adaptive suppression tiers become critical.

5. **Hot PR auto-merge safety**: should tier-1 auto-merge (non-overlapping changes) always be silent, or should agents optionally see what was merged? Leaning silent-by-default with a `--verbose-merge` flag.

## Summary

mish evolves from "LLM-native shell" to "agent microkernel":

- **Process table** for compute awareness (exists today)
- **Filesystem with OCC** for state coordination (str_replace CAS + generation counters)
- **Hot PR** for automatic conflict resolution (auto-merge, assisted merge, manual rebase)
- **Dual digest** for ambient awareness (process + file, adaptively suppressed)
- **Identity** via MCP connection, surviving reconnects
- **Context recovery** via per-agent deltas
- **Two-server ecosystem**: mish (compute + state + coordination), fcp-* (device drivers)
- **Teams as scheduler** sitting cleanly above

One kernel. No locks, no claims, no messages. Agents coordinate through the filesystem — conflicts resolved, not prevented.
