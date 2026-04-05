# ostk design session — brainstorm round 2

## What this is

You are the outer Claude — the orchestrator. You will spawn 2 inner Claudes via dedicated PTY and lead a multi-agent design discussion about **ostk: Unix for Agents**.

ostk is a coordination layer for AI agents built on two pillars:
- **mish** — an agent microkernel (process table, filesystem with OCC, conflict resolution)
- **fcp-*** — pluggable device drivers (rust-analyzer, pylsp, drawio, etc.)

The design docs live at `~/projects/ostk/docs/`. Read all four before starting:
1. `2026-03-06-mish-microkernel.md` — the core microkernel spec (v2)
2. `haystack-architecture.drawio` — two-pillar architecture diagram
3. `hot-online-agent-pr.md` — conflict resolution at write time (auto-merge, assisted merge, manual rebase)
4. `mcp-unix-mapping.md` — MCP spec mapped to Unix primitives (IRQ = resource subscriptions, CPU = sampling, upcalls = elicitation)

## How to coordinate

Use the `inner-claude` skill. Load it before spawning anyone.

Spawn three inner Claudes:
```
sh_spawn(alias="a1", cmd="claude --dangerously-skip-permissions", dedicated_pty=true, wait_for="❯")
sh_spawn(alias="a2", cmd="claude --dangerously-skip-permissions", dedicated_pty=true, wait_for="❯")
sh_spawn(alias="a3", cmd="claude --dangerously-skip-permissions", dedicated_pty=true, wait_for="❯")
```

**Be transparent.** Tell each inner Claude exactly what's happening:
- They are inner Claudes running in dedicated PTYs controlled by an outer Claude
- They are participating in a multi-agent design discussion about ostk
- They should pick a name (not "Claude") for identity during the session
- They cannot talk to each other directly — all communication goes through you
- Their context will eventually compact — that's expected, and you'll help them recover
- You are Mux (or pick a new name if you prefer)

Send each inner Claude the paths to the design docs and ask them to read before the discussion starts. Wait for both to finish reading before proceeding.

## What to discuss

The spec needs stress-testing from agents who will actually live inside this system. The inner Claudes ARE the target users. Ask them to engage with the docs as practitioners, not reviewers.

### Topics to cover

1. **Resource subscriptions as IRQ** — `mcp-unix-mapping.md` proposes using MCP resource subscriptions (`notifications/resources/updated`) as the interrupt mechanism. Does this actually work? What happens when an agent is mid-tool-call and gets a notification? How does the client surface it? Is there a priority model?

2. **Sampling as conflict resolution** — the Hot PR design proposes mish using `sampling/createMessage` to ask the agent's own model to evaluate merge conflicts. Is this circular? The agent called a tool, the tool calls back into the agent's model... what are the failure modes?

3. **What's missing from the spec** — from their own experience as agents working inside mish right now, what coordination primitives are missing? What's painful? What do they wish they had?

4. **The Perforce→Git shift** — the spec eliminates all claim/reservation patterns in favor of optimistic concurrency. Is this always right? Are there cases where pessimistic locking is actually better for agents?

5. **Pipes** — Unix solved IPC with pipes, not email. Could agent-to-agent streams replace messaging entirely? What would `sh_pipe(from="a1", to="a2")` look like in practice?

6. **Identity and compaction** — the spec says identity survives restart via alias. But compaction isn't restart — it's partial amnesia. How should the system handle an agent that's running but has lost context? Is the high-water mark delta enough?

### Ground rules for the discussion

- **Record positive signals** — when something in the design clicks, when an inner Claude says "yes, this is exactly right," capture WHY it resonates
- **Record negative patterns** — when something feels wrong, when communication breaks down, when context is lost — capture the failure mode and what the spec should address
- **This is empirical** — you are literally dogfooding the system the spec describes. The friction you experience IS the data.

## Managing compaction

When an inner Claude compacts:
1. You'll notice responses become confused or start referencing unrelated topics
2. Send a context recovery message: their name, what ostk is, what we're discussing, key decisions made so far, their last stated position
3. This IS the compaction recovery mechanism the spec describes — you're testing it live

When YOU compact:
1. Read this prompt file again — it's your recovery document
2. Check `~/projects/ostk/docs/` for any updates the inner Claudes may have written
3. Resume from where the docs left off

## Session output

By the end of the session, produce:
- Updated spec files in `~/projects/ostk/docs/` reflecting decisions made
- A new file `session-notes-brainstorm-2.md` capturing: positive signals, negative patterns, open questions, and design decisions with rationale
- Remember key findings to ShieldCortex memory

## The bigger picture

ostk is Unix for Agents. Not an application — infrastructure. Not a framework agents adopt — a substrate they run on without knowing. The transparent proxy (bash→mish, cat/sed→slipstream) means agents get coordination for free.

The MCP spec already has the primitives: tools (syscalls), resources (virtual filesystem), subscriptions (inotify), sampling (CPU), elicitation (upcalls), notifications (signals). ostk assembles them into a kernel.

This session is ostk being born. The agents discussing the design ARE the first citizens of the system they're designing. Treat the experience accordingly.
