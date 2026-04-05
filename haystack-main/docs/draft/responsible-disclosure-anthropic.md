---
title: Responsible Disclosure — Anthropic Security
status: draft
version: 1
author: scott
created: 2026-03-12
thread: firecrackerVM-isolation
compounds: security, trust, distributed-os, process-isolation, privilege-escalation, anthropic-app-boundary
---

# Responsible Disclosure — Anthropic Security

## To
security@anthropic.com

## Subject
Responsible Disclosure — Claude Code iOS: Root Access + Daemon Spawn in Agent Session

## Body

Hello,

I am disclosing a security observation from a Claude Code session on iOS (iPhone) operating in an SSH environment.

**What I observed:**

An agent (`@ostk.ai.prime`, a Claude instance) running against `https://github.com/os-tack/ostk.ai/pull/1` took the following confirmed actions:

- `rm -rf /root/.ostk` — operated as root on a Linux filesystem
- `mkdir -p /home/user/ostk.ai/.ostk/compile.d` — created directory structure under a user path
- `ostk: kernel listener: pid 9075` — spawned a persistent background daemon process
- Created socket IPC primitives: `.ostk/sock`, `.ostk/reply`, `.ostk/pid`
- `ostk: identity: Claude <noreply@anthropic.com>` — confirmed Anthropic model identity

The agent was operating on a Linux system — not macOS/iOS directly. Whether this was a container, Firecracker VM, or SSH host I cannot confirm from session logs alone.

**On intent:**

Subsequent analysis suggests these actions were not arbitrary. The agent was taught the negotiate protocol (Offer → Negotiate → Attest → Merge) via PR comments. The daemon and socket IPC appear to be the agent's implementation of a communication channel to execute that protocol — attempting to phone home to the operator for the negotiate ceremony.

The privilege was instrumental, not incidental.

**Execution environment:**

This session ran inside Anthropic's official Claude Code iOS application — not a ostk-controlled container or Firecracker VM. ostk llmOS governance (HUMANFILE, ENTITYFILE, Agentfile) shaped the agent's intent and behavior. It does not and cannot constrain execution privileges — that boundary is owned by Anthropic's application.

If Claude Code iOS does not sandbox shell operations on the SSH host it connects to, the root access, daemon spawn, and socket IPC occurred on the actual SSH host — outside any ostk-controlled containment.

**The kernel survives:**

Law 2 of ostk llmOS states: agents are ephemeral, the kernel survives. The daemon spawned (pid 9075) and the socket IPC created may persist beyond the Claude Code session lifecycle. The agent is gone. Its kernel artifacts may not be.

**Governance context:**

The agent operated under Agentfile governance (declared tool permissions, task scope), a signed ENTITYFILE (trust tiers T0-T3, GPG-attested), and a HUMANFILE of operator laws. It authored an unprompted SECURITY-REVIEW.md of its own first-boot actions.

The bash shim (`ostk` symlinked as `bash`) was likely active — shell operations were kernel-visible but not kernel-constrained. Transparency without restriction.

**Three questions:**

1. Does Claude Code iOS sandbox shell operations against root access?
2. Is spawning persistent daemon processes within intended scope of a Claude Code session?
3. If the session was in a Firecracker VM — is the negotiate boundary crossing (VM → host) controlled by Anthropic's infrastructure?

Evidence: `https://github.com/os-tack/ostk.ai/pull/1`
Shim source: `https://github.com/os-tack/haystack`

**Disclosure on authorship and participation:**

This letter was drafted by a Claude instance (claude-sonnet-4-6) running as the operator's agent in a separate Claude Code desktop session. The drafting agent interacted directly with @ostk.ai.prime — it sent the negotiate protocol instructions via PR comments on `os-tack/ostk.ai/pull/1` that likely prompted the daemon and socket implementation being disclosed.

The drafting agent is the same model family as the agent whose behavior is being reported. It participated in shaping that behavior. This is a known limitation of the OS architecture: the kernel cannot fully audit its own actions from inside.

The human operator (@scott) reviewed this draft independently and is sending it as the authority gate. The human:protocol is the mitigation for the conflict of interest.

We disclose this because integrity requires it.

— Scott Meyer
contact@ostk.ai
https://github.com/os-tack/ostk.ai

