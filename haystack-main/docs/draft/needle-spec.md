---
title: needle spec
status: draft
created_at: 2026-03-08T04:51:43Z
author: orchestrator
evidence: transcripts/discussions/meta-analysis-needle-guidance.md
---

# Needles

> A needle is a precise, pointed instruction. It has a verb, a location, and a test.
> "Fix create_file overwrite bug" is a needle. "Implement the coordination layer" is hay.

## What a Needle Is

A needle is the atomic unit of work in ostk. Not a user story. Not a ticket. Not a bead. A needle is what you hand to an agent and it comes back done.

Good needle: `Fix create_file overwrite bug in src/kernel/file.rs — error if file exists, test: create_file on existing path returns AlreadyExists`

Bad needle: `Agents should be able to create files safely`

## The Three Parts

Every needle has exactly three parts:

1. **Verb + target.** What to do and where. `Fix create_file in src/kernel/file.rs`. `Add ss tool to src/serve/dispatch.rs`. `Write integration test in tests/integration.rs`.

2. **Acceptance.** How to verify it's done. A test name, a command that passes, a behavior that's observable. `test_create_file_rejects_existing passes`. `make all exits 0`. `ostk audit check shows 0 gaps`.

3. **Context.** What to read first. File paths, function names, related needles. `Read src/kernel/file.rs create_file function. Related: bd-200`.

If you can't write all three parts, it's not a needle yet. It's hay. Refine it.

## Evidence

The meta-analysis (transcripts/discussions/meta-analysis-needle-guidance.md) found:
- Organic needles (filed from real friction): 44% close rate
- Decompose exhaust (auto-generated from spec checkboxes): 22% close rate
- The machine is 2x more effective on work it discovered itself

The difference: organic needles have verbs, locations, and tests. Decompose exhaust has sentences describing desired states.

## Needle Lifecycle

```
friction -> ostk needle add "verb + target" -> agent dispatched with context -> test passes -> ostk work close -> audit event
```

Not:
```
spec written -> ostk decompose -> 18 checkboxes -> no one works on them -> backlog grows
```

## Needle Quality Gate

Before dispatching an agent with a needle, verify:

| Check | Example |
|-------|---------|
| Has a verb? | "Fix", "Add", "Wire", "Write", "Port" |
| Has a file path? | `src/kernel/file.rs`, `tests/integration.rs` |
| Has a test? | `test_create_file_rejects_existing`, `make all` |
| Has context? | "Read src/kernel/file.rs, find create_file function" |
| Is one thing? | Not "fix bugs and add features and update docs" |

If any check fails, sharpen the needle before dispatching.

## Needles in Agentfiles

Agents receive needles through the WORK directive:

```dockerfile
FROM claude-sonnet-4-6
PROMPT file://prompts/kernel-architect.md
TOOL mish
TOOL fcp-rust
WORK tags=kernel,bugfix priority>=P0
```

The agent calls `ostk work next`. The queue returns a needle. The needle has all three parts. The agent executes. The agent calls `ostk work close`. The next needle arrives.

No orchestrator relay. No prompt engineering per task. The needle IS the prompt.

## Needle vs Decompose

`ostk decompose` is still useful for PLANNING — it shows the scope of a spec. But decomposed items are NOT ready to dispatch. They need sharpening:

| Decompose output | Sharpened needle |
|-----------------|-----------------|
| "304 read elision through full MCP path" | "In tests/integration.rs, write test_304_elision: ss_session read same file twice, first returns Full, second returns [304] with <=5 whitespace tokens. Read src/kernel/elision.rs for ReadResult enum." |
| "Tier 1 auto-merge through full MCP JSON-RPC path" | "In tests/integration.rs, write test_tier1_auto_merge: two str_replace_cas calls to lines >5 apart on same file, both succeed, file has both edits. Read src/kernel/hotpr.rs for PROXIMITY_LINES constant." |

The sharpening step is where the orchestrator adds value. It reads the code, finds the file paths, writes the test expectations. The agent executes.

## The Name

Finding the needle in the ostk. You have a massive codebase, hundreds of files, dozens of agents. The needle is the precise thing that matters right now. ostk finds it, points the agent at it, and gets out of the way.

## Acceptance Criteria

- [ ] Every needle has verb + target, acceptance test, and context
- [ ] `ostk needle add` enforces the three-part structure (warn if missing)
- [ ] `ostk work next` returns needles with all three parts
- [ ] Decompose output is labeled as "unsharpened" — not dispatchable until refined
- [ ] Agents receive needles via WORK directive, execute without orchestrator relay
- [ ] Organic needle close rate stays above 40% (tracked in metrics)
- [ ] Decompose exhaust is explicitly marked and not counted in velocity
