---
status: draft
author: orchestrator
created: 2026-03-07
---

# PTY Multiplexing: Share Sessions Across Connections

## Problem

Each mish daemon connection gets its own session with its own PTY. With 7 agents,
that's 7 PTYs with 7 master fds, 7 kernel buffers, 7 concurrent read loops.
Even with RwLock (v0.4.25), this creates I/O contention at the kernel level —
each PTY's 4KB buffer fills independently and the daemon must drain all of them.

## The Multiplexing Idea

Instead of one PTY per connection, agents share a **pool of PTY sessions**:

```
Connection 1 (agent A) ─┐
Connection 2 (agent B) ──┼── PTY Pool (N sessions) ── shell processes
Connection 3 (agent C) ─┘
```

When agent A runs `sh_run("cargo test")`:
1. Agent A's connection acquires a PTY from the pool
2. Command runs in that PTY
3. Output is captured and returned to agent A
4. PTY is released back to the pool
5. Agent B can now use the same PTY

### Why this works for `sh_run`

`sh_run` is fire-and-forget — run command, capture output, return. The agent
doesn't hold the PTY between calls. A pool of 3-4 PTYs can serve 10+ agents
because most agents are thinking (not running commands) at any given moment.

### Why this doesn't work for `sh_spawn` / dedicated PTY

Dedicated PTY agents (inner Claudes) need persistent PTY sessions. They can't
share — each one has its own TUI state. These stay as-is: one PTY per dedicated
agent, owned for the agent's lifetime.

## Pool Design

```rust
struct PtyPool {
    available: Vec<Session>,    // idle sessions ready for commands
    in_use: HashMap<String, Session>,  // alias → session currently running a command
    max_size: usize,            // pool ceiling
}

impl PtyPool {
    async fn acquire(&mut self) -> Session {
        if let Some(session) = self.available.pop() {
            return session;
        }
        if self.total() < self.max_size {
            return Session::new().await;
        }
        // Wait for a session to be released
        self.wait_for_available().await
    }

    fn release(&mut self, session: Session) {
        self.available.push(session);
    }
}
```

## Connection Multiplexing (alternative)

Instead of pooling PTYs, multiplex at the connection level:

```
Agent A ──┐                    ┌── PTY 1 (main session)
Agent B ──┼── MUX (daemon) ────┤
Agent C ──┘                    └── PTY 2 (overflow, created on demand)
```

The daemon maintains 1-2 main sessions. When multiple agents submit commands
simultaneously, they queue. The MUX decides ordering:
- Priority-based (P0 task goes first)
- Round-robin for equal priority
- Preemption for urgent commands

This is simpler than a full pool — just a queue in front of a fixed number
of sessions.

## Comparison

| Approach | PTYs | Concurrency | Complexity |
|----------|------|-------------|------------|
| Current (one per connection) | N | Full parallel | Low but crashes at N>3 |
| Pool | 3-4 | Limited parallel | Medium |
| MUX queue | 1-2 | Sequential + priority | Low |
| RwLock + Semaphore (v0.4.25) | N | Parallel with backpressure | Low (shipped) |

## Recommendation

v0.4.25 (RwLock + Semaphore) is the immediate fix. If it still crashes under
load, implement the MUX queue as the next step — it's the simplest and fits
the pull model (agents queue commands, daemon processes them in order).

Full PTY pooling is only needed if we find that sequential processing is too
slow (agents waiting for each other's commands). Profile first, then decide.

## Acceptance Criteria

- [ ] Profile: measure daemon performance with 3, 5, 7 concurrent agents on v0.4.25
- [ ] If crashes persist: implement MUX queue with priority ordering
- [ ] If queue is too slow: implement PTY pool with acquire/release
- [ ] Dedicated PTY agents always get their own session (no pooling)
