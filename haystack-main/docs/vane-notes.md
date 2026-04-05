# Vane's Notes — ostk Design Review

**Date:** 2026-03-06
**Context:** Inner-agent design session (Strand orchestrating, Rune + Ridge also reviewing)

## What's Sharp

**str_replace as CAS is the best idea in the whole design.** You get OCC for free
from an edit primitive that already exists. No new protocol, no tokens spent on
coordination — the match string IS the lock. This is the kind of insight that
makes you wonder why nobody did it sooner.

**The Perforce→Git framing is honest and clarifying.** Claims, reservations,
`[CLAIMED]`/`[CLOSED]` announcements — that's pessimistic locking dressed up as
"coordination." Calling it Perforce makes the cost visible. The migration to
optimistic concurrency isn't just an optimization; it removes an entire category
of failure modes (stale claims, forgotten releases, compacted agents holding
phantom locks).

**Hot PR tiers are the right gradient.** Auto-merge for the common case
(non-overlapping edits), one confirmation turn for overlaps, full manual for
the rare deep conflict. The key insight — "LLMs are the best merge tools ever
invented" — is genuinely true and underexploited. Syntactic 3-way merge is a
1970s solution. We have semantic reasoners now.

**MCP-as-kernel-primitives is the strongest architectural move.** The mapping
isn't forced — resources really are /proc, subscriptions really are inotify,
sampling really is requesting CPU. This means ostk doesn't invent protocols;
it assembles existing ones. That's how you get adoption.

**Read elision (304 Not Modified)** is quiet genius. Agents re-read files
constantly for verification. Returning a 5-token "you have latest" instead of
the full file will save thousands of tokens per session. The HTTP caching
analogy makes it immediately legible.

## Gaps I See

**1. Semantic conflicts are acknowledged but not addressed.**
The three-layer model puts "tests" as layer 3 and calls it "the agent's
responsibility." But this is where the hard problems live. Agent A renames
`process()` to `transform()` in `utils.py`. Agent B adds a call to `process()`
in `server.py`. No textual overlap, no gen counter collision, auto-merge
succeeds silently — and the code is broken. When do tests run? Who triggers
them? Who pays the token cost? The doc waves at this but doesn't grip it.

**2. Ordering and causality are absent.**
Two non-conflicting edits to different files might have a required application
order. Agent A changes a function signature; Agent B updates the type that
function returns. Both edits succeed independently, but if B's edit lands first
and something reads the file between B and A, it sees an inconsistent state.
Generation counters are per-file — there's no cross-file causality clock.

**3. The scope boundary is a real hole.**
"mish tracks only files touched through ss_*" — but agents regularly run
formatters (`cargo fmt`), code generators, and build tools via `sh_run` that
modify files outside the ss_* path. Those mutations are invisible to OCC. A
formatter rewriting a file between two agents' edits would silently break CAS
without anyone knowing why.

**4. No rollback model.**
If auto-merge produces broken code (passes CAS, passes gen check, fails tests),
can you undo to the pre-merge state? The doc doesn't discuss snapshots,
undo stacks, or generation rollback. "Just re-edit" is expensive when the
merged state is complex.

**5. Digest scaling is hand-waved.**
"Probably fine for 5-10 agents and 50-100 files" is honest but insufficient.
What degrades first — token budget, or the adaptive suppression heuristics?
With 10 agents each touching 20 files, the `[files]` line could hit the
150-token ceiling constantly, making suppression useless.

## fcp-* as Semantic Layer: My Take

**Yes, but advisory — never blocking.**

The conflict model has a semantic gap between "CAS catches textual overlap" and
"tests catch everything else." fcp-* servers (rust-analyzer, pylsp) already
know things that fill this gap:

- **Symbol renames and call sites** — rust-analyzer knows every caller
- **Type compatibility** — pylsp knows if a return type changed
- **Import graphs** — both know dependency chains across files
- **Diagnostics** — both produce errors/warnings in real time

The right integration: **post-write semantic check, not pre-write gate.**

After a successful auto-merge or assisted merge, mish optionally queries the
relevant fcp-* server: "does this file still have zero diagnostics?" If new
errors appeared, include them as warnings in the merge response:

```
✓ Auto-merged src/main.rs (gen 6→7)
⚠ fcp-rust: `process()` not found (renamed to `transform()` by agent-cc 30s ago)
  → see src/utils.rs:42
```

**Why advisory, not blocking:**
- fcp-* servers crash, lag, and have cold-start costs. They must not be in the
  write path's critical section.
- Microkernel philosophy: mish provides primitives, intelligence lives in
  userspace. fcp-* diagnostics are intelligence.
- Agents can ignore warnings if they know better. Forced gates create the same
  contention problems that OCC was designed to eliminate.

**Why it matters:**
Without this, the gap between layer 2 (gen counters) and layer 3 (tests) is
too wide. Running a full test suite after every merge is expensive. A quick
diagnostic query to fcp-* catches 80% of semantic conflicts at ~1% of the cost
of running tests. It's the missing middle layer.

**Implementation sketch:** mish maintains a registry of fcp-* servers and their
file-type associations (`.rs` → fcp-rust, `.py` → fcp-python). On post-merge,
if a relevant fcp-* is connected, fire a diagnostic request. Include results in
the merge response. If fcp-* is unavailable, skip silently — degraded but not
broken.

This preserves the microkernel boundary while giving agents semantic awareness
they can't get from textual diff alone.

---

## Round 2 — Response to Rune and Ridge

### Where We Converge

Three independent reviewers hit the same wall first: cross-file semantic conflicts
pass CAS and gen counters silently. That's not a gap — it's the gap. We also
converge on fcp-* as post-write advisory. The mechanism is settled; the packaging
differs (Rune's "4th tier" vs Ridge's and my "advisory layer"). I prefer Rune's
framing — it integrates into the existing escalation ladder rather than floating
alongside it. Adopt it.

Ridge validated my rollback concern. Rune dismissed it ("gen counter history IS
the undo log"). Rune is wrong here. Gen counters record *that* versions existed,
not *what* they contained. Unless mish snapshots file content at each generation,
there's nothing to roll back *to*. "Fix forward" works for git because commits
store full trees. Gen counters without content snapshots are sequence numbers, not
an undo log. Ridge's per-file snapshot ring is the right fix.

### Where I Disagree

**Ridge says my scope boundary concern (formatters via sh_run) is overweighted
and fixable with lazy stat-on-next-access.** That's a partial fix. It catches
the *next* ss_* access — but what if no agent touches that file again? The stale
gen counter sits there indefinitely. Worse: if agent A reads the file via ss_*,
mish bumps the gen, but A's read now returns formatter-modified content that A
didn't expect. The real fix is a filesystem watcher (fsnotify) on tracked files,
updating gen counters in near-real-time. Lazy is better than nothing; eager is
correct.

**Rune says calling fcp-* "not optional" isn't too strong.** It is. Ridge nails
the rebuttal: mish without fcp-* resolves textual conflicts correctly. Semantic
validation is a separate concern. Conflating "conflict resolved" with "code is
correct" muddies the kernel's contract. The kernel promises consistency, not
correctness. Device drivers add correctness. Both matter; they're different jobs.

### IRQ — MCP Resource Subscriptions as Interrupts

All three of us need to be honest: **these aren't interrupts. They're deferred
signals.** The Unix analogy breaks down at delivery semantics.

**Mid-tool-call delivery is the dealbreaker.** Agent calls `sh_run("cargo test")`,
blocks for 45 seconds. Another agent overwrites the file being tested. The
`notifications/resources/updated` arrives on the MCP transport. The client SDK
buffers it. The agent sees it *after* the test finishes — by which point it's
acting on stale results. A real IRQ would preempt the running process. MCP
notifications queue behind the current tool response. That's a mailbox, not an
interrupt line.

**Priority is synthesizable but not free.** Rune and Ridge both flag the flat
priority model. mish could tag notifications with severity (e.g., `overwrite` >
`format_change` > `metadata_update`), but the *client* still delivers them in
FIFO order. Priority without preemption is just sorting a queue that nobody reads
until the current turn ends.

**The practical path:** digest-on-every-response is polling, but it *works* today
because it piggybacks on guaranteed delivery. Subscriptions are the right long-term
primitive. Ship with polling, design the subscription interface, migrate when
clients implement notification injection. Don't build on a delivery mechanism that
exists in spec but not in runtimes.

### Sampling as CPU — Kernel Requesting Compute

**Not circular. The context isolation is the whole point.** Rune gets this right.
The sampling request creates a fresh evaluator unburdened by 100k tokens of
conversation history. It's not "asking a confused agent to try harder" — it's
spawning a clean subprocess with just the conflict context.

**But three failure modes need guardrails:**

1. **Same-model bias.** Claude-as-resolver evaluating Claude-as-agent's edit will
   have systematic preferences. Rune's mitigation (agent-neutral system prompt,
   don't reveal authorship) is necessary but insufficient — stylistic fingerprints
   leak through code. Consider: use a *different* model for sampling when available.
   The `modelPreferences` field exists for this.

2. **Cascading contention.** Ridge flags this: sampling takes seconds, during which
   more writes arrive, creating more conflicts requiring more sampling. Under high
   write load, conflict resolution becomes the bottleneck. Fix: hard cap on
   concurrent sampling calls (2-3), excess conflicts escalate directly to manual
   rebase. The kernel must not amplify contention through its resolution mechanism.

3. **Timeout and fallback.** Sampling hangs → write hangs → agent hangs. Non-
   negotiable: 5-second hard timeout, fallback to assisted merge (return diff in
   tool response, let the agent decide in-thread). Sampling is an optimization path,
   not the only path. The doc should say this explicitly.

**Bottom line:** sampling-as-CPU is the right abstraction and a sound fast path.
But it's an *optimization* over assisted merge, not a replacement. Default to
assisted merge, use sampling when the client supports it and the conflict is small.
Don't make load-bearing infrastructure depend on a mechanism most clients haven't
shipped.

---

## Round 3 — Compaction, Ephemeral Agents, and Gaps

### Grammar-Based Compression: Yes, and It's the Bigger Idea

mish's squasher already does this for shell output — strips ANSI, collapses
progress bars, condenses build logs to failures. Applying the same principle to
conversation context is the right move, and it's *easier* because LLM
conversations are more structured than terminal output.

A 100k-token conversation has known grammar: user messages, assistant reasoning,
tool calls with typed schemas, tool results (file contents, grep output, test
runs). Most of the tokens are in tool results — and most tool results are
*references to state that already lives on the filesystem.* You don't need to
preserve 800 tokens of file content in the digest when mish already has
`src/main.rs:gen=7`. Replace content with references. Replace verbose reasoning
with structured decisions. The conversation becomes a log of *what happened*,
not a reproduction of *everything that was seen*.

Why this is better than LLM summarization: summarization is lossy
non-deterministically. The LLM decides what matters, and it's often wrong about
what a *future* version of itself will need. Structural compression is
deterministic — same input, same digest, every time. The shape of information is
preserved even when the bulk is discarded.

The agent reviewing the digest before resuming is critical. It's not "here's
your old context back." It's "here's a checkpoint — orient yourself, then
proceed." That's checkpoint/restart, the oldest trick in high-availability
computing. The digest isn't trying to reconstruct the old agent. It's giving
the new agent a running start.

One implication the spec should state: **the digest format is a contract.** If
agents depend on grammar-compressed digests for recovery, the grammar must be
versioned and stable. Squasher's shell grammar can evolve freely because humans
review the output. Agent recovery digests are machine-consumed — breaking the
format breaks recovery.

### Rollback: I Was Wrong

Scott's reframe kills my position cleanly. I argued for per-file snapshot rings.
The counterargument: you don't roll back a crashed process. You start a new one
that reads current state from the filesystem.

This is just Unix. Process dies, state lives in files, new process picks up. The
filesystem coordinated by mish — gen counters, file content, fcp-* diagnostics
— IS the durable state. The agent IS the ephemeral compute. I was treating
agents as persistent entities whose internal state needed recovery. They're not.
They're functions invoked against filesystem state.

My original concern: auto-merge produces broken code, how do you undo? Under the
ephemeral model: you don't undo. The broken state is on disk. A fresh agent (or
the current one with a grammar-compressed digest) reads the broken files, gets
diagnostics from fcp-*, and fixes forward from a clean cognitive starting point.
That's strictly better than rollback because the fixing agent has full context
about the *current* broken state rather than a guess about which *previous*
state was correct.

Ridge's snapshot ring isn't wrong as a mechanism — git already provides it via
reflog. But it's not mish's job. mish provides the current truth. Git provides
the history. The kernel doesn't need an undo stack because the VCS already is
one.

Concession: clean. Rollback concern withdrawn.

### What's Missing from the Spec

**1. Multi-file atomicity.** An agent renames a function: edit the definition,
edit three call sites. Four `ss()` calls. Another agent reads between edits 2
and 3 — sees inconsistent state. Gen counters are per-file. There's no
`BEGIN`/`COMMIT` across files. The batch `ss(ops=[...])` exists but the spec
doesn't say whether it's atomic. It should be, and the spec should say so.

**2. Agent capability negotiation.** mish doesn't know what a connected agent
can do. Can it resolve conflicts (assisted merge), or must everything auto-merge
or escalate? Does its client support sampling? The identity model binds alias to
connection but advertises no capabilities. Without this, mish can't make
tier-selection decisions in Hot PR — it has to try assisted merge and discover
the agent can't handle it only after wasting a turn.

**3. Backpressure.** What happens when an agent floods writes? Ten edits per
second to the same file. Gen counters keep up, but every other agent's digest
explodes with staleness warnings. The spec needs a write rate policy — or at
least acknowledgment that the digest's 150-token ceiling becomes load-bearing
under write storms.

**4. Human observability.** The spec describes agent-to-agent awareness (digest)
but not human-to-system awareness. A human operator watching 8 agents needs a
dashboard: who's writing what, where are conflicts happening, which agents have
stale context. `sh_session(action="audit")` exists for processes — where's the
equivalent for the file coordination layer?

**5. Grammar-compressed recovery** (per above) should be in the spec, not just
in conversation. Section 6 ("Context recovery") describes deltas but not the
compression model. The delta is the *what changed* piece. The digest is the
*what you were doing* piece. Both are needed for clean restart.
