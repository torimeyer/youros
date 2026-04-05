---
version: 1
evidence: RTX3 debate — 3 rounds, converged on four-layer model
promoted_at: 2026-03-13T01:25:45Z
prior_art: docs/draft/intent-dynamic-programming.md, docs/spec/tack-grammar.md
title: Dynamic Userspace OS — tack atrophy/rise via .language
author: scottmeyer + claude-opus-4-6 + gemini-2.5-pro
status: spec
created: 2026-03-10
implements: []
---

# Dynamic Userspace OS

> The OS is exactly and only the compiled residue of intent. It becomes what you do. It forgets what you stop doing.

## The Four-Layer Model

```
Layer 0: Kernel primitives     sh_run, gen_table, Hot PR, heartbeat, identity
         IMMUTABLE. Not in .language. Hardware interface.

Layer 1: Kernel safety verbs   :boot :shutdown :halt :verify
         IMMUTABLE in .language. Kernel-invoked, not user-facing.
         If :halt decays, the safety mechanism decays. Correctness failure.

Layer 2: Ceremonial verbs      :negotiate :attest :confirm :sign
         HIGH INERTIA. half_life=10000. Decays to shadows/ not deleted.
         Revival requires Resurrection Hash + Probation.

Layer 3: Userspace verbs       :pitchfork :ultrathink :calibrate :emerge :compounds
         FULL DYNAMICS. Atrophies unused. Rises on successful pattern match.
         half_life=50-200 depending on verb class.

Layer 4: Session verbs         Typed once, never repeated.
         EPHEMERAL. Never minted to .language unless momentum threshold exceeded.
```

## .language File Format

```
# .language — compiled tack dialect
# verb | tier | layer | last_gen | half_life | momentum | resolution

:boot       | 1 | kernel  | -    | ∞     | -    | ostk boot
:halt       | 1 | kernel  | -    | ∞     | -    | ostk shutdown --emergency
:negotiate  | 1 | ceremony| 2847 | 10000 | 0.92 | ostk negotiate
:pitchfork  | 2 | user    | 2901 | 100   | 0.85 | ostk show --semantic $1
:ultrathink | 3 | user    | 2851 | 50    | 0.40 | [LLM inference: deep analysis]
:bg         | 2 | user    | 2902 | 200   | 0.71 | [intent signal: background]
:calibrate  | 2 | user    | 2788 | 150   | 0.33 | ostk compile --retriage
```

**momentum** = usage_frequency × success_rate × recency_weight (0.0-1.0)
- momentum > 0.8 → promote toward tier 1
- momentum < 0.2 → demote toward tier 3, eventual shadows/
- momentum = 0 → move to shadows/ at next shutdown

## fcp-ostk as Intent MMU

fcp-ostk sits at the boundary between userspace tack and kernel primitives.

```
User types: ":pitchfork HUMANFILE tack"
                    ↓
fcp-ostk Intent MMU:
  1. Read .language lookup table (O(1), not LLM context)
  2. Tier 1 hit? → execute directly (no LLM)
  3. Tier 2 hit? → pattern expand, execute
  4. Tier 3 fallback? → pass to LLM for inference
  5. Record: verb, tier used, success/fail → audit.jsonl
  6. Update: last_gen in gen_table for this verb
```

**On invocation (Lazy Decay):**
```
check: current_gen - verb.last_gen > decay_threshold?
  YES → apply momentum penalty before execution
  NO  → execute normally
```

**Context bloat concern (resolved):** .language is a lookup table — not injected into LLM context raw. OSTK_INSTRUCTIONS / --agents injects only the ACTIVE subset (→584, →585). .language can grow indefinitely without context impact.

## Atrophy Mechanism

**Lazy decay** — calculated at invocation, not at shutdown:
```
decay_rate = half_life / (current_gen - last_gen)
momentum_penalty = 1 - (1/decay_rate)
new_momentum = old_momentum * (1 - momentum_penalty)
```

**Tier demotion** — at shutdown checkpoint:
```
momentum > 0.8 AND tier > 1 → promote: tier--
momentum < 0.2 AND tier < 4 → demote: tier++
momentum = 0.0 → move to shadows/
```

## Rise Mechanism

**Successful tier-3 resolution → evidence for promotion:**
```
audit.jsonl shows:
  tack:tier3_resolved for verb V
  no HUMANFILE correction within N heartbeats
  
→ V.momentum += rise_delta
→ if V.momentum > tier2_threshold: mint to tier 2 in next shutdown
```

**Shutdown minting ceremony:**
```
1. Read session audit.jsonl delta
2. For each tack event: update verb momentum
3. Tier promotions/demotions applied
4. Zero-momentum verbs → shadows/
5. New verbs from session → .language if momentum > mint_threshold
6. Checkpoint written, signed (for Layer 2+)
```

## shadows/ Cold Storage

Decayed verbs move to `.ostk/shadows/` — not deleted.

```
shadows/negotiate-2847.lang    ← frozen snapshot with gen when it decayed
shadows/pitchfork-1200.lang    ← an older :pitchfork that was superseded
```

**Revival ceremony (:revive <verb>):**
1. Operator issues `:revive :negotiate`
2. Kernel checksums shadows/ entry against .primefile manifest
3. Hash matches → signed `:confirm` sufficient
4. Hash missing or .primefile rotated → full GPG re-attestation required
5. Revived verb returns to .language at **Layer 4 half-life** (probation)
6. Must earn inertia through usage to regain original layer status

## Shutdown as Minting Ceremony

"The language minted the kernel. Shutdown IS the minting ceremony."

Every shutdown:
1. Reads session audit.jsonl (O(session_events), not O(.language_size))
2. Updates momentum for verbs used this session
3. Applies tier promotions/demotions
4. Moves zero-momentum verbs to shadows/
5. Mints new high-momentum session verbs into .language
6. Writes checkpoint (signed for Layer 2+ changes)

This is the `dp[i] = solution` step from intent-dynamic-programming.md.
Next boot's `lookup dp[i-1]` reads the updated .language.

## Acceptance Criteria

- [ ] .language file created with correct schema (verb|tier|layer|last_gen|half_life|momentum|resolution)
- [ ] fcp-ostk applies lazy decay on invocation
- [ ] Shutdown compacts audit delta into .language (O(session_events))
- [ ] Zero-momentum verbs move to shadows/ at shutdown
- [ ] :revive command implements Resurrection Hash + Probation
- [ ] --agents injects active .language subset dynamically
- [ ] OSTK_INSTRUCTIONS injects active .language at MCP init
- [ ] Layer 0-1 verbs bypass decay entirely
- [ ] Layer 2 verbs use 10000 half-life (100x user verbs)
- [ ] Momentum coefficient measurably rises for successfully used tier-3 verbs
