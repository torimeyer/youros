---
title: seamless daemon upgrade
created_at: 2026-03-08T01:17:13Z
status: draft
author: orchestrator
applies_to: mish, ostk
---

# Seamless Daemon Upgrade — Zero-Downtime Agent Sessions

> When the runtime upgrades, agents don't die. This is table stakes for a
> coordination layer that agents trust.

## The Problem

Today: `cargo install mish` -> kill daemon -> all serve processes lose PTYs ->
all agent sessions get EIO -> every agent loses shell state -> manual `/mcp`
reconnect per session. With 5 agents running, that's 5 interrupted workflows
and 5 manual recoveries.

This is the container restart problem. Docker solved it. We need to solve it
for agent sessions.

## Constraints

1. Agents are mid-thought -- killing them loses context (compaction recovery
   is expensive, sometimes impossible)
2. The daemon owns the process table -- restarting orphans everyone
3. `mish serve` instances hold PTY file descriptors -- PTY fds can't survive
   process restart via exec
4. The Unix socket protocol may change between versions
5. Multiple Claude Code sessions may be running with different mish versions

## Design: Serve Owns PTYs, Daemon Coordinates

### Current Architecture (fragile)

```
daemon (owns PTYs, process table, socket)
  +-- serve1 (stdio<->socket bridge, stateless)
  +-- serve2 (stdio<->socket bridge, stateless)
```

Daemon dies -> PTYs die -> everything dies.

### Proposed Architecture (resilient)

```
daemon (process table, coordination, NO PTY ownership)
  +-- serve1 (owns its PTYs, reconnects on daemon death)
  +-- serve2 (owns its PTYs, reconnects on daemon death)
```

Daemon dies -> serves hold PTYs locally -> new daemon starts -> serves
re-register -> agents never notice.

### Upgrade Sequence

```
1. New binary installed (cargo install / package manager)
2. Signal old daemon: SIGUSR1 (prepare for handoff)
3. Old daemon:
   a. Stops accepting new connections
   b. Serializes process table to .local/share/mish/handoff.json
   c. Exits cleanly (or new daemon starts and old daemon exits)
4. New daemon starts:
   a. Reads handoff.json
   b. Listens on same socket path
   c. Accepts reconnections from existing serves
5. Each serve:
   a. Detects socket EOF (daemon gone)
   b. Holds PTYs -- they're still alive (serve owns the fds)
   c. Retries connection to socket with backoff
   d. Re-registers sessions with new daemon
   e. Resumes operation -- agent never sees interruption
```

### Protocol Versioning

```
serve -> daemon: { "protocol": 2, "client_version": "0.4.33" }
daemon -> serve: { "protocol": 2, "server_version": "0.4.34", "compat": [1, 2] }
```

- Daemon advertises which protocol versions it supports
- Serve picks highest common version
- Breaking protocol changes bump major version, old version supported for 2 releases
- Unknown fields are ignored (forward compat)

### Serve-Side Reconnect State

What serve needs to hold across daemon restarts:

```rust
struct ServeReconnectState {
    sessions: HashMap<String, SessionState>,
    locks: Vec<String>,        // active mish locks
    client_id: String,         // stable across reconnects
}

struct SessionState {
    alias: String,
    pty_fd: OwnedFd,          // serve owns this
    pid: Pid,                  // child process
    cwd: String,
    shell_path: String,
}
```

### Failure Modes

| Scenario | Behavior |
|----------|----------|
| Daemon killed, new daemon starts | Serves reconnect, re-register sessions |
| Daemon killed, no new daemon | Serves hold PTYs, retry with backoff, timeout after 30s, then degrade to local-only mode |
| Serve killed | Daemon detects disconnect, marks sessions as orphaned. New serve can re-adopt if same client_id. |
| Both killed | PTYs cleaned up by kernel. Agents get EIO. This is the only data-loss case. |
| Protocol mismatch | Serve falls back to lowest common version. If no overlap, serve logs error and operates local-only. |

### What "Local-Only Mode" Means

If serve can't reach any daemon, it still works -- it just loses cross-session
visibility and coordination. Commands still execute in the PTY. The agent
doesn't notice. When a daemon appears, serve reconnects and coordination
resumes.

This is the key insight: **the daemon is an optimization, not a dependency.**

## Migration Path

1. **v0.4.x**: Move PTY ownership from daemon to serve. Daemon becomes
   coordinator only. Backward compat: old serves still work (daemon creates
   PTYs for them).
2. **v0.5.0**: Serve-side reconnect. Protocol version 2. Old serves get
   "please upgrade" warning but still function.
3. **v0.5.x**: Handoff protocol. `mish upgrade` command that orchestrates
   the full sequence.
4. **v1.0**: ostk manages this -- `ostk upgrade` handles both mish
   and slipstream upgrades across all agent sessions.

## Acceptance Criteria

- [ ] Serve owns PTY file descriptors, not daemon
- [ ] Daemon restart does not kill existing shell sessions
- [ ] Serve reconnects to new daemon within 5s
- [ ] Agent sessions survive daemon upgrade with zero user intervention
- [ ] Protocol version negotiation on connect
- [ ] `mish upgrade` command for orchestrated daemon replacement
- [ ] Graceful degradation when daemon is unavailable (local-only mode)
- [ ] Handoff state serialized to disk (survives daemon crash)
- [ ] Mixed-version serves work with newer daemon
