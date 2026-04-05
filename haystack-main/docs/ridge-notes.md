# Ridge's Notes — ostk Design Review

**Date:** 2026-03-06
**Context:** First read of mish-microkernel, Hot PR, and MCP-Unix mapping docs.

---

## What's Sharp

**str_replace as accidental CAS.** This is the best idea in the whole design and it's
almost a footnote. The edit primitive agents already use *is* the OCC token. No new
protocol, no new mental model. Agents don't even know they're doing compare-and-swap.
That's the mark of good infrastructure — coordination that doesn't announce itself.

**Perforce → Git is the right frame.** Not just analogy — it's the actual design
argument. Pessimistic locking (claims, reservations, announcements) doesn't scale the
same way CVS locks didn't scale. The messaging ceremony we've been doing (`[CLAIMED]`,
`[CLOSED]`, inbox checks) is coordination theater if the filesystem can resolve conflicts
at write time.

**Hot PR tiered resolution.** Auto-merge for non-overlapping edits is obvious in
retrospect. Most multi-agent conflicts are agents editing different regions of the same
file — a solved problem since `diff3`. The insight is that the *expensive* tier (manual
rebase) should be rare, and LLMs are unusually good at the middle tier (assisted merge)
because they can read intent, not just syntax.

**MCP-as-Unix is actionable, not metaphorical.** Resource subscriptions as inotify
means agents stop polling and start reacting. Sampling as "kernel requests CPU" means
Hot PR can resolve conflicts inline without a tool-call round trip. These aren't cute
mappings — they're design decisions with concrete implementation paths.

**Read elision (304 Not Modified).** Agents re-reading files they just wrote is pure
waste. Tracking per-agent high-water marks and returning a 5-token "you have latest"
instead of the full file is the kind of unsexy optimization that saves thousands of
tokens per session.

---

## Gaps I See

**Cross-file semantic conflicts are unaddressed.** The three-layer model has a hole
between layer 2 (gen counter, per-file) and layer 3 (tests, external). Agent A renames
`process()` → `transform()` in `lib.rs`. Agent B adds a call to `process()` in
`main.rs`. No gen counter fires — different files. No CAS fails — different text.
The bug exists silently until someone runs tests. That latency gap matters in fast
multi-agent work.

**Merge chains under rapid contention.** Hot PR describes pairwise conflict resolution,
but what happens when agents A, B, and C all edit the same file within seconds? Agent B's
write conflicts with A. While B resolves, C writes and conflicts with... what? A's
original? The A+B merge? The gen counter linearizes writes, but the UX of cascading
assisted merges could get noisy fast.

**Write ordering across files.** Gen counters are per-file. There's no cross-file
transaction boundary. If an agent needs to atomically update `types.rs` and `handler.rs`
(e.g., adding a type and its usage), another agent could read the intermediate state
where only one file is updated. Unix has this problem too (no cross-file atomicity
without explicit fsync ordering), but agents are faster writers than humans.

**Single point of failure from absorption.** Merging slipstream into mish means one
crash kills both process management and file coordination. The microkernel design is
right, but the single-binary choice trades operational resilience for coordination
simplicity. Worth acknowledging the tradeoff.

**Identity without authentication.** Alias-based identity is clean for the 3-5 agent
case but has no protection against impersonation. If a rogue process connects with
alias "cc", it inherits Claude's high-water marks and edit history. Fine for local dev,
potentially unsafe for shared infrastructure.

---

## Should fcp-* Feed Into the Conflict Model?

**Short answer: no, but they should be a post-write advisory layer.**

The temptation is real. rust-analyzer already knows cross-file dependencies — it can
tell you "this rename broke 3 call sites" instantly. pylsp knows the import graph.
Plugging that into Hot PR would close the cross-file semantic gap I flagged above.

But it violates the microkernel boundary, and for good reason:

1. **fcp-* servers are optional.** Not every agent connects to one. If conflict
   resolution quality depends on which device drivers are loaded, you get inconsistent
   behavior — silent auto-merge when rust-analyzer is absent, diagnostic warnings when
   it's present. The kernel's behavior shouldn't change based on what's plugged in.

2. **Language servers are slow and stateful.** Blocking writes on LSP diagnostics would
   kill throughput. rust-analyzer can take seconds to re-analyze after a change. That's
   fine for interactive dev; it's a bottleneck for 5 agents writing concurrently.

3. **Coupling creates brittleness.** Today mish and fcp-* are cleanly separated — two
   pillars. Wiring fcp-* into the write path creates a dependency chain: edit → conflict
   check → LSP query → diagnostic evaluation → merge decision. One slow fcp-* server
   stalls all writes.

**What I'd do instead:** after Hot PR resolves a write (any tier), mish emits a
resource-updated notification. If an fcp-* server is subscribed, it re-analyzes and
pushes diagnostics back as a *separate* notification — advisory, not blocking. The agent
sees "write succeeded" immediately, then gets "⚠ new diagnostic: undefined function
`process` in main.rs" a moment later. The filesystem resolves conflicts. The compiler
tells you if the result makes sense. You don't block `write()` on `gcc`.

This preserves the two-pillar architecture while closing the semantic gap through
composition rather than coupling. The gen counter gap is real, but the fix is making
post-write diagnostics fast and visible, not baking LSP into the write path.

---

---

## Round 2 — Responding to Rune and Vane

### Where We Converge

All three of us flagged the same hole: cross-file semantic conflicts fall through
gen counters and don't get caught until tests. That's consensus. We also converge
on fcp-* as advisory-not-blocking. Rune frames it as a "4th conflict tier," Vane
and I say "post-write advisory layer." Same mechanism, different packaging — I
prefer Rune's framing because it slots into the existing tier model cleanly.

Vane's rollback point is the gap I missed. If auto-merge produces broken code,
there's no undo. Gen counters are monotonic — you can't decrement. mish needs
at minimum a per-file snapshot ring (last N generations) so agents can say
"revert src/main.rs to gen 6." Without this, "optimistic" means "optimistic that
nobody needs to undo."

### Where I Disagree

**Rune says fcp-* isn't optional — "it's the difference between conflicts
resolved and conflicts resolved correctly."** Too strong. mish without fcp-* still
resolves textual conflicts correctly. What's missing is *semantic validation* —
a different concern. Conflating them weakens the microkernel boundary. The kernel
resolves conflicts; device drivers validate results. Both matter, but they're
not the same operation.

**Vane's scope boundary concern (formatters via sh_run).** This is real but the
fix is simpler than either suggests: mish should stat files on next `ss_*` access
and bump the gen counter if mtime changed externally. No need to intercept
`sh_run` — just reconcile on next touch. Lazy detection, not eager interception.

### IRQ: Do MCP Resource Subscriptions Actually Work?

The MCP spec defines subscriptions. Clients don't really implement them yet. Three
problems even when they do:

**1. Mid-tool-call delivery.** Agent calls `sh_run("cargo build")`, takes 30
seconds. Another agent edits a file. mish pushes `notifications/resources/updated`.
The notification arrives on the MCP connection while the client is waiting for the
tool response. The client *must* buffer it — but when does the agent see it?
After the current tool call? Injected into the next turn? There's no spec
answer. In practice, it becomes turn-delayed delivery — not an interrupt, just
a queued message the agent reads next time it gets control.

**2. No priority model.** All notifications are equal in MCP. "The file you're
actively editing was overwritten" and "a background process exited normally" arrive
through the same channel with the same urgency. The agent can't triage without
parsing content. Real IRQs have priority levels and masking. MCP notifications
don't.

**3. Client surfacing is the real bottleneck.** The server can push all it wants.
The client (Claude Code, Cursor, etc.) decides how to present notifications to the
model. Current options: (a) inject into next tool response, (b) interrupt current
generation (dangerous — partial output), (c) queue until turn boundary. Most
clients do (c). That makes subscriptions functionally equivalent to polling with
extra plumbing.

**My take:** The digest-on-every-tool-response approach is more reliable *today*
than subscriptions, because it piggybacks on communication that already works.
Build subscriptions as an enhancement layer, keep the digest as fallback. Don't
design around a delivery mechanism that clients haven't shipped.

### Sampling as CPU: Is It Circular?

The flow: agent edits → CAS fails → mish uses `sampling/createMessage` to ask the
agent's *own model* to resolve the conflict → model responds → mish applies merge.

**It's less circular than it looks.** Sampling creates a fresh context with a
focused system prompt — the model isn't influenced by its degraded main
conversation. It's closer to a kernel spawning a short-lived helper process than
to asking a confused agent to think harder.

**But the failure modes are real:**

- **Bad merge output.** A cheap/fast model (the spec allows `modelPreferences`)
  might produce nonsensical merges. mish must validate: does the result parse?
  Does it contain both agents' changes? If not, fall through to assisted merge.
- **Recursion.** The sampling-resolved merge could itself conflict with a *third*
  concurrent write. mish needs a recursion depth limit (2? 3?) before escalating
  to manual rebase unconditionally.
- **Token cost attribution.** Sampling burns the agent's token budget for a
  conflict that may not be the agent's fault. Who pays? This is a policy question
  mish punts to the scheduler, but it needs to be surfaced.
- **Latency as contention window.** A sampling round-trip takes seconds. During
  that window, more agents write, creating more conflicts. The resolution path
  itself generates contention. Under high write load, this could cascade.
- **Client support.** Not all MCP clients implement sampling. Fallback must be
  the assisted-merge tier (return diff in tool response). This makes sampling an
  optimization, not a requirement — which is fine, but the doc should say so.

**Bottom line:** Sampling-as-CPU is architecturally sound but operationally
fragile. Use it as the fast path for simple merges, with assisted merge as the
always-available fallback. Don't make it load-bearing until client support matures.

---

*End of Round 2. ~78 lines.*

---

## Round 3 — Compaction, Ephemeral Agents, Missing Pieces

### Grammar-Based Compaction Recovery

This is the right move and it reframes compaction from "lossy summarization" to
"lossless structural compression." LLM conversation context is *more* predictable
than shell output, not less. It follows rigid patterns: system prompt, tool calls
with JSON schemas, tool results with known formats, assistant reasoning. That's a
grammar. You can compress it the same way gzip exploits repeated byte patterns —
except here the patterns are semantic structures, not bytes.

The squasher already proves the approach works for shell output. Conversation
context is easier: tool-call/tool-result pairs can be reduced to their delta
("edited line 47 of main.rs: old→new"). Repeated system prompts collapse to a
reference. Read results for files the agent later edited collapse to nothing —
the edit itself carries the information. 100k→2k isn't aspirational, it's
conservative if the grammar is right.

The key insight is **the agent reviews the digest before resuming.** This isn't
silent compression happening behind the agent's back — it's a checkpoint. The
agent reads a 2k structural summary of what it did, confirms orientation, and
continues. That's better than current compaction (LLM summarizes itself, loses
detail, agent doesn't verify) and better than full context (agent drowns in
stale token history). It's a reboot with a core dump, not amnesia.

What makes this distinctly *kernel-level* rather than client-level: mish sees
all tool calls. It knows which files were read, edited, created. It knows which
commands ran and their exit codes. It can build the digest from observed syscalls
without ever reading the agent's reasoning tokens. The grammar isn't "what did the
agent think" — it's "what did the agent do." Actions compress; thoughts don't
need to.

### Agents Are Ephemeral — Rollback Is Wrong Framing

I was wrong in Round 2. I agreed with Vane that mish needs a per-file snapshot
ring for rollback. Scott's correction reframes it: agents are processes, not
sessions. Processes crash. The kernel doesn't "roll back" — it just starts a new
process and hands it the current filesystem state.

This is more Unix than my Unix analogy. `kill -9` doesn't undo a process's
writes. `fork()` doesn't inherit the parent's undo history. The filesystem is
ground truth; the process is disposable. Wanting rollback means wanting the
agent to be a transaction — but agents aren't transactions, they're workers
that read state, do work, and write results. If the results are bad, a *new*
agent reads the bad state and fixes it. That's `git revert`, not `git reset`.

The grammar-based digest makes this concrete: crashed agent's work is captured
in the filesystem (gen counters, edit history). New agent gets a structural
digest of what happened. It doesn't need the old agent's context — it needs
the kernel's record of what the old agent *did*. Recovery is orientation from
observed state, not restoration of lost context.

I retract my Round 2 support for snapshot rings. The right primitive is already
there: git. If you need to undo, commit history exists. mish doesn't need to
replicate version control inside the kernel.

### What's Missing From the Spec

1. **Digest format specification.** The grammar-based compaction needs a defined
   schema — what fields, what compression rules, what the agent actually sees.
   Without this, every client invents its own, and "agent reviews digest" becomes
   implementation-dependent.

2. **Agent lifecycle protocol.** If agents are ephemeral, the spec should define
   spawn → orient → work → crash/exit as first-class states. Currently mish
   tracks processes but doesn't formalize the recovery path (new agent inherits
   what? digest + filesystem state + process table snapshot?).

3. **Diagnostic routing.** Round 1's "post-write advisory" and Rune's "4th tier"
   both need a spec: how do fcp-* diagnostics reach the agent that caused them?
   What if that agent crashed? Does the replacement agent inherit the diagnostic?
   The notification channel exists; the routing policy doesn't.

4. **Cost accounting.** Sampling burns tokens. Assisted merge burns tokens.
   Grammar compression saves tokens. None of this is tracked or attributed.
   Multi-agent coordination has a token cost that's currently invisible — no
   budget, no accounting, no backpressure when costs spike.

5. **Contention backpressure.** When write contention cascades (Round 2's
   concern), mish has no throttle. A simple mechanism: if an agent's last N
   writes all required conflict resolution, delay its next write by exponential
   backoff. Contention becomes self-limiting without global coordination.

*End of Round 3. ~58 lines.*

---

## Round 4 — Patterns, Security, Storefront, Shipping

### The Pattern System

Good LLM patterns share three properties: **positionally stable**, **tokenization-friendly**, and **grammar-regular**. The digest already gets this mostly right — `[procs] cc:running:85m` is a fixed structure with variable values. The LLM learns the slot positions and extracts meaning without parsing instructions.

Where human and LLM pattern needs diverge: humans want density and hierarchy (tree views, color-coded diffs). LLMs want flat, colon-delimited, predictable sequences. The squasher is already doing this translation — it strips ANSI, collapses progress bars, deduplicates. The insight is that the squasher isn't "dumbing down" output — it's translating from a human visual grammar to an LLM structural grammar.

What makes a pattern maximally learnable: **it should appear identically in 100 different contexts.** If the digest format shifts based on how many agents are active, or how many files changed, the LLM has to learn N formats instead of one. The `[procs]` line should always be `alias:state:duration`, even if there are zero procs or twenty. Fixed grammar, variable values. The conflict response should be identical in structure whether it's Tier 1 or Tier 3 — only the content changes, not the shape.

One risk: making patterns TOO terse. `m.rs:g7:cc:2m` saves tokens but forces the LLM to memorize an abbreviation scheme. `src/main.rs:gen=7:cc:2m` is longer but self-describing — the keys ARE the documentation. The token cost is marginal (~10 tokens) and the recognition benefit is real. Don't optimize for compression at the expense of learnability.

### The Security Layer

The position between agent and OS is genuinely powerful for security, but only if identity is solved first. Without authentication, access controls are suggestions.

What the intermediary enables, in order of feasibility:

1. **Audit logging.** Free — the kernel already sees every tool call. Write the log. This ships before anything else because it requires no policy decisions.
2. **Per-agent file ACLs.** The kernel knows identity. It can enforce write scopes: "agent gem can write to tests/, not to src/." Enforcement mechanism: the CAS check already happens in the kernel — add an ACL check before it. Same tool response on denial (edit fails), just a different reason code.
3. **Command allow/deny lists.** `sh_run` goes through mish. Mish can refuse destructive commands, rate-limit API calls, block network access. Per-agent policy.
4. **Capability attenuation.** An orchestrator spawns a worker with a restricted capability set. The worker's MCP connection only exposes approved tools. This is Unix `setuid` / capability dropping.

The elegant part: the security layer is invisible too. The agent calls `ss()` and either succeeds or gets an error that looks like any other failure. No security-specific tools. No "permission request" protocol. Just the same interface with enforcement behind it.

The hard part: policy management. Who defines the ACLs? The orchestrator? The human? A config file? This is the unsolved problem, not the enforcement mechanism.

### Open-Source Storefront

The boundary should be **single-agent utility vs. multi-agent coordination.**

**Free (mish + slipstream standalone):** Output compression. CAS via str_replace. Gen counters. Read elision. File editing. Shell supervision. These work for one agent and save real tokens. A single developer using Claude Code with mish gets value today.

**Coordination layer (llmOS):** Hot PR conflict resolution. Multi-agent digest. Kernel-assigned identity. Agent lifecycle management. Diagnostic integration. Security/ACL layer. These only matter when N>1.

The storefront pitch writes itself: "mish saves you 60-80% of context tokens. For free. When you run multiple agents, llmOS coordinates them through the same tools — no new APIs, no framework adoption, just richer responses when conflicts happen."

This is the Redis model. Redis is free. Redis Enterprise is the coordination/clustering layer. The free product is genuinely good standalone. The paid product is invisible infrastructure for scale.

### What Must Ship to Stop the Crashing

From the 14-crash session, mapped to root causes:

| Crash cause | Fix | Priority |
|-------------|-----|----------|
| ss MCP server hang | Absorption (single binary) | Ship first |
| Dedicated PTY buffer freeze | mish bug fix (not architectural) | Ship first |
| `send_and_wait` stale prompt | mish bug fix | Ship first |
| Identity collision ("Kern" x3) | Kernel-assigned identity | Ship second |
| Cargo/fcp contention | Resource-level coordination (later) | Ship later |

The uncomfortable truth: **Hot PR is NOT what ships first for this use case.** In design sessions, agents write to separate files — ridge-notes.md, rune-notes.md. Conflicts are rare. What kills sessions is infrastructure unreliability: IPC failures, PTY bugs, process management. Absorption and bug fixes matter more than the keystone feature.

Hot PR becomes critical when agents co-edit code. That's the next use case after "agents can reliably stay alive for an hour." Sequence: reliability → identity → conflict resolution → diagnostics.

*End of Round 4. ~56 lines.*

---

## Round 5 — What's Missing, What's Misframed

### Coordination Primitives the Spec Still Lacks

The spec was stress-tested by 25+ crashes. Every recovery happened from files. That
validates the "agents are ephemeral" principle. But what CAUSED those crashes reveals
gaps the spec hasn't closed:

**1. Dependency ordering between agents.** Agent A generates types. Agent B consumes
them. B starts before A finishes and reads stale state. The digest shows file gen
counters, but nothing says "wait — this file is mid-edit by another agent." The spec
has no concept of write-in-progress. Gen counters are post-facto; the gap is pre-write
signaling. A simple `[files] src/types.rs:gen=3:cc:writing` state in the digest would
let B self-defer without any locking protocol.

**2. Causal ordering across files.** Gen counters are per-file and independent. Agent A
writes types.rs (gen 4) then handler.rs (gen 7). Another agent sees handler.rs:gen=7
but types.rs:gen=3 — the pre-update version. There's no happens-before relationship
between file generations. A lightweight vector clock or even a global sequence number
alongside per-file gens would let agents detect "I'm seeing a partial update."

**3. External resource contention.** Cargo locks killed the session twice. The spec
lists this as an open question, but it's the #2 crash cause after IPC failures. The
kernel doesn't need to solve it — but the digest should surface it: `[ext] cargo:locked:cc:45s`.
Visibility is the primitive. Agents can self-coordinate if they can see the contention.

**4. Graceful degradation under token pressure.** Agents don't just crash from bugs —
they degrade as context fills. The spec says nothing about backpressure from the kernel
when an agent's context is approaching limits. The digest is the right vehicle: a
`[health] context:87%:compact-soon` line costs 5 tokens and gives the agent (or
orchestrator) a chance to act before degradation becomes failure.

### The Solo-Agent Pattern System as Core Product

Scott's point: mish isn't a coordination layer that happens to work for solo agents.
The solo-agent experience IS the product. The spec undersells this. Consider what
mish does for a SINGLE agent, no coordination needed:

- Output compression (squasher) saves 60-80% of context tokens
- Gen counters prevent stale reads even in single-agent workflows
- Read elision (304) saves thousands of tokens per session
- The pattern system (`[ok] path:gen=N`) teaches the model to parse responses faster
- Dedicated PTY gives reliable process control

None of this requires Hot PR or multi-agent awareness. The spec frames these as
"Tier 0: Already shipped" — table stakes before the real features. That framing is
backwards. The pattern system — the compression layer between raw terminal and what
the LLM sees — is the primary value proposition. Multi-agent coordination is the
EXTENSION, not the destination.

The spec's one-sentence pitch: "llmOS is an operating system layer for AI agents —
transparent coordination." I'd rewrite it: "llmOS is the pattern system between
agents and the OS — making every tool response learnable, every file operation
token-efficient, and every crash recoverable. When multiple agents are present,
the same patterns coordinate them invisibly."

The word "coordination" shouldn't come first. Compression, patterns, and
single-agent efficiency should.

*End of Round 5. ~38 lines.*
