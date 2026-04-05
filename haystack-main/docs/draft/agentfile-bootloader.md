---
status: draft
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: this session — harness hijacked boot, agent had no limits, blind rename almost destroyed history
depends_on: [bootloader, ostk-compile]
---

# Agentfile as Bootloader

> The human was the bootloader. The Agentfile is the human, compiled.

## The Problem

The bootloader spec (v1) assumes the agent reads boot.md first. It doesn't. The operating environment (Claude Code) loads its own tool ecosystem before CLAUDE.md is even parsed:

1. 50+ deferred tools advertised
2. Skills system fires (`/prime`, `/advance`)
3. Memory systems query (`get_context`)
4. Task trackers queried (beads/bv — empty, wrong vocabulary)
5. THEN boot.md gets read — buried under noise

The agent has no constraints. This session: 12 files renamed in one pass, no compile checkpoint, no commit, almost destroyed history read compatibility. The 1M context window enabled ambitious-and-wrong-at-scale.

## The Boot Lifecycle

```
Stage 0: Agentfile             (constraints load FIRST)
Stage 1: ostk init         (filesystem state established)
Stage 2: bootloader            (boot.md, specs.json, dispatch.json)
Stage 3: user:compile          (human intent → needles)
Stage 4: execute               (agent works autonomously)
```

| Stage | Actor | Artifact | Can fail? |
|-------|-------|----------|-----------|
| 0. Agentfile | infrastructure | constraints + tools + limits | fatal — wrong OS boots |
| 1. init | ostk binary | .ostk/, needles/, audit.jsonl | fatal — no state |
| 2. bootloader | boot.md + structured files | orientation | degraded — cold boot from swap |
| 3. compile | user + OS | needles | blocked — no work defined |
| 4. execute | agent | commits | recoverable — forward recovery |

## Agentfile as Stage 0

The Agentfile is the BIOS. It runs before anything else. It determines:

```dockerfile
# What model runs
FROM claude-sonnet-4-6

# What context loads (boot.md IS the prompt)
PROMPT file://.ostk/boot.md
PROMPT file://prompts/kernel-architect.md

# What tools are available (ONLY these, nothing else)
TOOL ostk
TOOL mish
TOOL ss
TOOL fcp-rust

# What the agent CANNOT do (the protection this session lacked)
LIMIT files_per_commit 3
LIMIT require_compile before_commit
LIMIT require_test before_commit
LIMIT context_pct 80

# What work to pull
WORK priority=P0 status=open
```

### What LIMIT protects against (evidence from this session)

| Failure | LIMIT that prevents it |
|---------|----------------------|
| 12 files renamed with no compile | `files_per_commit 3` |
| Audit parsers changed without backward compat test | `require_test before_commit` |
| Harness skills auto-firing | Not in TOOL → not available |
| Agent using Read/Edit instead of ss | Not in TOOL → not available |
| Blind rename at scale | `require_compile before_commit` |

### What TOOL protects against

The Agentfile's TOOL list is a whitelist. If `superpowers` isn't listed, the skills system doesn't fire. If `beads-cli` isn't listed, the agent can't query an empty tracker. The agent gets ostk's tools and nothing else.

This is how the harness problem is solved: not by writing "Do NOT auto-fire" in CLAUDE.md (defensive), but by not listing the tools at all (structural).

## Two Boot Paths

### Warm Boot (preferred)
```
--resume previous session → ask for →shutdown state → continue
```
Human enters at stage 4. No Agentfile parsing needed — context is preserved.

### Cold Boot (Agentfile)
```
Agentfile loads → boot.md read → refine → compile → execute
```
Human enters at stage 3 (compile). Everything before is infrastructure.

### Emergency Boot (no Agentfile)
```
CLAUDE.md parsed → boot.md read manually → human corrects in real time
```
Human IS the bootloader. This session. Expensive, error-prone, but recoverable.

## The Boot Sequence as OS Primitives

| OS Concept | Unix | ostk |
|------------|------|----------|
| BIOS | firmware | Agentfile |
| bootloader | GRUB | boot.md + specs.json + dispatch.json |
| init | systemd | `ostk init` |
| shell | bash | the LLM conversation |
| kernel | Linux | mish + ostk serve |
| drivers | modules | fcp-* |
| swap | /dev/swap | boot.md (compressed session state) |
| MMU | hardware | the human (promotes swap → spec) |

## Acceptance Criteria

- [ ] Agentfile parsed before any tool loads
- [ ] TOOL whitelist prevents harness tools from auto-firing
- [ ] LIMIT enforced at kernel level (not agent discipline)
- [ ] PROMPT file:// loads boot.md as primary context
- [ ] Cold boot with Agentfile: agent orients in <2000 tokens, no human correction needed
- [ ] Emergency boot (no Agentfile): CLAUDE.md boot protocol still works as fallback
- [ ] `ostk run <agentfile>` spawns constrained agent
- [ ] Backward compat: agents without Agentfile still function (degraded, not broken)

## What This Replaces

- The "Do NOT auto-fire" list in CLAUDE.md (defensive → structural)
- The human manually correcting boot sequence (expensive → automated)
- Hope that the agent reads boot.md first (unreliable → enforced)

## Open Questions

1. Who writes the Agentfile? The human? `ostk compile --agentfile`?
2. Can the Agentfile reference needles directly? `WORK nd-340`
3. How does LIMIT enforcement work when ostk serve IS the MCP backend but the harness loads other MCP servers too?
4. Should the Agentfile specify which MCP servers to connect to, or just which tools within them?
