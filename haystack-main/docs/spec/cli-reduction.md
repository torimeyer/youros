---
title: "CLI Reduction — 5 Primitives + Registry"
version: 1
status: spec
evidence: Round table 2026-03-13, 4 models, 2 rounds + attestation
participants: claude-sonnet-4.5, gemini-3-flash, grok-4.20-beta, qwen-plus
attested: sonnet, flash, qwen (3-1, grok dissents on boot)
created: 2026-03-13
implements: []
---

# CLI Reduction

> 44 commands is a wall. 5 primitives + discovery is an OS.

## Resolved

44 CLI commands reduce to 5 primitives. Everything else becomes a .language verb resolved by `do`.

| Primitive | Purpose | Unix analogy |
|-----------|---------|-------------|
| `do` | Execute intent | sh / exec |
| `get` | Read state | cat / read |
| `set` | Write state | echo > / write |
| `show` | Discover / introspect | man / apropos / ls |
| `watch` | Observe / react to events | tail -f / inotifywait |

## Minority Position

**Grok:** `boot` should be a primitive, not `do boot`. Boot is orthogonal — it brings the system into existence; all other verbs presuppose a running system. Recorded, not adopted (3-1 majority).

## How It Works

```
ostk do compile          →  resolves :compile in .language → ostk compile
ostk do spawn agent.af   →  resolves :spawn → ostk spawn agent.af
ostk do boot             →  resolves :boot → ostk boot (register dump)
ostk get .ostk/audit.jsonl  →  read file through kernel (compressed)
ostk set .ostk/config key=val  →  write through kernel (CAS)
ostk show :compile       →  (hay) → (needles) | "triage thinking into work"
ostk show verbs           →  list all .language entries
ostk show agents          →  list all Agentfiles
ostk watch audit          →  stream audit.jsonl changes
ostk watch fleet          →  stream agent state changes
```

## Migration

The 44 existing commands become aliases that route through `do`:

```rust
// ostk compile → ostk do compile
// ostk needle add → ostk do needle add
// ostk bench → ostk do bench
```

Existing commands continue to work. The 5-primitive surface is additive — no breaking changes. Over time, documentation and boot output reference the primitives. The 44 commands remain as direct shortcuts.

## Register Dump Integration

The `sys:` line in the boot register dump maps to `do` arguments:

```
sys: :test :hay :run :ask :reap :bench :promote :spawn
```

Means: `ostk do test`, `ostk do hay`, etc. The vDSO IS the hot-path `do` verbs.

## Acceptance Criteria

- [ ] `ostk do <verb> [args]` resolves through .language and dispatches
- [ ] `ostk get <path>` reads through kernel with compression
- [ ] `ostk set <path> <value>` writes through kernel with CAS
- [ ] `ostk show <query>` introspects .language, agents, needles, specs
- [ ] `ostk watch <target>` streams changes (audit, fleet, nudges)
- [ ] All 44 existing commands continue to work unchanged
- [ ] `ostk --help` surfaces the 5 primitives prominently
- [ ] Boot register dump references primitives, not raw commands
