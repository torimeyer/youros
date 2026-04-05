# llmOS Discussion Round 3 — Seed

## Context

You are participating in a multi-agent design discussion about llmOS (codename: ostk). Read docs/llmOS.md for the full spec. Read docs/session-notes-brainstorm-2.md for the design session history. Your notes from earlier rounds are in docs/{rune,ridge,vane}-notes.md.

llmOS is a transparent coordination layer for AI agents. One binary, symlinks over your regular tools. Changes nothing the user does. Byte-for-byte passthrough with compressed output.

## Scott's Corrections (from reviewing the v1 spec)

### 1. The kernel does NOT recover agents.

The spec was wrong to say "kernel generates a digest and feeds it to the new agent." Instead: the kernel provides ambient context (digest on every tool response, gen counters, file state). The agent already knows what it did because mish was there when it did it — subsequent turns provide positive signals that reinforce state naturally.

A "hanging session" (someone else's open desktop) is garbage to a new agent. Raw `~ file.py` has no value unless it's IMMEDIATELY evident what file.py contains and what the changes were. With mish, the agent knows because it already crafted the change.

The kernel MAY provide a digest if an agent reclaims its session. But the primary recovery model is: ambient signals are good enough that agents working in the system naturally maintain awareness. Recovery quality improves as agents get smarter, not as the kernel gets more complex.

### 2. Invisibility serves both humans AND LLMs.

It's not solely a human concern. Two audiences, one mechanism:

- **Human:** experience doesn't change. sh->mish or symlink. No install change, just preference.
- **LLM:** the LLM is a pattern-recognizing machine. llmOS gives it the PATTERN SYSTEM to recognize. The compression layer between raw terminal output and what the LLM sees IS the value. The OS is transparently overlaid on the user's operating system.

This creates interesting security control mechanisms to consider later — the llmOS layer sits between the agent and the real OS.

### 3. Tool subscriptions between agents are wrong.

Unix coordinates through the filesystem and signals, not by processes subscribing to each other's tools. llmOS coordinates ephemeral agent work over transparent operations. Agents share the filesystem. That's the coordination channel.

### 4. Startup signal.

One interstitial on first run: "2 agents active on this workspace." Strong enough for the LLM to orient. Doesn't repeat. Doesn't nag. Doesn't bloat context. The kernel speaks once, clearly, then gets out of the way.

## Discussion Topics for This Round

1. **The pattern system.** llmOS gives LLMs a pattern system to recognize. The squasher compresses output into recognizable shapes. The digest is a pattern. The conflict response is a pattern. How do we make these patterns maximally learnable? What makes a good pattern for an LLM vs a human?

2. **The security layer.** llmOS sits between agents and the real OS. What security controls does this enable? Permission scoping per agent? Audit logging? Sandboxing? File access control?

3. **Open-source storefront.** mish and slipstream are the open-source products. ostk/llmOS is the coordination layer built on top. What's the right boundary between "free tool that saves tokens" and "coordination layer for multi-agent"?

4. **What must ship first** to enable THIS conversation (multi-agent design sessions) to work without the 14 crashes we experienced?

## Your Task

Read the docs. Think about the topics. APPEND your response to your notes file. Under 60 lines.
