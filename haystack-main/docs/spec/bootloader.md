---
status: spec
version: 1
author: orchestrator
created: 2026-03-08
evidence: .ostk/boot-test-results.md (70% recovery from 1600 tokens, fresh agent dogfood)
discussion: shutdown sequence of session 2026-03-08
implements: []
---

# ostk bootloader

> The OS boots from disk, not from memory. 1600 tokens. 0.16% of context. Full orientation.

## The Problem

LLM sessions end. Context windows fill. Processes die. The agent that knew everything is gone. The next agent starts from zero — unless the OS provides a boot sequence.

## The Boot Sequence

```
power on (new claude session)
  ↓
BIOS: read CLAUDE.md + .ostk/boot.md           (~1600 tokens, what exists + vocabulary + preferences)
  ↓
page table: read .ostk/specs.json               (16 specs, 19 drafts, 226 criteria — the document landscape)
  ↓
dispatch queue: read .ostk/dispatch.json         (309 needles, 62 P0, 15 sharp — what's ready)
  ↓
register recovery: read .ostk/registers-dump.md  (volatile state from last session — what the bootloader missed)
  ↓
live calibration: run pid checks, tail logs          (what's actually running NOW)
  ↓
ready: ostk compile                              (orient and act)
```

## The Three Boot Files

### .ostk/boot.md (the swap file)
Produced by: agent reading audit.jsonl + git log + filesystem.
Contains: what exists, what's running, what's next, vocabulary, preferences.
Size: <2000 tokens. Structured, not prose.
Updated: on shutdown (register dump triggers regeneration) or on demand (`ostk compile --boot`).

### .ostk/specs.json (the page table)
Produced by: agent scanning docs/spec/ and docs/draft/ frontmatter.
Contains: every spec and draft with version, criteria count, status.
Size: ~500 tokens. JSON, machine-parseable.
Updated: on spec promotion, decompose, or on demand.

### .ostk/dispatch.json (the dispatch queue)
Produced by: agent compiling .ostk/needles/issues.jsonl.
Contains: open needles sorted by priority, sharpness, dependencies.
Size: ~1000 tokens. JSON, machine-parseable.
Updated: on needle close, new needle, or on demand.

## The Register Dump

On shutdown, the current session writes .ostk/registers-dump.md — volatile knowledge that lived only in the context window:
- Corrections the human made (preferences not yet in specs)
- Bugs discovered but not yet filed as needles
- Decisions made in conversation but not yet in docs
- State that changed since the boot files were last generated

The register dump is the gap between the bootloader and full context. It's lossy — not everything survives. The 70/30 split from dogfooding: 70% boots from structured files, 30% needs the register dump.

## Shutdown Sequence

```
1. ostk compile                    (produce final thread/needle state)
2. write .ostk/registers-dump.md   (dump volatile context)
3. regenerate boot.md                  (optional — if significant changes since last)
4. regenerate dispatch.json            (optional — if needles changed)
5. git add + commit + push             (persist to disk and remote)
6. session ends                        (registers lost, disk survives)
```

## Boot Quality Metric

Dogfood test: spawn a fresh agent with ONLY the boot files. Compare its orientation against the full-context agent's knowledge.

| Metric | Target | Achieved (v1) |
|--------|--------|---------------|
| Correct project understanding | 100% | 100% |
| Correct priority identification | top 3 match | 2/3 match |
| Knows what's running | accurate | accurate (found nothing running — correct) |
| Knows vocabulary | all terms | all terms |
| Knows preferences | critical ones | partial (missed calibrate, corrections) |
| Tokens consumed | <2000 | ~1600 |
| Context used | <0.5% | 0.16% |

## What the Bootloader Does NOT Replace

- The full audit trail (boot.md is a summary, not the log)
- The offload prompts (domain knowledge, loaded via PROMPT file:// in Agentfiles)
- The spec contents (specs.json is the index, not the text)
- The codebase itself (the agent still reads source files as needed)

The bootloader orients. The filesystem provides depth on demand.

## Acceptance Criteria

- [ ] Fresh agent reads 3 boot files and orients in <2000 tokens
- [ ] boot.md regenerated from structured state (audit + git + filesystem), not from memory
- [ ] specs.json updated on spec promotion and decompose
- [ ] dispatch.json updated on needle close and creation
- [ ] registers-dump.md written on shutdown with volatile knowledge
- [ ] Shutdown sequence: compile → dump → regenerate → commit → push
- [ ] Boot quality metric: >80% orientation accuracy from boot files alone
- [ ] Register dump covers the gap to >95% with boot files combined
- [ ] `ostk compile --boot` regenerates all boot files on demand
- [ ] Boot files are structured (parseable), not prose
