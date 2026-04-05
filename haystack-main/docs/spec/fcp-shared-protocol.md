---
version: 1
author: scottmeyer + orchestrator
created: 2026-03-09
evidence: this session. Tack emerged from interaction. Human and LLM converged on the same protocol without design. The protocol IS the interface.
promoted_at: 2026-03-15T04:54:49Z
status: spec
implements: []
---

# fcp: The Shared Protocol

> The human and the LLM speak the same language. That's the whole design.

## The Discovery

This session, Scott typed `:compile → :ship → :delegate`. No translation was needed. The LLM parsed it and executed. Scott typed `.? where's TUI` and got an answer. Scott typed `:calibrate months` and the LLM corrected itself.

Tack wasn't designed. It was typed into existence. Every token was used before it was documented. The human and the LLM converged on the same protocol through interaction — not through specification.

## What fcp Actually Is

fcp is not a translation layer between human and LLM. They don't need one. They're both CPUs on the same bus, and they already speak the same language.

fcp is the protocol layer between the CPUs and the devices:

```
Human (CPU1) ──┐                          ┌── rust-analyzer
               ├── ostk (kernel/bus) ──┤── pylsp
LLM (CPU2)  ──┘                          ├── drawio
                                          ├── filesystem
                                          └── any domain device
```

Both CPUs issue the same calls. Both see the same responses. The device doesn't know — or care — which CPU is calling.

## How Protocols Emerge

The protocol between human and LLM emerged from pattern convergence:

1. **Session 1:** Human uses full sentences. LLM uses full paragraphs. 200 tokens per operation.
2. **Session N:** Human uses tack (`:ship`). LLM responds with action. 10 tokens per operation.
3. **The protocol stabilizes:** Both sides agree on the grammar. `:` = command. `.?` = query. `→` = flow. `::` = escalate.

This is exactly how Unix protocols work. HTTP started as "GET /path" and a document came back. Over decades, both sides (client and server) converged on the same vocabulary (methods, headers, status codes). The protocol emerged from usage patterns, then was documented.

fcp captures this: a device driver encodes the protocol that emerged between the CPUs and a specific domain. fcp-rust encodes how both human and LLM talk to rust-analyzer. The protocol is the same for both callers.

## The Protocol Properties

1. **Symmetric.** Human can call `rust_query("find references to Foo")`. LLM can call `rust_query("find references to Foo")`. Same call, same result.

2. **Pattern-based.** The protocol is shaped by the domain, not by the caller. Rust has types, lifetimes, traits. The protocol speaks types, lifetimes, traits. Not "human-readable Rust" or "machine-readable Rust" — just Rust.

3. **Convergent.** Over time, both CPUs optimize their usage of the protocol. The human compresses (`:compile` not "please compile the hay into needles"). The LLM compresses (executes, doesn't explain). They meet in the middle.

4. **Emergent.** Nobody designed tack. Nobody designed the specific way this session's communication works. It emerged from the interaction and then was documented. Design follows usage, not the reverse.

## fcp as Protocol Capture

Each fcp-* driver captures a converged protocol:

| Driver | Domain | Protocol |
|--------|--------|----------|
| fcp-rust | Rust tooling | rust_session, rust_query, rust (verbs from rust-analyzer) |
| fcp-python | Python tooling | python_session, python_query, python (verbs from pylsp) |
| fcp-drawio | Diagrams | drawio_session, drawio_query, drawio (verbs from diagram manipulation) |
| fcp-human (future) | Human preferences | The humanfile — compiled from correction patterns |
| fcp-tack (future) | Session communication | The tack grammar — compiled from interaction patterns |

The device driver IS the protocol specification. When you write an fcp driver, you're documenting "here's how intelligence talks to this domain."

## Why This Matters

If the human and LLM speak the same language, then:

1. **Tools don't need human mode and machine mode.** One interface. Both callers.
2. **The OS doesn't need to translate between them.** It schedules, arbitrates, and multiplexes. But it doesn't translate.
3. **New devices are just new protocols.** fcp-midi, fcp-spreadsheet, fcp-3d — each captures a domain protocol that both CPUs can speak.
4. **The protocol is the product.** Not the kernel. Not the tools. The protocol that lets any intelligence talk to any domain through the same interface.

## The Unix Parallel

Unix didn't succeed because of the kernel. It succeeded because of the interface: everything is a file, read/write/ioctl. Any process can talk to any device through the same syscalls. The kernel is invisible. The interface is everything.

ostk succeeds if fcp becomes that interface. Any intelligence (human, LLM, future model) talks to any domain (code, diagrams, data, hardware) through fcp. The kernel is invisible. The protocol is everything.

## Acceptance Criteria

- [ ] `@gemini.prime` attestation verified and published
- [x] Tack grammar unified across all presentation layers (TUI, llmOS)
- [x] Real-time FCP resolution active over kernel socket
