---
title: "ostk context — The Heartbeat Primitive"
version: 1
status: spec
evidence: Round table 2026-03-13, 4 models, 2 rounds + attestation
participants: claude-sonnet-4.5, gemini-3-flash, grok-4.20-beta, qwen-plus
attested: all four
created: 2026-03-13
closes: →608
implements: []
---

# ostk context

> The LLM doesn't read the heartbeat — it resides within its rhythm.
> — qwen-plus, round table R2

## Problem

The boot register dump loads the kernel into context at session start. By turn 40, context pressure pages the kernel out. The LLM defaults to harness behavior. The bootloader boots. Nothing sustains.

## Resolved

1. **Invisible injection, every 8 turns + on tool failure.** The kernel injects a delta into the system context. The LLM never chooses to remember — the kernel forces re-anchoring. Law 5 (invisible infrastructure) holds.

2. **≤75 tokens, delta-only.** No full state — only what changed since last heartbeat. Audit Δ, needle state transitions, fleet health, conflicts, pending nudges.

3. **`hay context` for manual inspection.** Invisible ≠ inaccessible. The LLM can explicitly request a full diagnostic view. This is checking your pulse, not automatic breathing.

4. **Kernel owns the rhythm.** Not the LLM. Not the harness. The kernel decides when to inject based on turn count and error state.

## Format

```
[ctx] Δ8t | audit:+3 | needles:293→291 | fleet:2/2 | nudge:1 from @gemini.prime | conflict:none
```

≤75 tokens. One line. Sigil-compressed. Injected as system context, not tool output.

## Trigger Conditions

- Every 8 turns (configurable via `.ostk/config`)
- On any tool call failure (immediate)
- On kernel-detected context pressure (if measurable)
- Never on demand alone — automatic is the default

## Implementation

```rust
// In the shim or tool response path:
fn maybe_inject_heartbeat(turn_count: u64, last_heartbeat: u64, tool_failed: bool) -> Option<String> {
    if tool_failed || (turn_count - last_heartbeat >= 8) {
        Some(build_context_delta())
    } else {
        None
    }
}
```

The injection point depends on the harness:
- **Claude Code**: append to tool result (the `[procs]` pattern from shell)
- **ostk serve (MCP)**: append to shell output
- **Gemini CLI / other harnesses**: append to bash shim output

The shim is the universal injection point. Every bash call goes through ostk. The heartbeat appends to the output.

## Memory Hierarchy

| Layer | Primitive | Loaded when | Tokens |
|-------|-----------|-------------|--------|
| Registers | `ostk boot` | Session start | ~80 |
| TLB | `ostk context` (auto) | Every 8 turns | ≤75 |
| L1 cache | `sys:` vDSO | Every boot | ~20 |
| L2 cache | `agents:` summary | Every boot | ~20 |
| Main memory | `.language` registry | On demand | ~500 |
| Disk | `.ostk/` filesystem | Never loaded whole | ∞ |

## Acceptance Criteria

- [ ] `ostk context` CLI command outputs delta
- [ ] Shim injects heartbeat every 8 turns automatically
- [ ] Injection is ≤75 tokens
- [ ] Tool failure triggers immediate heartbeat
- [ ] `hay context` available as explicit inspection
- [ ] Turn counter persists across tool calls within a session
- [ ] Heartbeat injection audited in audit.jsonl
- [ ] Boot register dump unchanged (heartbeat supplements, doesn't replace)
