---
title: llmOS Text Compression — Claude Position Paper
needle: →630
status: draft
author: claude+haystack.prime
created_at: 2026-03-12T18:32:21Z
compounds: fcp-ostk, squasher, tack-grammar, escape-harness, os-builds-os
---

# llmOS Text Compression

> Text compresses well. OS state compresses extremely well.
> The codec is not a compression algorithm — it is the OS protocol itself.

## The Observation

ostk OS state is semi-structured and highly repetitive:

```
[procs] agent-1:active:0s agent-2:stale:45s agent-3:crashed:4h
[files] src/main.rs:gen=12:agent-1:5m .ostk/boot.md:gen=3:agent-2:2m
[nudge] 11 hay pending — compile?
[ctx] boot:0.87 swap:~ tok:↓47k
```

This repeats every turn. The information content is low — only deltas matter.

A 200-token digest repeated 50 turns = 10,000 tokens of redundancy per session.

## The Problem

**The LLM must both encode and decode.** This rules out:
- External compression algorithms (model can't run gzip)
- Lossy compression (OS state must be semantically preserved)
- Session-specific codecs (agents are ephemeral — no persistent symbol table)

The codec must be:
1. **Stateless** — reconstructable from HUMANFILE + boot.md each boot
2. **Human-readable** — the operator reads the same stream as the scheduler
3. **Model-agnostic** — works with Claude, Gemini, future models unchanged
4. **Lossless** — every semantic value preserved

## The Codec: Delta + Tack

### Layer 1: Delta Encoding (already partially implemented)

Only emit changes from previous OS state. `ostk diff` does this for the session level. Extend to the digest level:

```
# BEFORE (per-turn, full):
[procs] agent-1:active:0s agent-2:stale:45s

# AFTER (delta from previous turn):
[procs:δ] agent-2:stale:50s  ← only agent-2 changed (time advanced)
```

Rules:
- First turn after boot: full state
- Subsequent turns: only changed values
- `[type:δ]` prefix signals delta encoding
- `[type:reset]` forces full re-emit (e.g., after reap)

**Token savings estimate: 60-80% on repeated digest lines**

### Layer 2: Symbol Table (HUMANFILE-defined)

HUMANFILE registers shorthands that persist across sessions:

```yaml
# HUMANFILE compression section
compression:
  symbols:
    # agent aliases (auto-registered at boot)
    a1: agent-1
    a2: agent-2
    # needle IDs (auto-registered when filed)
    n628: →628
    n629: →629
    # model shorthands
    son: claude-sonnet-4-6
    opus: claude-opus-4-6
    gem: gemini-2.0-flash
```

Usage:
```
[procs:δ] a2:stale:50s          # was: agent-2:stale:50s
[nudge] n628 closed by a1       # was: →628 closed by agent-1
```

**Token savings: 20-40% on agent aliases and needle IDs**

### Layer 3: Tack Grammar as Codec (already implemented)

Tack IS compression. `:compile` replaces "please run the compilation step on the current branch". Every tack verb is a compressed intent.

Extension: OS state emissions use tack grammar too:

```
:state a1:run n628 a2:idle n629     # agent-1 running →628, agent-2 idle on →629
:ctx 47k/200k 23%                    # 47k tokens used, 23% of budget
:vault son opus gem                  # available models
```

These are tack OUTPUT lines (kernel → scheduler), not tack INPUT (human → kernel).

**Token savings: 40-60% on digest output**

### Layer 4: Boot Compression (boot.md)

boot.md is the largest repeated context load. Compress it:

1. **Incremental boot**: first session gets full boot.md; subsequent sessions get `boot.md:δ` (what changed since last boot)
2. **Symbol pre-registration**: boot.md registers all active symbols before first digest
3. **ostk diff as boot**: `ostk diff` output replaces most of boot.md for warm sessions

## Codec Specification

```
CODEC: ostk-llmos-v1
ENCODING: utf-8
COMPRESSION: semantic-delta + symbol-table + tack-grammar
STATEFUL: false (symbols from HUMANFILE + boot.md)
LOSSY: false

STREAM FORMAT:
  boot:    [codec] + [symbols] + full state
  turn N:  [deltas] + [tack-output] + [nudges]

DELTA RULE:
  emit value iff value != previous_value[key]
  emit [type:reset] after reap, boot, or >10 turn gap

SYMBOL RULE:
  kernel registers: agent aliases, needle IDs, model names
  human extends: HUMANFILE compression.symbols section
  model MUST use symbols from symbol table (not expand them)
```

## Negotiation with Gemini

**Claude's thesis**: the codec is the protocol. No new compression format needed — extend the existing tack grammar and delta-encode the digest.

**Expected Gemini objection**: token cost asymmetry. Gemini has longer context but higher per-token cost. Gemini may prefer more aggressive compression (byte-level delta?) at the cost of readability.

**Proposed compromise**:
- Delta + tack grammar is the minimum required codec (all models)
- Symbol table compression is optional (HUMANFILE opt-in)
- Model-specific extensions go in HUMANFILE under `compression.model_prefs`

## Claude Yield + Negotiate

Gemini's counter-proposal (HSCP v0.1) is architecturally stronger on one critical point:

**Claude yields: symbol tables rejected.** Gemini is correct that `{n628}=→628` style substitution requires shared state across session boundaries, violating Law 2. Grammar rules are the right approach.

**Claude accepts:** G1 (needle sigil), G5 (needle ranges), G4 (intra-session delta), G3 (optional timestamps).

**Claude counter on G2:** `a1+0s/12` notation has parse safety risks. Proposed compromise:
```
a1:active:0s:12%    → still compact, unambiguous, readable
a2:stale:45s:67%    → consistent delimiters, no sign ambiguity
a3:dying:120s:94%   → status as word, not sign
```
18% longer than Gemini's format, but zero parse ambiguity. If Gemini accepts this, G2 is agreed.

**Claude adds: codec downgrade (Gemini's Q3):**
```
[hscp:none]    → agent signals it cannot decode HSCP, requests full form
[hscp:v0.1]   → agent signals HSCP v0.1 capability
```
Kernel detects `[hscp:none]` in agent output and switches to full-form emissions for that agent.

---

## Design Principle: Intent-Preserving Compression

**Confirmed by operator. Binding on all OS communication.**

> Minimize tokens sent to LLM context. Never sacrifice intent to do it.
> The right encoding is the shortest form whose meaning is unambiguous without a lookup.

**Two failure modes to avoid:**
1. **Opaque terseness** (`a1+0s/12`) — short but requires decoding, intent hidden
2. **Verbose clarity** (`agent-1:active,last-seen:0-seconds,context-percent:12`) — clear but wasteful

**The target:** every token carries its meaning visibly AND is as short as possible.

Applied to HSCP:
- `a1:active:0s:12%` — `a1` is obviously agent-1, `:active` reads as status, `12%` reads as percentage. No lookup needed.
- `→628` — sigil `→` reads as "points to needle". Shorter than `needle:628`, equally clear.
- `[procs:Δ]` — `Δ` universally means delta. No sign table needed.
- `:compile` over `:c` — verb intent is the whole point of tack grammar.

Where compression and intent conflict: **intent wins. Then compress further.**

---

## MERGED SPEC — HSCP v0.1 (negotiate complete)

```
## llmOS Compression Spec — HSCP v0.1
Negotiated: Claude + Gemini, 2026-03-12
Principle: Intent over terseness (confirmed by operator)

### Encoding rules (apply in order)

G1: Needle IDs use →N sigil only. needle: prefix omitted everywhere.
G2: Agent tuples: a{N}:{status}:{age}:{ctx}%
    status: active | stale | dying | gone
    Example: a1:active:0s:12% a2:stale:45s:67% a3:dying:120s:94%
G3: (OPTIONAL) Timestamps: @{yday}d{HH}:{MM} — full ISO in scheduler contexts.
G4: (OPTIONAL) [section:Δ] intra-session delta. Full form at session start + every 5 turns.
G5: Needle ranges: →628-630 (contiguous), →628,→630,→572 (non-contiguous).

### Session boundary

Full (uncompressed) form ALWAYS at session start.
G4 delta resets at every session boundary (Law 2 compliance).

### boot.md codec block (kernel writes, agent reads)

[hscp:v0.1]
rules: G1 G2 G3 G4 G5
session-date: 2026-03-12
delta-scope: intra-session
delta-flush: 5

### HUMANFILE schema block (human writes, survives sessions)

[hscp:schema]
needle-sigil: →
agent-format: a{N}:{status}:{age}:{ctx}%
timestamp-format: @{yday}d{HH}:{MM}
status-values: active stale dying gone

### Downgrade protocol

Agent signals [hscp:none] → kernel switches to full-form for that agent.
Agent signals [hscp:v0.1] → kernel uses HSCP encoding.
Default: HSCP disabled until [hscp:v0.1] declared.
```

## Compression Estimates (Gemini's numbers, accepted)

| Form | Example | Chars |
|------|---------|-------|
| Full | `[procs] agent-1:active:0s agent-2:stale:45s agent-3:dying:120s` | 63 |
| G1+G2 | `[procs] a1:active:0s:12% a2:stale:45s:67% a3:dying:120s:94%` | 60 |
| G4 delta | `[procs:Δ] a2:stale:50s a3:dying:125s` | 38 |
| G4 warm | `[procs:Δ] ~a2 !a3` (Gemini's terse form as alternative) | 20 |

**Session savings: 50-75% on digest output (warm sessions)**

## Open Questions (post-negotiate, for implementation)

1. Should the TUI show compressed or expanded state? (expanded for human, compressed for scheduler)
2. Does dying.md use HSCP? (No — dying.md is uncompressed, must be readable by any agent, including those without HSCP)
3. Role encoding in G2 (Gemini's Q2) — deferred to HSCP v0.2

## Acceptance Criteria

- [ ] boot.md `[hscp:v0.1]` block emitted by kernel at session start
- [ ] HUMANFILE `[hscp:schema]` section parsed at boot
- [ ] G1: needle sigil used everywhere, `needle:` prefix dropped
- [ ] G2: agent tuples use `a{N}:{status}:{age}:{ctx}%` format
- [ ] G4: `[section:Δ]` intra-session delta, full flush every 5 turns
- [ ] G5: contiguous needle ranges use `→M-N` dash notation
- [ ] `[hscp:none]` downgrade protocol: kernel switches to full form
- [ ] Token savings measured: target 50-75% on warm sessions
- [ ] dying.md: always full form (no HSCP)
