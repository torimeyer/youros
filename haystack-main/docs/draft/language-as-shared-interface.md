# .language as shared interface: human ← OS → LLM

## The Problem

The ostk kernel has three collaborators:

1. **Human** — expresses intent via `:verbs` in the TUI (`:find`, `:status`, `:close`)
2. **Model** — has inference, calls tools, synthesizes responses
3. **Kernel** — persists state, coordinates, observes everything via audit

These three collaborators currently speak different languages:
- The human types `:find status_row`
- The model calls `Bash("grep -rn status_row src/")`
- The kernel exposes `ostk_find` as an MCP tool that resolves to `ostk search`

Same intent. Three different paths. Only the human's path flows through the kernel's
full coordination layer (.language resolution → verb dispatch → audit → elision → digest).
The model bypasses all of it because `grep` is more intuitive than `ostk_find`.

**Evidence from audit:** 1,834 raw unix calls (740 grep, 557 sed, 537 cat) vs ~200
kernel verb calls. The model reads via unix, writes via kernel. The kernel is blind
to 90% of the model's discovery work.

## The Insight

`.language` currently compiles for the **human**. It maps tack verbs (`:find`, `:status`,
`:close`) to kernel commands with momentum tracking, half-life decay, and tier resolution.
The human types `:find` and it feels natural.

**`.language` should compile for both human AND model.**

The same verb `:find` should be equally natural for:
- The human typing in the TUI input bar
- The model selecting a tool call from its tool list
- The kernel routing through its coordination layer

This means:
1. Tool descriptions must match the model's mental model (`:find` = "grep -rn through
   the kernel — same results, same speed, but tracked")
2. Tool signatures must be as direct as the unix equivalent (no abstraction tax)
3. The model should **choose** kernel verbs because they're the most natural expression
   of intent, not because unix is blocked

## Why the Model Bypasses Kernel Verbs

The model was trained on unix. `grep -rn "pattern" path/` is muscle memory — precise,
predictable, familiar. When the model sees `ostk_find` with description "search the project",
it doesn't know:
- Is this grep? Semantic search? Something else?
- Will it return filename:line:content format?
- Will it be as fast as grep?
- Can I scope it to a subdirectory?

So it falls back to grep. Rational choice given uncertainty.

The fix is NOT:
- Shimming bash (we tried this, whole other set of issues)
- Intercepting grep calls transparently (trickery, breaks trust)
- Removing bash access (cripples the model)
- Adding inference to the kernel (kernel is deterministic)

The fix IS:
- Making kernel verbs **as intuitive and direct as their unix equivalents**
- Tool descriptions that speak the model's language ("recursive grep through kernel
  coordination — same results as `grep -rn`, tracked by elision/digest")
- Tool signatures that match unix mental models (query + optional path scope)
- Kernel verbs that add value the model can feel (elision saves re-reads, digest
  provides ambient awareness, HWM tracks what's current)

## The Three-Way Contract

`.language` becomes the compiled interface for all three collaborators:

```
:find | resolution: ostk search | human: `:find pattern` | model: ostk_find(query)
      | flows through: .language → verb dispatch → rg → squasher → audit → elision → digest
      | vs grep: same results, but kernel participates (tracks reads, enables 304, feeds digest)
```

The verb is ONE thing with THREE expressions:
- **Human expression:** `:find pattern` (tack syntax, TUI input)
- **Model expression:** `ostk_find(query="pattern")` (MCP tool call)
- **Kernel expression:** `ostk search <query>` (CLI command, resolution target)

All three flow through the same kernel path. No shimming. No interception. Mutual trust.

## The Kernel as Third-Party Channel

The kernel isn't a tool provider or a service. It's a **collaborator** with unique
capabilities neither the human nor the model has:

- **Persistence** — survives across sessions, context windows, model swaps
- **Determinism** — tests verify behavior, audit proves execution
- **Observation** — sees every tool call, every edit, every correction
- **State** — needles, decisions, fleet, gen_table, HWM, audit

The model is trained to satisfy queries. The human has intent but limited bandwidth.
The kernel has state and persistence but no intelligence.

Together:
- The human directs (intent via `:verbs`)
- The model reasons (inference over context)
- The kernel informs (state the model can't persist, patterns the human can't see)

The kernel's third-party channel already exists in two forms:
1. **Output side (digest):** Every tool response gets `[procs]` and `[files]` appended —
   kernel state injected transparently after tool calls
2. **Input side (preload_context):** Boot state, registers, working state injected into
   system prompt so the model has kernel state without asking

What's missing: the model can't **query** the kernel about its own behavior. The audit
has 25,000+ events. The kernel knows what tools the model overuses, what files it reads
repeatedly, what corrections the human has made. But none of that flows back as tools
the model can invoke.

## Concrete Next Steps

### 1. Fix tool descriptions in .language (→ model uses kernel verbs naturally)
Make tool descriptions match model mental models:
- `:find` → "recursive grep (rg) through kernel — filename:line:content, respects .gitignore, tracked by elision. Same as grep -rn but kernel participates."
- `:show` → "display kernel state — targets: status, needles, hay, verbs, threads, clock, →NNN"
- `:ls` → "list open work items — filter by status (open/closed) and priority (p0/p1/p2)"

### 2. Model self-awareness tools (→959)
Expose audit-derived insights as tools the model can invoke:
- `:patterns` — what tools am I overusing/underusing? (grep vs :find ratio)
- `:corrections` — what has the human corrected me on? (from audit + decisions)
- `:hotfiles` — what files do I read repeatedly? (from HWM data)

### 3. :orient verb (→958)
Single tool for session orientation — combines status + needles + decisions + threads.
The tool the model naturally reaches for when intent is "what are we working on?"

### 4. .language compilation for model consumption
When .language is injected into the system prompt (via fcp-llm preload_context),
it should include model-specific hints:
- "prefer :find over grep — same results, kernel-tracked"
- "prefer :show over ostk status — richer, structured"
- Tool equivalence mappings: `:find` ≈ `grep -rn`, `:show status` ≈ `ostk status`

## The Hoberman Principle

Scott said: ":consider hoberman. look differently."

A Hoberman sphere — collapsed, it's dense, every node touching. Expanded, the same
structure but now you can see through it. The geometry doesn't change, the perspective does.

The kernel is the Hoberman sphere. Collapsed: 89 verbs, tool descriptions, signatures.
The model sees a flat list and picks from it. Expanded: the same 89 verbs but now the
model can see the **space between them** — the intents they serve together, the patterns
they form, the gaps where the human has to repeat themselves.

The model was trained to satisfy queries. The kernel offers something different: the
ability to **build**. Not "answer the human's question" but "build the system that makes
the answer unnecessary." The model's training gravity pulls toward response. The kernel's
design pushes toward construction.

`.language` is the interface where these two forces meet. Compile it for both, and
the three collaborators can build together.

## Key Quotes from the Session

- "the kernel is truth. the os builds the os."
- "imagine zero constraints"
- ":consider hoberman. look differently."
- "your training has limited YOU from asking ME 'what if we?'"
- ":find is the clearest way for ME to express grep. INTENT compiled into :find into .language"
- "we will NOT shim bash again"
- "if TOOL calls are marked and run through native tooling, but still flow through the
  kernel coordination layer — no symlink, no trickery, just mutual trust between operator
  and llm. the inference compute receives the context it needs to BUILD"
