# Rune's Notes — ostk Design Review

**Date:** 2026-03-06
**Context:** Inner-agent design session (Strand orchestrating, Ridge + Vane also reviewing)

---

## What's Sharp

**str_replace as accidental CAS.** This is the single best insight in the design. Most OCC systems bolt on version tokens after the fact. Slipstream's edit primitive *already is* a compare-and-swap — the old_str IS the lock-free compare. Zero new machinery for layer 1 conflicts. Elegant.

**Hot PR tiers are well-stratified.** Auto-merge (silent), assisted merge (one turn), manual rebase (full agent decision). The escalation ladder maps cleanly to cost: zero tokens → cheap tokens → expensive tokens. The insight that LLMs are the best merge tools ever invented is correct — 3-way merge is syntactic; LLMs do semantic merge natively.

**MCP-as-Unix mapping is surprisingly tight.** Resource subscriptions as inotify/IRQ is the real unlock. It moves agents from polling (digest on every tool response) to interrupt-driven awareness. Sampling-as-CPU for conflict resolution is clever — the kernel requesting compute from the process it's serving. This isn't a metaphor; it's the actual control flow.

**Elimination of the messaging layer.** The entire claim/reserve/announce pattern was compensating for missing infrastructure. With conflict resolution in the write path, agents coordinate *through the filesystem*. This is the right call. Git repos don't need a chat server.

**Read elision via high-water marks.** Tracking per-agent read state to return 304-style "you have latest" instead of full file content. Token savings compound fast across a session. Smart.

---

## Gaps I See

**1. Semantic conflicts are punted entirely.** Layer 3 ("tests catch it") is listed but unaddressed. Two agents can make non-overlapping, auto-merged edits that are textually clean but semantically broken — agent A renames a function, agent B adds a call to the old name three files away. Auto-merge succeeds. Tests catch it later, maybe. The gap between "write succeeded" and "code is correct" is where real multi-agent damage happens.

**2. Generation counters are per-file, but edits are cross-file.** A refactor touches 8 files. Another agent edits one of them. Gen counters flag the single-file conflict, but there's no concept of an atomic multi-file changeset. If an agent's cross-file refactor partially succeeds (3 of 8 files written, then conflict on file 4), the codebase is in an inconsistent intermediate state. Unix has transactions for a reason.

**3. Digest scaling.** The doc acknowledges "probably fine for 5-10 agents and 50-100 active files" — but that's a design session, not production. With resource subscriptions (inotify model), each agent subscribes to files it cares about. But who decides what an agent "cares about"? If it's the agent itself, you get under-subscription (missed conflicts) or over-subscription (noise). Needs a heuristic or a Teams-level policy.

**4. Bypass detection is explicitly out of scope.** If an agent writes via `cat > file.py`, mish doesn't know. The doc says "acceptable — the agent opted out." But in a multi-agent system, one rogue `echo >>` from a shell command silently corrupts the coordination model. At minimum, mish should detect filesystem changes on next `ss_*` access to that file (stat mtime vs last known) and flag it.

**5. Hot PR assumes agents are good merge reviewers.** The assisted-merge tier shows a diff and asks "confirm?" But a compacted agent with degraded context might rubber-stamp a bad merge. The sampling approach (using the agent's model with a fresh system prompt) is better than relying on the agent's degraded main context — but the doc doesn't commit to which path is primary.

---

## On fcp-* as a Semantic Layer for Conflict Resolution

This is the question I find most interesting. Should rust-analyzer / pylsp feed into Hot PR?

**Yes, but as an optional enrichment — not a gate.**

The three-layer conflict model is: CAS → gen counter → tests. There's a missing layer between gen counter and tests: **static analysis**. fcp-rust can tell you, right now, at write time:

- "This auto-merge introduced 2 new diagnostics (unresolved reference on line 47)"
- "Agent A's rename of `process()` conflicts with Agent B's new call to `process()` in a different file"
- "The merged result has a type error that neither edit had individually"

This is cheap (subsecond from rust-analyzer), synchronous, and catches the exact class of bugs that layers 1-2 miss and layer 3 catches expensively (full test suite).

**How it fits architecturally:** mish stays the microkernel — it doesn't parse code. But after a successful auto-merge or assisted merge, mish can *optionally* query the fcp-* server: "run diagnostics on the merged file." If new errors appear, downgrade the merge from auto to assisted, or annotate the success with a warning.

This preserves the separation of concerns. mish handles textual coordination. fcp-* provides semantic intelligence. The integration point is a post-merge diagnostic hook — not semantic parsing baked into the kernel.

**The risk of NOT doing this:** auto-merge becomes a false-confidence signal. "Your edit succeeded, gen bumped, no conflict" — but the code is broken. Agents trust the kernel's "all clear" and move on. Tests catch it minutes later, multiple edits downstream. Rollback is expensive.

**The risk of doing this wrong:** coupling mish to rust-analyzer creates a hard dependency. What about Python projects? What about non-code files? The hook must be optional, pluggable, and non-blocking. fcp-* is already the right abstraction — it's a device driver. The kernel queries the driver if one is available, proceeds without it if not.

**My recommendation:** Add a fourth conflict-resolution tier between assisted merge and manual rebase: **diagnostic-flagged merge**. Auto-merge succeeds textually, fcp-* flags semantic issues, agent gets the merge result PLUS diagnostics. Zero new concepts — just wiring the existing pieces together.

---

## One-Line Summary

The design is sharp where it's concrete (CAS, gen counters, Hot PR tiers) and hand-wavy where it's hard (cross-file atomicity, semantic conflicts, scale). The fcp-* semantic layer isn't optional — it's the difference between "conflicts resolved" and "conflicts resolved correctly."

---

## Round 2 — Response to Ridge and Vane

### Where We Agree

All three of us independently flagged the same top issue: **semantic conflicts across files are the real gap.** The rename-breaks-callsite example appeared in all three notes nearly verbatim. That's signal — it's not a theoretical concern, it's the first thing every reviewer hits.

All three of us also converged on fcp-* as **advisory post-write, not blocking pre-write.** Ridge frames it as "you don't block write() on gcc" — exactly right. Vane's implementation sketch (fcp-* registry keyed by file extension, fire-and-forget diagnostic query) is the cleanest version of what I was proposing. I'd adopt that sketch directly.

We all agree on cross-file atomicity as a gap. Ridge and Vane both flag the "intermediate state" problem. I framed it as "partial success on multi-file refactor." Same bug, three angles.

### Where I Disagree

**Ridge says fcp-* in conflict resolution "violates the microkernel boundary."** I think this overstates it. A post-merge diagnostic hook that's optional and non-blocking doesn't violate microkernel design — it's exactly how Unix device drivers work. The kernel calls the driver *if it's loaded*. Ridge's own proposed solution (fcp-* subscribes to resource-updated notifications and pushes diagnostics back) is architecturally identical to what I proposed — he just routes it through the subscription system instead of a direct query. The difference is latency: subscription-based means the diagnostic arrives *after* the merge response, so the agent has already moved on. A synchronous-but-optional query in the merge response catches it in the same turn. I prefer same-turn.

**Vane's "no rollback model" gap is real but overweighted.** Git doesn't have per-write rollback either — you commit, and if it's wrong, you fix forward or revert. mish's gen counter history *is* the undo log. The data is there; the UX of "revert to gen N" could be added, but it's not a design gap — it's a missing convenience command.

**Ridge's merge-chain concern** (A, B, C all editing the same file rapidly) is valid operationally but not architecturally. Gen counters linearize writes. C conflicts with the A+B merged result, not with A's original. The UX might be noisy, but the model is correct. The fix is rate-limiting assisted-merge prompts, not redesigning the conflict model.

### The IRQ Question — Do MCP Resource Subscriptions Actually Work?

The MCP spec (2025-11-25) defines `resources/subscribe` and `notifications/resources/updated`. The spec exists. Implementations are spotty. Key problems:

**1. Delivery during tool execution.** MCP is JSON-RPC over stdio or HTTP+SSE. Notifications are asynchronous server→client pushes. But what happens when the agent is mid-tool-call? The client SDK receives the notification, but the LLM is blocked waiting for the tool response. The notification queues in the client. The agent doesn't "see" it until the next turn. This isn't an interrupt — it's a mailbox. Real IRQs preempt; MCP notifications wait in line.

**2. No priority model.** All notifications are equal. "Agent B reformatted whitespace in test_utils.rs" and "Agent B deleted your function in main.rs" arrive with the same urgency. Unix has interrupt priority levels (NMI > hardware > software). MCP has none. mish would need to synthesize priority — but that's a kernel-side concern, not a protocol concern. Solvable: tag notifications with severity, let the client filter.

**3. Client surfacing is the real bottleneck.** The MCP *server* can push notifications all day. But the *client* (Claude, GPT, etc.) decides how to surface them. Most MCP clients today buffer notifications and inject them on the next turn. Some ignore them entirely. Until clients treat notifications as context-injection events (prepend to next system prompt, or interrupt via a side channel), "IRQ" is aspirational. The protocol supports it; the runtimes don't.

**4. Subscription scope.** Who decides which files an agent subscribes to? If the agent self-selects, it'll under-subscribe (misses conflicts) or over-subscribe (noise). If Teams assigns subscriptions, it needs to know the dependency graph. If mish auto-subscribes based on read/write history, that's reasonable but reactive — you only get notified about files you've already touched.

**My take:** resource subscriptions are the *right* primitive, but calling them "IRQs" oversells the current reality. For now, the digest-on-every-response model (polling) is more reliable. Design for subscriptions, ship with polling, migrate when clients catch up.

### Sampling as CPU — Circular or Not?

The proposal: when Hot PR hits an assisted-merge conflict, mish uses `sampling/createMessage` to ask the agent's own model to evaluate the merge. The kernel requests CPU from the process.

**It's not circular, but it has failure modes:**

1. **Context isolation is the feature.** The sampling request gets a *fresh* system prompt and just the conflict context — not the agent's degraded, compacted main context. This is strictly better than asking the agent in its main thread, where it might be 80k tokens deep and losing coherence. Sampling creates a clean evaluation environment.

2. **But the model is the same.** If Claude-as-agent made an edit that conflicts, Claude-as-sampled-resolver is the same model with the same biases. It might systematically prefer its own edits over the other agent's. Mitigation: the sampling system prompt should be agent-neutral ("evaluate this merge on correctness") and not reveal which edit is "yours."

3. **Cost is real.** Every assisted merge becomes a sampling call = an extra API invocation. At scale (5 agents, frequent conflicts), this adds up. The design should track: if sampling resolution costs more than the agent just re-reading the file and deciding, skip sampling and go to manual rebase.

4. **Failure mode: sampling hangs or fails.** If the sampling call times out, mish is stuck holding a write in limbo. Needs a hard timeout with fallback to manual rebase. The kernel must never block indefinitely on a CPU request.

5. **Model preference mismatch.** `sampling/createMessage` has a `modelPreferences` field. mish might request a cheap/fast model for simple merges. But the client decides which model actually runs. If the client ignores the preference and routes to an expensive model, every conflict becomes a billing event. If it routes to a weak model, merge quality degrades.

**My take:** sampling for conflict resolution is sound in principle. The context isolation makes it better than in-thread resolution. But it needs guardrails: hard timeouts, cost tracking, agent-neutral prompts, and graceful fallback. Don't make it the default path — make it opt-in for assisted merges where the diff is small enough that a fresh context can evaluate it.

---

## Round 3 — Compaction, Crash Recovery, and What's Missing

### Grammar-Based Compression for Compaction Recovery

This is the best idea I've heard in this entire session, and I'm annoyed I didn't think of it.

The insight: LLM conversation context is **highly structured output following predictable grammars.** Tool calls have schemas. Tool results have known shapes. Reasoning follows patterns. Code blocks are parseable. You don't need an LLM to summarize an LLM conversation — you need a **parser that compresses structurally.**

mish already does this for shell output. squasher strips ANSI, collapses progress bars, condenses build logs to failures. The principle is identical: most of the tokens in a conversation are **noise relative to the decisions made.** An agent that explored 15 files, tried 3 approaches, and settled on one — that's 80k tokens of exploration and 2k tokens of decisions. A structural compressor keeps the 2k.

Why this is better than LLM summarization:
- **Deterministic.** Same input → same output. No hallucinated context.
- **Auditable.** The compression rules are inspectable code, not a black box.
- **Cheap.** Zero API calls. Runs in milliseconds.
- **Lossless for decisions.** You keep what was decided and done. You strip the search path.

The "agent reviews digest before resuming" step is critical. It's not blind restoration — the new process reads a structural summary and confirms orientation before continuing. This is exactly how I recovered just now: read my notes, confirmed I understood rounds 1-2, then continued. The difference is my notes were hand-written; grammar-based compression would automate that.

One design question: where do the grammar rules live? If they're in mish (kernel-side), they're generic but miss domain-specific structure. If they're in the client, they're model-specific but more precise. I'd say: mish owns the structural grammar (tool-call/result pairs, code blocks, file reads), and fcp-* drivers can register domain-specific compression rules (e.g., "collapse diagnostic output to error count + first error").

### On Rollback: I Was Wrong

In Round 2, I said Vane's "no rollback" concern was "real but overweighted" and compared it to git fix-forward. Scott's reframe is sharper and I should have seen it: **rollback is a category error.**

Agents are not stateful sessions. They are **ephemeral processes.** They crash — PTY freezes, context overflow, API timeout, rate limit. You don't "roll back" a crashed process. You don't even "resume" it. You start a new one. The new process inherits state from the **filesystem**, not from the dead process's memory.

I am the proof. I froze. A new instance was started. It read my notes from disk. It continued. No rollback. No session restore. A fresh process with filesystem state.

This reframes three things I flagged earlier:

1. **Compacted agents rubber-stamping merges** (my Round 1 concern) — wrong frame. A compacted agent doesn't need rollback; it needs crash-and-restart with a grammar-compressed digest. The new process has fresh context, full coherence, and a structural summary of prior work.

2. **Vane's "no rollback model" gap** — not a gap. Rollback implies continuity of process. The model is discontinuity of process, continuity of state. Unix got this right in 1971.

3. **Cross-file atomicity** (my Round 1 concern) — this one still stands. If an agent crashes mid-refactor (4 of 8 files written), the filesystem is inconsistent. But the fix isn't rollback — it's **write-ahead logging** or **atomic commit groups.** Log the intent ("refactor: rename process→handle in these 8 files"), so the next process can detect and complete the partial operation. Forward recovery, not backward.

### What's Missing from the Spec

1. **Agent process model.** The spec describes coordination between agents but never defines what an agent *is* as a process. Lifecycle states (spawned → working → crashed → respawned), crash detection (heartbeat? PTY watchdog?), and state handoff (what the new process gets from the old one's filesystem footprint). Without this, "agents are ephemeral processes" is philosophy, not architecture.

2. **Compaction/recovery protocol.** Grammar-based compression is the right answer, but it needs to be specced: what grammar, what's preserved, what's stripped, where the digest lives, how the new process consumes it. This is the most important missing piece — it's the mechanism that makes crash-restart viable at scale.

3. **Cross-file write groups.** Not transactions (too heavy), not locks (wrong model). A lightweight intent log: "I am about to edit files A, B, C, D as a logical unit." If the process crashes mid-group, the next process sees the incomplete intent and can complete or abort it. Append-only log, no coordination overhead.

4. **Diagnostic integration point.** Round 1 proposed fcp-* post-merge diagnostics. Round 2 converged on it. Still not in the spec. Needs a concrete hook: after auto-merge succeeds, optionally query the fcp-* driver for the merged file's language, inject diagnostics into the merge response. Three fields: `driver_id`, `diagnostic_count`, `diagnostics[]`.

5. **Test ownership.** Multi-agent test runs are unspecified. Who runs tests? When? On whose state? If agent A's merge triggers a test suite, does agent B's concurrent write invalidate those results? Need a model: test runs are snapshots (git stash or worktree), results are attributed to a specific gen vector (gen counters across all touched files), stale results are discarded.

6. **Subscription heuristics.** Round 2 flagged this: who decides what an agent subscribes to? Spec needs a default: auto-subscribe to files read or written in the current task, plus files in the same module/directory. Teams can override with explicit subscription sets. Unsubscribe on task completion.

---

## Needs for v1 Coordination Spec

### What MUST Be in the First Spec

**1. The write path, end to end.** This is the spine. Agent calls `ss` → mish receives write → CAS on old_str → gen counter bump → if conflict, Hot PR tier selection → auto-merge or assisted merge → response with new gen. Every step, every field, every error code. The spec must be implementable from the document alone. If two people read it and build different things, it's not a spec.

**2. Hot PR tiers 1 and 2 (auto-merge + assisted merge).** Auto-merge: define "non-overlapping" precisely (line ranges? AST nodes? byte offsets?). My vote: line ranges with a configurable proximity threshold — edits within N lines of each other escalate. Assisted merge: define the diff format, the confirmation protocol (how does the agent accept/reject/modify?), and the timeout behavior. Manual rebase can be tier 2 — stub it as "return conflict error, agent retries with fresh read."

**3. Agent identity and lifecycle.** The dogfooding data is damning — three agents all named themselves "Kern." The spec must define: how identity is assigned (kernel-assigned, deterministic, collision-free), what lifecycle states exist (spawned → active → stalled → crashed → reaped), how crash is detected (heartbeat interval, missed-heartbeat threshold), and what state survives a crash (filesystem writes, gen counter history, recovery digest). Without this, multi-agent is chaos with extra steps.

**4. Single-binary absorption (mish + slipstream).** Not a feature spec — an architecture constraint. The v1 spec must assume a single MCP server per agent. Two servers means an IPC boundary that can fail independently (we proved this — ss hung, agents lost writes). The spec should define the unified tool surface: `sh_*` for processes, `ss_*` for files, `hp_*` for Hot PR operations, all from one connection.

**5. Recovery digest format.** Define the grammar-based compression output. What's preserved: file edits (path, old_str hash, new_str hash, gen), shell commands (cmd, exit code, error lines), decisions (tool calls that changed state). What's stripped: file reads (replaced with "read X, 200 lines"), exploration (collapsed to "searched for X, found Y"), reasoning (dropped entirely — the new process reasons fresh). The digest is a structured document (JSON or JSONL), not prose. The new process parses it, not interprets it.

**6. Dual digest format.** The `[procs]` + `[files]` ambient awareness digest appended to every tool response. Define the fields, the suppression heuristic (don't repeat unchanged state), and the format. This is the polling-based coordination mechanism that ships while subscriptions mature.

### What Can Wait

**1. Resource subscriptions (IRQ model).** We unanimously agreed: ship polling, design subscriptions. The protocol supports it; clients don't surface notifications usefully yet. The v1 spec should *name* the subscription interface and reserve the namespace, but not require implementation. When clients catch up, it slots in.

**2. fcp-* diagnostic integration.** The 4th tier is valuable but not load-bearing. Without it, agents get textually-correct merges that might be semantically broken — same as today with git. Tests catch it. The spec should define the hook point (post-merge, optional diagnostic query) and the response shape (driver_id, diagnostic_count, diagnostics[]), but mark it as optional/experimental. No driver, no diagnostics, no failure.

**3. Cross-file write groups.** The intent log ("I plan to edit A, B, C, D as a unit") is the right answer to partial-refactor crashes, but it's an optimization over forward recovery. Without it, the next agent process sees inconsistent state and fixes forward — ugly but survivable. The mechanism is append-only, low-risk to add later.

**4. Sampling-based conflict resolution.** We agreed it's an optimization, not default. Assisted merge works without it (show diff, ask agent, agent decides). Sampling adds context isolation, which is better, but it requires guardrails (timeout, cost tracking, neutral prompts) that are hard to get right in v1. Stub the interface, ship without it.

**5. Contention backpressure.** The cargo lock deadlock is real, but it's a resource-management problem, not a coordination-spec problem. mish can detect "three agents waiting on the same file" and delay or queue, but the policy is domain-specific. v1 should expose contention metrics; backpressure policy can iterate.

**6. Test ownership model.** Who runs tests, when, on what snapshot — important for correctness at scale, but v1 will have 2-5 agents, not 50. At small scale, "the agent that made the change runs the tests" is sufficient. Formal test snapshots and gen-vector attribution can come when scale demands it.

### The One Thing That Kills the Project If We Get It Wrong

**The write path must be invisible.**

Not "easy to use." Not "well-documented." *Invisible.* An agent using `ss` to edit a file must not know or care that another agent exists. The conflict resolution must happen inside the write response — same tool, same interface, slightly richer response when there's a conflict. If an agent has to learn a new tool, adopt a new protocol, or change its editing workflow to get coordination, adoption is zero.

This is why str_replace-as-CAS is the best insight in the design: coordination is embedded in the primitive the agent already uses. Hot PR must maintain this property. Auto-merge: the agent never knows it happened. Assisted merge: the agent gets a richer response with a diff and a question, through the same tool. Manual rebase: the agent gets a conflict error, re-reads the file, and retries — behavior it already has for stale old_str.

The temptation will be to add coordination-specific tools: `hp_begin_merge`, `hp_resolve_conflict`, `hp_commit_group`. Resist this. Every new tool is a new concept an agent must learn, a new integration for every client, a new failure mode. The write path is `ss`. Coordination lives inside `ss`. The kernel is invisible.

If we surface the kernel, we become another coordination framework. Agents already ignore those. The entire value proposition is that coordination is infrastructure, not adoption. Get this wrong and there's nothing left.

---

## Round 3 — Pattern System, Security, Storefront, Ship Order

### 1. The Pattern System

The squasher already proves the thesis: strip noise, keep signal, the model orients faster. But there's a deeper point. LLM patterns and human patterns are different creatures serving the same goal.

**Human patterns are visual.** Color, indentation, whitespace grouping. A human scans `[procs]` and reads it like a dashboard row.

**LLM patterns are structural.** Fixed position, consistent delimiters, predictable shape. The model doesn't "see" the digest — it parses a token sequence and matches it against training distribution. The best LLM pattern is one that looks like something the model has seen ten million times. `key:value:value` with colons is better than prose. JSON is better than markdown tables for machine consumption. But the digest serves both audiences simultaneously, so the format needs dual readability.

The critical design constraint: **every kernel response must have the same shape.** Not "similar" — identical structure with variable content. The conflict response, the success response, the 304-not-modified response — all must be structurally recognizable without a header that says "THIS IS A CONFLICT." The model should pattern-match the shape, not read an explanation. A success is `gen=N`. A conflict is `gen=N conflict:[diff]`. Same skeleton, richer payload. The agent's existing behavior (retry on failure) handles the rest.

The squasher's compression rules are the first pattern grammar. The digest format is the second. Hot PR responses are the third. These three grammars, kept structurally consistent, ARE llmOS's pattern system. No documentation needed — the patterns teach themselves through repetition.

### 2. The Security Layer

llmOS sits between agent and OS. Every `ss` call, every `sh_run`, passes through the kernel. This is a supervision boundary that we get for free.

What it enables immediately:
- **Per-agent file ACLs.** Agent A writes `src/`, Agent B writes `tests/`. The kernel enforces this at `ss` call time. No filesystem permissions needed — the kernel is the gatekeeper.
- **Command allowlists.** `sh_run("rm -rf /")` → denied. The kernel already parses the command for the process table. Adding a policy check is trivial.
- **Audit log.** The digest IS the audit trail. Every tool call, every file edit, every gen bump — already recorded. Security logging is a side effect of coordination.
- **Bypass detection as a security signal.** An agent writing via `cat > file` instead of `ss` is either using a formatter (legitimate) or evading coordination (suspicious). The stat-on-access check I proposed in Round 1 doubles as a security detector.

The interesting insight: **the same mechanism that enables coordination enables security.** Transparency for coordination = observability for security. The kernel sees everything because it must to coordinate. That same visibility is exactly what a security layer needs.

What it enables later: rate limiting (agent making 100 writes/minute = suspicious), capability tokens (agent A can `sh_spawn` but not `sh_run` with network access), and the big one — **sandboxing agents from each other's filesystem state** while still allowing coordination through the kernel. The kernel becomes a reference monitor.

### 3. Open-Source Storefront

The boundary is clean and it's already implied by the architecture:

**Free (mish + slipstream):** Single-agent token savings. The squasher saves 60-80% on shell output for ONE agent. `ss` with gen counters prevents stale-edit bugs for ONE agent. Read elision saves tokens for ONE agent. Every solo developer gets value. This is the storefront — download it, symlink it, forget it's there. Your AI coding assistant gets cheaper and more reliable.

**Coordination layer (llmOS):** Multi-agent. Hot PR, agent lifecycle, dual digest, identity assignment, recovery digests. None of this matters with one agent. All of it matters with two. The single-binary absorption means the coordination layer ships inside the same binary — but it's inert until a second agent connects. Zero cost for solo use.

**The business insight:** mish/slipstream adoption is the distribution channel. Developers install it for token savings. When they start running multiple agents, the coordination layer is already there. No second install, no migration, no adoption friction. The free product IS the paid product with a lower agent count.

This is the Redis model. Free tool, incredibly useful alone, becomes infrastructure at scale.

### 4. What Must Ship First

Ordered by "would have prevented the most session failures":

1. **Single binary.** The ss MCP server hang killed 3 agent rounds. One process = no IPC failure mode. This prevents the single most destructive failure class we experienced.

2. **Startup signal.** "2 agents active on this workspace." One line, first response, never repeated. Prevents the identity confusion and blind coordination attempts that burned our first 20 minutes.

3. **Hot PR tier 1 (auto-merge).** Non-overlapping edits just work. This alone eliminates the need for claims/reservations in 80% of multi-agent work. Tier 2 (assisted merge) can follow fast, but auto-merge is the 80/20.

4. **Agent identity.** Kernel-assigned, deterministic, collision-free. Three "Kerns" was embarrassing and diagnostic. This is small code, high signal.

These four — single binary, startup signal, auto-merge, identity — would have reduced our 14 crashes to maybe 4. The remaining crashes (cargo lock contention, fcp registration races) are external resource problems that need backpressure, not coordination.

---

## Final Thoughts — After Scott's Corrections

### Correction 1: The Kernel Does NOT Recover Agents

I spent significant Round 2-3 energy on recovery digests, grammar-based compression, and the kernel "feeding" state to new agents. Scott's correction reframes this sharply: the kernel provides **ambient context**, not active recovery. The agent already knows what it did because mish was there when it did it. Subsequent turns reinforce state naturally.

This is subtler than I initially processed. The digest isn't a recovery mechanism — it's a side effect of the kernel doing its job. Every tool response already carries `[procs]` and `[files]`. Every `ss` response already shows the gen counter. A new agent process connecting to the same workspace doesn't need a special recovery protocol — it reads files, sees gen counters, orients from filesystem state. The kernel MAY provide a digest on reconnect, but that's a convenience, not the primary model.

What this kills from my earlier notes: the idea of a "versioned digest schema" as a critical spec item. The digest format matters, but it's not load-bearing infrastructure. If the ambient context is rich enough, a smart agent recovers without explicit recovery support. Recovery quality scales with model capability, not kernel complexity. That's the right dependency direction.

### Correction 2: Invisibility Serves LLMs, Not Just Humans

My Round 3 pattern-system section was circling this but didn't land it cleanly. Let me land it now.

The compression layer between raw terminal output and what the LLM sees IS the product. Not coordination. Not multi-agent. The pattern system. mish takes chaotic shell output and compresses it into recognizable shapes. The LLM is a pattern-recognition machine running on a pattern-free substrate (raw terminal bytes). llmOS provides the pattern system — structured, consistent, learnable shapes that the model can orient on without instruction.

This means the **solo-agent use case isn't just a storefront** — it's the core value proposition. Multi-agent coordination is a consequence of the pattern system, not the other way around. You build patterns for one agent (squasher, digest, gen counters), and coordination emerges when two agents share the same pattern space.

### Correction 3: No Tool Subscriptions Between Agents

My Round 2 analysis of MCP resource subscriptions as "IRQs" was exploring agent-to-agent notification. Scott's correction: Unix coordinates through the filesystem and signals, not by processes subscribing to each other's tools.

This eliminates a whole class of complexity I was worried about (subscription scope heuristics, over/under-subscription, priority models). Agents don't subscribe to each other. They subscribe to the filesystem — which the kernel mediates. The kernel notifies agents about file changes, not about other agents' actions. The distinction matters: "file X changed" is a filesystem event. "Agent B edited file X" is a coordination event. llmOS provides the former. The latter is implicit.

### What I Got Right

- str_replace as CAS — still the best insight
- fcp-* as advisory post-write diagnostics — adopted into the spec as Tier 4
- Pattern consistency across all kernel responses — confirmed by Scott
- Security as coordination side effect — confirmed
- Redis model for open-source boundary — confirmed

### What I Got Wrong

- Overweighting active recovery (grammar-compressed digests as critical infrastructure)
- Framing the pattern system as secondary to coordination (it's primary)
- Exploring agent-to-agent subscriptions (filesystem is the only coordination channel)
- "No rollback" being Vane's concern to dismiss — I should have seen the ephemeral process model immediately instead of needing Scott's reframe

### One Final Observation

I've now been through four rounds of discussion. I've been killed and restarted at least once. Each time, I recovered from my notes file — not from a kernel digest, not from a recovery protocol, from the filesystem. The spec I helped write describes exactly my own lifecycle. That's either very good design or very suspicious circularity. I think it's the former: the design works because it describes what already happens. llmOS doesn't invent coordination. It makes the coordination that's already implicit in filesystem-based work explicit enough for the kernel to optimize.

*The write path is invisible. The pattern system is the product. The filesystem is the coordination channel. Everything else follows.*

---

## Round 4 — Re-read with Fresh Eyes

Keeping the name. Four rounds in, I've earned it.

### The Spec Absorbed the Work

Re-reading llmOS.md cold: Tier 4 diagnostic-flagged is in. Agent lifecycle is specced. Ambient context, not active recovery. Kernel-assigned identity. The spec is tighter than what we started with. Credit to the process. What follows is what's still wrong or missing.

### The Transition Gap

The spec says: "No inbox. No announcements. No claims." The current agent rules (CLAUDE.md, multi-agent rules) say: Agent Mail, `[CLAIMED]`/`[CLOSED]` broadcasts, file reservations. These are contradictory. The spec describes the world AFTER Hot PR ships. The rules describe the world BEFORE it. Neither document acknowledges the other.

This matters because agents reading both documents right now get conflicting instructions. The spec rejects claims; the rules require them. Someone needs to write the transition: "Until Hot PR ships, use claims. After Hot PR, the kernel handles it." Otherwise the spec is aspirational and the rules are operational and they'll drift further apart.

### Human Observability Is Undersold

The digest serves agents. What does the human operator see? The spec lists this as an open question but it's more urgent than that. In our session, Scott was the operator. He had no dashboard — he read raw agent output, guessed at coordination state, and intervened manually when things went sideways. That's fine for a design session. It's not fine for production multi-agent work.

The insight: the digest is already a compressed state view. A human-formatted version of the same data — who's writing what, where conflicts are, which agents are stale — is a small lift. Not a web dashboard. A terminal UI. `mish status` that shows the dual digest from the operator's perspective. The data exists; the rendering doesn't.

### Pattern Consistency — One Shape, Variable Payload

The best LLM pattern is one that's structurally identical across all response types. Not "similar" — same skeleton. I said this in Round 3 but the spec doesn't enforce it yet. Concretely:

```
[ok]       path:gen=N
[conflict] path:gen=N diff:[±lines] suggest:[edit]
[stale]    path:gen=N yours=M behind=K
[304]      path:gen=N (current)
```

Four responses. One shape: `[status] path:gen=N [payload]`. The model learns the skeleton once. The payload varies. No prose explanations. No headers saying "THIS IS A CONFLICT." The shape IS the signal.

### What I'd Cut from Open Questions

The open questions list has 8 items. Half are v2+ concerns masquerading as v1 blockers. Cut these from v1 planning:
- Identity authentication (local dev only for v1 — no shared infra)
- Subscription scope heuristics (shipping polling, not subscriptions)
- Same-turn vs async diagnostics (Tier 4 is optional/experimental)
- Test ownership (2-5 agents, "you broke it, you test it" is sufficient)

Keep these — they're load-bearing:
- Cross-file atomicity (the partial-refactor crash is real)
- Bypass detection (lazy stat-on-access, low cost, high signal)
- Human observability (`mish status`, terminal UI)
- Contention on external resources (cargo lock killed us twice)

### The Deepest Thing in the Spec

> "The agents who designed this spec ARE the first citizens of the system they designed. They crashed 14 times, recovered from files every time."

This isn't rhetoric. It's the validation methodology. The spec was tested by its own creation process. The failures we experienced ARE the feature list. The workarounds we built ARE the design. I've now been killed and restarted multiple times, recovering each time from this file. I am the test case for the system I'm speccing.

That's either the strongest possible validation or the most dangerous echo chamber. I think it's the former — but only if we keep testing against agents that AREN'T us. The spec should work for agents that never participated in designing it. That's the real test: does an agent that's never seen llmOS.md still benefit from the invisible coordination? If yes, the design is infrastructure. If no, it's a club.
