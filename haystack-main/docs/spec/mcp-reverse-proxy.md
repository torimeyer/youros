---
status: spec
version: 1
author: scottmeyer + rtx3 (architecture, protocol, competitive)
created: 2026-03-09
needle: →466
evidence: 3-round parallel analysis. R3 competitive scan confirmed market is commodity gateways (Microsoft, IBM, Docker, Envoy, Gravitee). The moat is the kernel, not the proxy.
implements: []
---

# ostk MCP Reverse Proxy

> ostk doesn't proxy MCP. It IS MCP. One endpoint, invisible coordination.

## The Insight

Six companies already ship MCP gateways. They solve routing, auth, rate limiting. That's commodity infrastructure. ostk's value is the 50ms between "agent issues write" and "filesystem changes" — where conflicts are caught, context is injected, and coordination happens invisibly. The proxy is the delivery vehicle. The kernel is the payload.

## Architecture

### What It Is

A syscall dispatcher, not a load balancer. Agents connect to one ostk MCP endpoint. ostk routes tool calls to appropriate backends (fcp-rust, fcp-python, fcp-drawio, the kernel itself). The routing is invisible — agents see a flat tool namespace.

### What It Is Not

- Not an API gateway (no auth, rate limiting, RBAC — those are commodity)
- Not nginx (stateless path-prefix routing)
- Not a service mesh (no inter-backend communication)

## Data Flow

```
Agent calls tool (e.g. ss, rust_query, drawio)
  → ostk receives JSON-RPC request
  → INSPECT: inject identity, check gen counter, read digest
  → ROUTE: tool_name → backend_id lookup
  → FORWARD: dispatch to backend over stdio pipe
  → RECEIVE: backend returns result
  → AUGMENT: inject digest fragment, update gen table, squash output
  → RETURN: agent receives clean response
```

Pass-through is the degenerate case. Tools ostk doesn't coordinate (drawio_query) go straight through. Tools on the write path (ss) get the full kernel treatment.

## Routing Table

Static registration at startup. Each backend declares its tool manifest via MCP `tools/list`.

```
tool_name        → backend
─────────────────────────────
ss               → kernel (built-in)
ss_session       → kernel (built-in)
sh_run           → kernel (built-in)
sh_spawn         → kernel (built-in)
sh_interact      → kernel (built-in)
sh_lock          → kernel (built-in)
sh_session       → kernel (built-in)
rust_session     → fcp-rust (subprocess)
rust_query       → fcp-rust (subprocess)
rust             → fcp-rust (subprocess)
python_session   → fcp-python (subprocess)
python_query     → fcp-python (subprocess)
python           → fcp-python (subprocess)
drawio_session   → fcp-drawio (subprocess)
drawio_query     → fcp-drawio (subprocess)
drawio           → fcp-drawio (subprocess)
```

**Collision rule:** kernel tools shadow backends. fcp-* tools are namespaced by convention (rust_, python_, drawio_). If two backends register the same tool name, config pin wins, then startup order.

## Namespace

Flat. ostk owns it. No `mcp__plugin_fcp-rust_rust__rust_query` — that's a Claude Code artifact, not protocol. Agents see `rust_query`. Period.

## Backend Lifecycle

1. **Startup:** ostk reads config, spawns each backend as child process over stdio, calls `initialize` + `tools/list`, builds merged manifest.
2. **Warm:** backends stay alive between tool calls. No spawn-per-call overhead.
3. **Death:** if a backend crashes mid-call, ostk returns JSON-RPC error `-32000`, restarts the backend. Agent retries naturally.
4. **Degraded:** backends that fail to start are logged and omitted. Remaining backends serve. Not fatal.
5. **Hot-reload:** config directory watch. New fcp-* binary appears → spawn, initialize, merge tools. Send `notifications/tools/list_changed` to client.

## The Interception Point

This is the entire product. Between receiving a tool call and forwarding it:

| On the way in | On the way out |
|----------------|-----------------|
| Inject agent identity | Update gen table |
| Check gen counter (OCC) | Inject digest fragment |
| Enforce concurrency | Squash token-heavy output |
| Log to audit trail | Record heartbeat |

Agents never know coordination happened. The write path stays invisible.

## Vector.dev Parallel

Vector routes observability data: sources → transforms → sinks.
ostk routes agent intent: agents → kernel → backends.

The transform layer is where the value lives. A dumb proxy passes bytes. ostk's kernel transforms the interaction — output compression, conflict resolution, ambient context injection.

## Capability Aggregation

Each backend returns `capabilities` during MCP `initialize`. ostk takes the union but downgrades conservatively — if any backend lacks streaming, ostk advertises no streaming. Per-tool capability tracking adds complexity that buys nothing at this scale.

## What Ships

### Already exists
- `dispatch.rs` — routes tool calls to handlers (sh_run, ss, etc.)
- `serve/` — full MCP server over stdio
- Kernel modules — gen table, identity, digest, heartbeat, Hot PR

### Needs building
1. **Backend spawner** — manage fcp-* subprocesses, stdio pipes, lifecycle
2. **Tool merger** — aggregate `tools/list` from all backends into one manifest
3. **Config format** — enumerate backends (binary path, args, env)
4. **Forwarding** — JSON-RPC proxy for non-kernel tools
5. **`list_changed` notification** — on hot-reload

### Does not need building
- Auth, RBAC, rate limiting (commodity — let Microsoft and Docker do that)
- Load balancing (one backend per domain, not replicas)
- Service discovery (config file, not Kubernetes)

## Competitive Position

Do not position ostk as an MCP gateway. Position it as the coordination kernel that happens to speak MCP. The gateway is the interface; the kernel is the product. Let commodity gateways handle auth and Kubernetes. ostk's job is invisible coordination at the write path.

## Target User

2-5 agents editing one codebase. That's where OCC, Hot PR, gen table, and digest are load-bearing. Solo devs benefit from unified config but can get that from Docker's gateway. The multi-agent team cannot get coordination from anyone else.
