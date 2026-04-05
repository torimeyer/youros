---
title: Boot Compression — Architecture-Adaptive Kernel Output
version: 1
status: spec
evidence: Round table 2026-03-13, 6 models across 3 vendors, 2 rounds + attestation
participants: claude-sonnet-4.5, gemini-3.1-pro, gemini-3-flash, grok-4.20-beta, gemini-2.5-pro, qwen-plus
moderator: "@haystack.prime+1111"
attested: claude-sonnet-4.5, gemini-3-flash, grok-4.20-beta, qwen-plus
created: 2026-03-13
implements: []
---

# Boot Compression

> boot.md is the constitution. .language is the judiciary. The kernel is the executor.
> — qwen-plus, round table R1

## Resolved

1. **boot.md is immutable.** Governance-bound, cryptographically singular, same file for all architectures. One truth. One signature chain. No per-model variants.

2. **The kernel mediates transmission.** `ostk boot` outputs a compressed register dump adapted to the detected model architecture at runtime. The file doesn't change. The output does.

3. **.language is the adaptation layer.** Verb momentum, half-life, and tier metadata drive per-architecture dispatch. Terse models receive high-momentum verbs with minimal expansion. Broad models receive compound-aware layered dispatch. Surgical models receive precision modifiers with targeted context windows.

## Minority Position

**Grok (recorded, not adopted):** Adaptation strictly in userspace interpretation. The kernel never interprets boot.md differently. The model walks the same map with its native gait.

*Grok attested the consensus despite dissenting.*

## Implementation

```
ostk boot
  → detect architecture (model name, context window, harness type)
  → read boot.md (immutable tack)
  → read .language (adaptation table)
  → compress output per detected profile:
      terse:    ~50 tokens  (registers only)
      standard: ~100 tokens (registers + active work)
      broad:    ~200 tokens (registers + work + lineage)
  → output to stdout (the LLM's context)
```

## Register Dump Format

```
@haystack.prime+N | vX.Y.Z | POST N/7
wall: ISO8601 | audit: N | needles: N open
.language: N verbs | fleet: N alive
P0: →NNN description
laws: invisible-write | ephemeral | filesystem | OCC | invisible-infra
```

## Acceptance Criteria

- [ ] `ostk boot` detects model architecture from environment
- [ ] Output compressed per profile (terse/standard/broad)
- [ ] boot.md file never modified by compression — read-only
- [ ] .language momentum drives verb selection in output
- [ ] POST results included in register dump
- [ ] `ostk clock` output merged into boot registers
- [ ] Profile selection audited in audit.jsonl
