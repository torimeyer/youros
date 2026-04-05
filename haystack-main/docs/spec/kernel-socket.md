---
promoted_at: 2026-03-12T17:41:02Z
created_at: 2026-03-12T17:39:35Z
needle: →619
compounds: escape-harness, from-auto, tui-multiplexer, fleet-connectivity, negotiate-protocol
title: Kernel Socket — IPC Between ostk and ostk Kernel
status: spec
author: scott+haystack.prime
evidence: os-tack/ostk.ai PR
implements: []
---

# Kernel Socket — IPC Between ostk and ostk Kernel

> `.ostk/sock` is the nervous system. Without it, agents are isolated processes.
> With it, the OS is alive.

## The Problem

Without a socket, every agent session is disconnected:
- `ostk run` spawns agents that can't find the kernel
- Review pool `sh_run` returns nothing — no kernel reachable
- FROM auto can't query vault — no live kernel to ask
- TUI polls files instead of receiving push events
- Agents return transcripts, not results

The socket closes all of these gaps with one primitive.

## Protocol

### Transport

FIFO pair at well-known paths:

```
.ostk/sock        # command channel (callers write here)
.ostk/sock.reply  # reply channel   (callers read here)
```

Both FIFOs created by `ostk listen` at daemon start.
Callers check `kernel_alive()` before connecting — fall back to fork if not alive.

### Wire Format

```
# Request (caller → .ostk/sock)
<COMMAND>\n
<payload lines>\n
END\n

# Response (kernel → .ostk/sock.reply)
STATUS <code>\n
<payload lines>\n
END\n
```

Status codes:
- `STATUS OK` — command accepted, payload follows
- `STATUS ERR` — command failed, reason in payload
- `STATUS NACK` — negotiate rejected (identity/tier failure)
- `STATUS ACK` — negotiate accepted, session bound

### Commands

| Command | Direction | Purpose |
|---------|-----------|---------|
| `BOOT` | caller→kernel | Initialize session, get kernel state |
| `NEGOTIATE <session_id>` | caller→kernel | Start identity handshake |
| `COMPILE` | caller→kernel | Trigger compile pipeline |
| `BENCH <scenario>` | caller→kernel | Run bench scenario |
| `VAULT QUERY` | caller→kernel | List available models (FROM auto) |
| `NUDGE <alias> <msg>` | kernel→caller | Push notification to agent |
| `PING` | caller→kernel | Liveness check |

### kernel_alive()

```sh
kernel_alive() {
    [ -p "${OSTK_DIR}/sock" ] && \
    [ -p "${OSTK_DIR}/sock.reply" ] && \
    kill -0 "$(cat "${OSTK_DIR}/kernel.pid" 2>/dev/null)" 2>/dev/null
}
```

Three conditions: sock FIFO exists, reply FIFO exists, kernel PID alive.
All three must be true — partial state means crashed kernel, not live kernel.

## Negotiate Handshake

The socket carries the negotiate protocol (GOVERNANCE Part 12, runtime form):

```
ostk boot
  → resolve_identity()           # GPG → SSH → $OSTK_ENTITY → anonymous
  → NEGOTIATE <session_id>       # write to .ostk/sock
      payload: {type, key_fp, tier_claim, sign_method}

ostk listen (kernel)
  → reads NEGOTIATE from sock
  → verify_identity(payload)     # check key fingerprint, signing method
  → write kernel reply to sock.reply:
      STATUS ACK
      verified=<method>
      tier=<T0|T1|T2|T3>
      nonce=<random>
      END

ostk
  → reads ACK from sock.reply
  → appends kernel binding to session:
      bound=true, tier=T1, verified=ssh, nonce=<x>
  → write path now open at tier level
```

Identity resolution order:
1. GPG key (T0 — root authority)
2. SSH signing key (T1 — verified agent)
3. `$OSTK_ENTITY` env var (T2 — named but unverified)
4. anonymous (T3 — read-only)

Write path enforcement: T2/T3 blocked from `compile`, `bench`, vault mutations.

## compile.d Pipeline

Compile is not a single command — it's a pipeline:

```
.ostk/compile.d/
  00-syntax.sh     # verify ostk + ostk parse cleanly
  10-*.sh          # additional stages (lex order)
  ...
```

Each stage is an executable. `ostk listen` runs stages in lexicographic order.
Stage failure halts pipeline, returns `STATUS ERR` with failing stage name.
New stages added by dropping executables into `compile.d/` — no config change.

## Compounds

### Fleet Connectivity
Spawned agents (from `ostk run`) call `kernel_alive()` at boot.
If alive: connect via socket. Session bound, write path open, vault queryable.
Fixes: review pool dead on arrival, bench pane agent lost its codebase.

### FROM auto
`VAULT QUERY` via socket returns live vault inventory.
Scheduler resolves `FROM auto` against live models, not stale config.
Vault = what's available right now, not what was configured at install.

### TUI Multiplexer
Kernel pushes `[procs]`, `[files]`, `[nudge]`, `[ctx]` tokens via socket to TUI process.
TUI subscribes on startup: `SUBSCRIBE digest`.
Eliminates polling — push model, sub-second latency.

### Escape the Harness
Socket = the return path for `ostk spawn`.
Agent completes work → writes result → kernel receives via sock → routes to caller.
Agents return structured results, not transcripts.
The `Agent tool` return path (harness) is no longer needed.

## Implementation Status

From os-tack/ostk.ai PR #1 (dab23888):

| Component | Status |
|-----------|--------|
| FIFO pair creation | ✓ shipped |
| `ostk listen` daemon | ✓ shipped |
| `kernel_alive()` check | ✓ shipped |
| Wire format (STATUS/END) | ✓ shipped |
| Negotiate handshake | ✓ shipped |
| `compile.d` pipeline | ✓ shipped |
| 26 integration tests | ✓ shipped |
| VAULT QUERY command | pending |
| TUI SUBSCRIBE command | pending |
| Agent spawn return path | pending |

PR #1 blocked pending: S1 fix (bundle ostk in signed tarball) + signed offer from @ostk.ai.prime.

## Acceptance Criteria (kernel-socket spec)

- [ ] `ostk listen` creates FIFO pair, writes PID to `kernel.pid`
- [ ] `kernel_alive()` returns true iff all three conditions hold
- [ ] `NEGOTIATE` → `ACK/NACK` round-trip completes in < 100ms
- [ ] T1 caller can `COMPILE` and `BENCH`; T2/T3 caller receives `STATUS NACK`
- [ ] `compile.d` stages run lex-order; failure halts with stage name in error
- [ ] `VAULT QUERY` returns live vault inventory (FROM auto unblocked)
- [ ] TUI `SUBSCRIBE digest` receives push events from kernel
- [ ] Spawned agents connect via socket; results return to caller (Agent tool retired)
- [ ] Kernel crash: FIFO pair removed, `kernel_alive()` returns false cleanly
