---
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
implements: []
---

# ostk compile — The Intelligence Layer

> Hay goes in. Needles come out. The compiler IS the intelligence layer.

## The Stack

```
HUMAN         throws hay        ~ some-idea
COMPILER      sharpens          ostk compile -O2
NEEDLE        ready             → some-idea (verb, file, test)
SCHEDULER     dispatches        ostk run agent.af (pulls → needle)
CPU           executes          LLM runs the needle
KERNEL        coordinates       Hot PR, 304, digest, nudge
CONSOLE       observes          operator sees savings
AUDIT         attributes        ostk trace → some-idea
```

## Two States, One Stack

**Hay** `~` — captured intent. No structure. Ideas, notes, friction, futures.
```
$ ostk hay "fix the overwrite thing"
~ fix-the-overwrite-thing
```

**Needle** `→` — executable instruction. Verb, file, test. Ready for dispatch.
```
→ fix-the-overwrite-thing
  verb: Add exists() guard to create_file
  file: src/kernel/file.rs
  test: test_create_file_rejects_existing
  context: FileError::AlreadyExists variant needed
```

## The Compiler

`ostk compile` transforms hay into needles by reading `.ostk/` state.

### Inputs
- The hay item (raw human text)
- `.ostk/gen_table.jsonl` — recently edited files
- `.ostk/audit.jsonl` — recent events
- `.ostk/needles/issues.jsonl` — related work
- `.ostk/sessions/` — what agents have done
- The codebase itself (file contents, function signatures)

### Outputs
- A needle with all three parts: verb + file + test
- Context: related needles, recent edits, relevant code locations
- Confidence: how sure the compiler is about the sharpening

### Compiler Feedback

Every compilation returns a status:

```
SUCCESS     → needle produced with verb, file, test, context
              ready for dispatch

WARNING     → needle produced but missing context
              "no spec or draft found for this area — needle may be orphaned"
              "similar needle exists: → fix-overwrite-bug (duplicate?)"
              "no test expectation inferred — add manually"

ERROR       → cannot compile
              "no verb detected — what do you want to DO?"
              "no file/codebase match — where does this belong?"
              "intent is ambiguous — did you mean X or Y?"
```

Warnings produce a needle anyway — it's just flagged. Errors require human refinement. The compiler tells you exactly what's missing.

```
$ ostk compile ~ vague-thought
ERROR: no verb detected
  hay: "something about auth"
  hint: what do you want to DO? (fix, add, write, refactor, test?)

$ ostk compile ~ better-thought  
WARNING: no spec found
  → better-thought (verb: add, file: src/auth.rs, test: inferred)
  note: no spec covers auth — consider: ostk draft "auth system"

$ ostk compile ~ sharp-thought
SUCCESS
  → sharp-thought (verb: fix, file: src/kernel/file.rs:184, test: test_overwrite_guard)
  context: related → fix-create-file (closed), spec: sprint-5-launch-plan
```

### Optimization Levels

**-O0: Pass-through.** Hay stays hay. No compilation. For when you want to capture without sharpening.

**-O1: Cleanup + context.** Fix spelling/grammar. Append relevant `.ostk/` state. The hay becomes annotated hay.
```
~ fix the overwrite thing
  [context] src/kernel/file.rs:create_file uses fs::write (silent overwrite)
  [context] related: → wire-nudge-dispatch (same file)
  [context] last edited by: agent-1, 5m ago
```

**-O2: Needle generation.** Full compilation. The compiler reads the codebase, identifies the exact file and function, writes the verb and test expectation. Produces a dispatchable needle.
```
→ fix-create-file-overwrite
  verb: Add path.exists() guard before fs::write in create_file()
  file: src/kernel/file.rs:184
  test: test_create_file_rejects_existing — create_file on existing path returns AlreadyExists
  context: add AlreadyExists variant to FileError enum
```

**-O3: Multi-needle decomposition.** Complex hay compiled into ordered needles with dependencies.
```
~ "refactor the auth system"
  → auth-extract-middleware        (depends: nothing)
  → auth-jwt-validation           (depends: → auth-extract-middleware)
  → auth-refresh-token             (depends: → auth-jwt-validation)
  → auth-integration-test          (depends: all above)
```
The compounding order is computed, not planned.

## The Compiler Implementation

A haiku call ($0.001) + local state lookup (free):

```
1. Human throws hay: "fix the overwrite thing"
2. ostk reads .ostk/ state:
   - gen_table: src/kernel/file.rs last edited 5m ago
   - audit: create_file called 3 times in last session
   - needles: no open needle for this file
   - code: file.rs:184 create_file() uses fs::write
3. haiku call: "Given this hay and this context, produce a needle"
4. Result: → fix-create-file-overwrite with verb, file, test
5. Human reviews (optional): "looks right" or "no, I meant the OTHER overwrite"
```

Cost: <$0.01 per compilation. The savings in LLM interpretation tokens dwarf it.

## The Symmetry

```
OUTPUT compression:  raw terminal (240K) → squasher → compressed (77K)
INPUT compilation:   raw human (14 words) → compiler → needle (200 tokens)

The LLM operates in a compressed, unambiguous bubble.
Inputs are pre-compiled. Outputs are post-compressed.
```

## ostk hay

```
$ ostk hay "some idea"
~ some-idea

$ ostk hay "another thought about auth"
~ another-thought-about-auth

$ ostk hay list
~ some-idea                          (uncompiled)
~ another-thought-about-auth         (uncompiled)
→ fix-create-file-overwrite          (compiled, ready)
→ wire-nudge-dispatch                (compiled, in progress)
```

## ostk compile

```
$ ostk compile ~ some-idea
Compiling ~ some-idea...
  reading .ostk/ state...
  found: src/lib.rs:47 related function
  found: → related-needle (similar scope)

→ some-idea
  verb: ...
  file: ...
  test: ...
  context: ...

Accept? [y/n/edit]
```

The `Accept?` prompt is the human review gate. -O3 shows multiple needles and asks for confirmation of the dependency order.

## ostk compile --auto

For autonomous operation, skip the review:
```
$ ostk compile --auto ~ some-idea
→ some-idea (compiled, queued)
```

Agents running with `WORK` directives pull compiled needles automatically.

## ostk intelligence — Silent Clustering

The compiler doesn't just sharpen individual hay. It sees the PILE.

Humans throw hay over hours, days, sessions. They don't remember half of it. The intelligence layer silently clusters by semantic similarity and surfaces patterns the human didn't plan.

### How it works

On every `ostk hay list`, the intelligence layer:
1. Reads all hay items (~ state)
2. Groups by semantic similarity (haiku call, <$0.01)
3. Names each cluster with a discovered theme
4. Shows unclustered hay separately

### What the human sees

```
$ ostk hay list

Clusters (discovered):

  ONBOARDING (4 hay)
    ~ first-run-experience
    ~ per-tool-interstitial
    ~ things-will-look-different
    ~ ego-manifesto

  PORTABILITY (3 hay)
    ~ testcontainers-harness
    ~ windows-compat
    ~ shim-intercept-all-tools

  UNCLUSTERED (2 hay)
    ~ firecracker-delivery
    ~ paper-2-infrastructure
```

The human glances and sees: "those four ideas are one feature." They didn't plan it. The pile revealed it.

### Compile a cluster

```
$ ostk compile --cluster ONBOARDING
Compiling 4 hay into ordered needles...

→ onboarding-agents-guide        (verb: rewrite, file: src/cli/agents.rs)
→ onboarding-per-tool-hints      (verb: add, file: src/serve/dispatch.rs, depends: → onboarding-agents-guide)
→ onboarding-first-run-flow      (verb: implement, file: src/main.rs, depends: → onboarding-per-tool-hints)
→ onboarding-manifesto-page      (verb: write, file: docs/manifesto.md, depends: nothing)

Accept? [y/n/edit]
```

Four scattered ideas become four ordered needles with dependencies. The compounding order is discovered from the cluster, not imposed by a human.

### The dogfooding proof

This session: 275 hay thrown over 6 hours. Scattered across conversations, commits, drafts. If intelligence existed, it would have shown:
- "You've been designing a compiler" (6 hay from different angles = one feature)
- "You've been thinking about onboarding" (4 hay = one user flow)
- "These 3 naming ideas converge" (beads -> needles -> → prefix = one identity)

The human didn't organize. The pile organized itself.

### Intelligence runs

| Trigger | Cost | What |
|---------|------|------|
| `ostk hay list` | <$0.01 | Cluster on demand |
| `ostk compile --cluster X` | <$0.05 | Compile cluster to ordered needles |
| Background (every 30m) | <$0.01 | Re-cluster as new hay arrives |
| `ostk console` | free | Display cached clusters |

### What intelligence does NOT do

- Does not auto-compile hay into needles (human or --auto flag required)
- Does not delete or merge hay items (append-only, fifth law)
- Does not prioritize (that's the human's job or the WORK directive)
- Does not execute (that's the CPU/LLM)

Intelligence DISCOVERS structure. Humans DECIDE what to do with it.

## Thread Calibration

`ostk calibrate <thread>` surfaces the full state of a thread: hay, needles, evidence, dependencies, gaps, and the number that matters.

### Example: `ostk calibrate SWE`

```
thread: SWE

hay:
  ~ swe-bench-baseline-v18
  ~ swe-prompt-injection-working
  ~ swe-two-tools-coordination-drag
  ~ swe-want-single-binary
  ~ swe-silent-treatment-idea
  ~ swe-bench-is-ostk-bench
  ~ swe-dockerfile-agentfile-pattern
  ~ swe-solve-this-docker-image

needles:
  → bench-fork-mini-swe
  → bench-ci-musl-binary
  → bench-find-prompt-injection
  → bench-replace-injection
  → bench-v19-control
  → bench-v19-treatment-injected
  → bench-v19-treatment-silent
  → bench-v19-score
  → bench-runner-skill
  → bench-kickoff-v19

evidence:
  v18 report: results/v18_report.txt
  base image: ostk-bench-base (74MB, 8/8 verified)

dependency order:
  fork → ci-binary → find-injection → replace-injection
                                          ↓
                            control ← runner-skill → injected → silent
                                          ↓
                                        score

gaps:
  WARNING: ostk repo not on GitHub — ci-binary blocked
  WARNING: API key injection into Docker not designed
  WARNING: mini-swe-agent skill may need adjustment

the number that matters:
  if silent matches injected → OS is invisible → 1.0 ships
```

### What calibrate does

1. **Collects** all hay and needles semantically related to the thread name
2. **Maps** dependencies between needles (from compile -O3 ordering)
3. **Surfaces** evidence: existing results, artifacts, verified states
4. **Identifies** gaps: warnings (missing but non-blocking), errors (blocking)
5. **States** the number that matters — the one metric that proves the thread
6. **Shows** the conditional: if X then Y (the decision gate)

### Calibrate vs compile

| Command | Input | Output |
|---------|-------|--------|
| `ostk compile` | single hay → single needle | sharpens one idea |
| `ostk calibrate` | thread name → full state | shows the whole picture |

Compile is the microscope. Calibrate is the telescope.

The human says `ostk calibrate SWE` and sees: where am I, what's done, what's blocked, what number do I need. One command. Full orientation.

## Acceptance Criteria

- [ ] `ostk hay "text"` captures uncompiled hay with ~ prefix and slug
- [ ] `ostk hay list` shows all items with state (~ uncompiled, → compiled)
- [ ] `ostk hay list` silently clusters hay by semantic similarity
- [ ] Clusters are named with discovered themes
- [ ] Unclustered hay shown separately
- [ ] `ostk compile ~ slug` produces a needle with verb, file, test
- [ ] `ostk compile --cluster NAME` compiles all hay in a cluster to ordered needles
- [ ] -O0 passes through without compilation
- [ ] -O1 appends .ostk/ context to the hay
- [ ] -O2 produces a full needle from hay + codebase analysis
- [ ] -O3 decomposes complex hay into ordered needles with dependencies
- [ ] Compiler reads gen_table, audit, needles, sessions, codebase
- [ ] Haiku used for intent extraction and clustering (<$0.01 per call)
- [ ] Human review gate on compile (skippable with --auto)
- [ ] Compiled needles dispatchable via `ostk run` + WORK directive
- [ ] `ostk calibrate <thread>` shows full thread state: hay, needles, evidence, deps, gaps
- [ ] Calibrate identifies blocking gaps (ERROR) vs non-blocking (WARNING)
- [ ] Calibrate states "the number that matters" — the one metric that proves the thread
- [ ] Calibrate shows the conditional gate: if X then Y
- [ ] The → prefix is added by ostk, never typed by the user
- [ ] The ~ prefix is added by ostk, never typed by the user
- [ ] Intelligence discovers structure, does not impose it
- [ ] Clustering is append-only — hay items are never deleted or merged
