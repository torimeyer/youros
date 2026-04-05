---
title: "→902: Multi-Model Session Hand-Off"
implements: []
---

# →902: Multi-Model Session Hand-Off
<!-- status: spec | implements: →902 | promoted: 2026-03-28 -->

## Discovery

Two TUI instances (Claude + Gemini) accidentally shared a daemon session.
User typed "continue" in Claude's TUI; Gemini responded "Continuing" while
Claude resumed tool execution. Neither model lost context. The kernel's
filesystem-as-coordination-layer produced coherent multi-model hand-off
through state convergence.

## Core Principle

A conversation is a **session**, not a model. The model is a rendering
parameter. `SharedMessages` (`Arc<Mutex<Vec<Message>>>`) is the fork
primitive — cloning the Arc gives shared live history.

## Architecture: Three Layers

### Layer 1: Intra-Session (same daemon, ~150 lines)

Switch the model on the current session. History preserved. Attribution
on messages shows `[claude]` vs `[gemini]` badges.

**Already works:** `:model gemini` changes `config.model`, calls
`invalidate_client()`, boot context refreshes. The conversation continues.

**Additions needed:**
- `model: Option<String>` on `Message` for attribution
- Attribution rendering in `components/render.rs`
- Status bar chain: `claude→gemini` instead of single model name

**Verbs:**
- `:model <name> ["note"]` — silent switch, history preserved
- `:handoff <name> ["note"]` — switch + inject transition message + observer mode

### Layer 2: Inter-Session (daemon multiplexing, ~200 lines)

Multiple TUI clients coordinate through the daemon. Each gets isolated
event channels but can observe shared sessions.

**Additions needed:**
- `ClientId` + `bound_session` on daemon connections
- `session/dispatch` routes through client binding, not `mgr.active`
- Event fan-out: daemon polls sessions, broadcasts to bound clients
- `SessionManager::fork_from(name, source, model)` — share the
  `SharedMessages` Arc, different `LoopConfig`

**Verbs:**
- `:observe <session>` — read-only binding to another session
- `:relay <model> ["query"]` — both models respond, user picks
- `:who` — show connected clients

### Layer 3: Cross-OS (registry-mediated, spec exists)

Hand off context between different project OS instances. The trust-chain
`@import` ceremony gates authorization.

**Already exists:**
- `:handoff @alias →needle` interception (tested, broadcasts `fcp.resolved`)
- `~/.ostk/registry.jsonl` tracks live OS instances
- Trust-chain spec defines GPG verification for cross-OS operations

**Missing:** routing from `@alias` to a remote daemon socket.

**Verbs:**
- `:handoff @alias →needle` — cross-OS context transfer

## Existing Primitives (no new code needed)

| Primitive | Location | Role |
|-----------|----------|------|
| `SharedMessages` (Arc) | `cpu/session.rs` | Shared live history |
| `SessionManager.switch()` | `cpu/session.rs` | Named session switching |
| `AgentSession.outbox` | `cpu/session.rs` | Inter-session message injection |
| `LoopConfig.model` per-session | `cpu/session.rs` | Hot-swappable model |
| `driver_matches()` | `cpu/session.rs` | Per-model driver creation |
| Hot PR OCC | `serve/tools/fs_ops.rs` | Conflicting file edit prevention |
| Dying/nudge system | `kernel/dying.rs` | Hand-off prototype |
| `:handoff` interception | `tests/agent_coordination.rs` | Already works |
| Registry | `kernel/registry.rs` | Cross-project discovery |
| Trust-chain `@import` | `docs/spec/trust-chain.md` | Security gate |

## Implementation Priority

1. **Phase 1:** Per-client isolation (fix the bug) + `:model` with attribution
2. **Phase 2:** `:handoff` verb + observer mode + event fan-out
3. **Phase 3:** Cross-OS routing via registry + trust-chain

## The Insight

The kernel doesn't coordinate agents — it coordinates **context**. The
model that generates the next turn is irrelevant to the conversation
state. This is why the accidental hand-off worked: the context was the
session, and the session didn't care which model was driving.
