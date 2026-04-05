# MCP Spec as Unix Primitives

**Date:** 2026-03-06
**MCP Spec Version:** 2025-11-25

The MCP specification already contains almost every Unix kernel primitive. They just haven't been assembled into a kernel before.

## Transport

| MCP | Unix |
|-----|------|
| **stdio** (subprocess, newline-delimited JSON-RPC) | Pipe to child process |
| **Streamable HTTP** (SSE-capable) | Network socket |

## Server Features (what mish exposes)

| MCP Feature | Unix Concept | Notes |
|---|---|---|
| **Tools** — callable functions with JSON Schema input | **syscalls** | `sh_run`, `ss`, etc. are literally syscalls into the kernel |
| **Resources** — readable data identified by URI | **/proc, /dev** — virtual filesystem | mish could expose `mish://procs/cc`, `mish://files/src/main.rs` |
| **Resource subscriptions** — client subscribes, server pushes `notifications/resources/updated` | **inotify / kqueue** | **THIS IS THE IRQ.** Agent subscribes to a file resource. When another agent edits it, mish pushes a notification. No polling. |
| **listChanged** — server notifies when tool/resource list changes | **Device hotplug (udev)** | New fcp-* connects → tools list changes → agent gets notified |
| **Logging** — structured server→client log messages | **syslog / dmesg** | |
| **Completion** — argument autocomplete | **Tab completion** | |
| **Pagination** — cursor-based result paging | **readdir with offset** | |

## Client Features (what the agent host provides)

| MCP Feature | Unix Concept | Notes |
|---|---|---|
| **Sampling** — server requests LLM inference from client | **Server requesting CPU time** | The LLM IS the CPU. mish could use sampling to ask the agent's model to evaluate a merge conflict. |
| **Elicitation** — server requests user input (form or URL mode) | **Upcall / ioctl to userspace** | Kernel asking user process for a decision. Hot PR "assisted merge" → elicitation to the agent. |
| **Roots** — client tells server about workspace dirs | **Mount points** | |

## Utilities

| MCP Feature | Unix Concept |
|---|---|
| **Ping** | Watchdog timer / heartbeat |
| **Cancellation** | SIGINT / SIGTERM |
| **Progress** | `/proc/[pid]/status` |

## Notifications (bidirectional push)

| Direction | MCP | Unix |
|---|---|---|
| Server → Client | `notifications/resources/updated` | **IRQ** — hardware interrupt |
| Server → Client | `notifications/resources/list_changed` | **udev hotplug** |
| Server → Client | `notifications/tools/list_changed` | **Module load** |
| Client → Server | `notifications/roots/list_changed` | **mount/unmount** |
| Client → Server | `notifications/cancelled` | **SIGINT** |

## The Three Big Ones

### 1. Resource subscriptions = inotify (IRQ)

mish exposes each tracked file as an MCP resource (`mish://files/src/main.rs`). Agent subscribes via `resources/subscribe`. When ANY agent edits that file, mish pushes `notifications/resources/updated`. The subscribing agent doesn't poll — it gets interrupted.

The digest becomes opt-in per-file rather than broadcast-everything. An agent working on `parser.rs` subscribes to that resource. An agent working on `server.rs` doesn't. Each agent only gets interrupts for files it cares about.

This is the mechanism that replaces the `[mail]` digest tier. No messaging needed — file change notifications are built into MCP.

### 2. Sampling = server requesting CPU

The LLM is the compute unit. When mish hits an assisted-merge Hot PR conflict, it could use `sampling/createMessage` to ask the agent's own model: "Here's the diff, here's your intended edit, should I auto-merge?"

The kernel is requesting CPU cycles from the process to resolve a conflict. The agent doesn't even need to make a tool call — mish resolves it inline. Parameters available:

- `messages`: the conflict context (diff, intended edit, suggested merge)
- `modelPreferences`: can request fast/cheap model for simple merges
- `systemPrompt`: merge-resolution instructions
- `maxTokens`: keep it tight
- `tools`: could even give the model merge-specific tools

### 3. Elicitation = upcall to userspace

When mish hits an unresolvable conflict (manual rebase tier), it elicits the human via `elicitation/create` with the diff displayed in a form. The kernel escalates to the operator.

Form mode elicitation supports:
- `message`: "Merge conflict in src/main.rs — two agents edited overlapping regions"
- `requestedSchema`: structured choices (accept theirs, accept mine, manual edit)

This is the operator handoff pattern already in mish, but formalized in the MCP spec. No custom protocol needed.

## What mish already has vs what MCP provides

| Primitive | mish today | MCP provides | Gap |
|---|---|---|---|
| Process table | `[procs]` digest on every response | Resources (`mish://procs/*`) + subscriptions | Expose as resources for subscription |
| File awareness | `[files]` digest on stale | Resources (`mish://files/*`) + subscriptions | Expose as resources, push on change |
| IRQ / interrupts | Polling (digest on tool response) | `notifications/resources/updated` | Implement resource subscriptions |
| Conflict resolution | Planned (Hot PR) | Sampling for auto-resolve, elicitation for escalation | Wire sampling into Hot PR |
| Operator handoff | Custom (handoff IDs) | Elicitation (form + URL mode) | Replace custom with MCP elicitation |
| Device drivers | fcp-* as separate MCP servers | `notifications/tools/list_changed` for hotplug | Already works |
| Context recovery | High-water marks + delta | Resources with `lastModified` annotation | Enhance with MCP annotations |

## Implications for ostk architecture

The MCP spec already has the primitives. ostk's job is to **assemble them into a kernel**:

1. **Tools = syscalls** — already done (sh_*, ss_*)
2. **Resources = virtual filesystem** — expose process table and file state as subscribable MCP resources
3. **Subscriptions = IRQ** — agents subscribe to files they care about, get push notifications on change
4. **Sampling = CPU** — kernel can request compute from the agent's model for conflict resolution
5. **Elicitation = upcall** — kernel escalates to human when automated resolution fails
6. **Notifications = signals** — bidirectional push for state changes

No custom protocols. No messaging servers. Just MCP, assembled correctly.
