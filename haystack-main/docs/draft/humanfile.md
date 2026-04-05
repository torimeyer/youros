---
title: Humanfile
status: draft
version: 1
author: scottmeyer + orchestrator (3-perspective round table)
created: 2026-03-08
evidence: CLAUDE.md is the hand-written Humanfile. audit.jsonl contains the raw signal. intent-signal-gradient.md and bidirectional-convergence.md are the theory.
depends_on: [agentfile-bootloader, intent-signal-gradient, bidirectional-convergence, human-in-the-loop]
---

# Humanfile

> The Agentfile is the agent, compiled. The Humanfile is the human, compiled. The kernel runs between them.

## Round Table

### Privacy Architect

The Humanfile contains patterns, not content. "Human corrects after options are presented" is safe. "Human corrected agent about the Jenkins deploy key" is not — it leaks infrastructure. The line: **behavior patterns YES, contextual details NO.**

Corrections are the hardest case. `:correct X` contains X — the thing the agent got wrong, which may reference private architecture, names, secrets. The compiler must extract the *pattern* (agent presented options when it should have executed) and discard the *instance* (the specific deploy key discussion).

Ownership is non-negotiable: the Humanfile lives on the human's machine, period. It is not telemetry. It is not uploaded. It can be shared across projects by the human's choice (symlink, copy), never by the OS's initiative. Cross-project sharing reveals workflow across security boundaries — the human decides.

The Humanfile MUST be human-readable. If the human can't audit what the OS learned about them, the OS is a surveillance tool. Plain text. No embeddings. No opaque vectors.

### Compiler Engineer

Input: `audit.jsonl` — every tool call, every correction, every timing signal. The raw material is already on disk.

The compiler (`ostk learn human`) runs offline, after sessions. Not during. During-session learning is the agent's job (bidirectional convergence). The Humanfile captures what *persists across sessions* — the patterns that survive context death.

Overfitting is the core risk. One angry session where the human typed in ALL CAPS doesn't mean "always treat caps as baseline." The compiler needs a frequency threshold: a pattern must appear across N sessions (N >= 3) before it's compiled. Single-session anomalies stay in audit, not in the Humanfile.

Output format: sections that map to kernel behavior. Not prose — structured entries the kernel can act on without LLM interpretation. The boot sequence reads the Humanfile the same way it reads the Agentfile: mechanically.

Versioning: append-only with timestamps. Patterns are never deleted, only superseded. `v1: presents options (2026-03-01)` -> `v2: executes without options (2026-03-08)`. The human evolves. The Humanfile tracks the evolution, doesn't freeze it.

### Product Designer

Without a Humanfile, every session is a cold start. The agent presents options. The human corrects. The agent over-explains. The human interrupts. 20 minutes of calibration before productive work. This is the current CLAUDE.md experience — it reduces calibration from 20 minutes to 5, but 5 is still friction.

With a Humanfile, the agent boots *already converged*. It knows not to present options. It knows `:` means hard demand. It knows typos are speed, not sloppiness. The first message is productive, not calibratory.

The Agentfile constrains what the agent CAN do. The Humanfile calibrates HOW the agent does it. The Agentfile is a firewall. The Humanfile is a tuning fork.

The upgrade path from CLAUDE.md: `ostk learn human --seed CLAUDE.md` bootstraps the Humanfile from the hand-written version, then audit data refines it. CLAUDE.md doesn't disappear — it remains the human-editable override. The Humanfile is the compiled version. When they conflict, CLAUDE.md wins. The human's explicit word beats the OS's inference.

Who writes it? Both. The OS compiles it from evidence. The human can edit it directly (`humanfile.toml` is a file like any other). Direct edits are highest-priority entries — they skip the frequency threshold because the human said so explicitly.

---

## Spec

### Format

```toml
# ~/.ostk/humanfile.toml

[identity]
# Human-provided, never inferred
name = "scott"

[signals]
# Compiled from intent-signal-gradient observations
soft = "."              # exploring, probing
hard = ":"              # demanding, correcting
flow = "->"             # sequential next
boost = "=>"            # elevated priority

[preferences]
# Pattern: key = behavior, evidence = session count
execute_dont_ask = { value = true, sessions = 12 }
ci_verified_binaries = { value = true, sessions = 8 }
no_options_when_intent_clear = { value = true, sessions = 14 }
typos_are_speed = { value = true, sessions = 6 }

[corrections]
# Patterns extracted from :correct events
# Format: what the agent did wrong -> what it should do
present_options = "execute"           # 14 occurrences
explain_before_acting = "act_first"   # 9 occurrences
ask_permission = "do_it"              # 7 occurrences

[communication]
# Compiled from token measurements across sessions
early_session_tokens = 12             # avg tokens per human message, first 10 msgs
late_session_tokens = 4               # avg tokens per human message, after convergence
convergence_turn = 8                  # avg turn where corrections drop below 1/5 msgs

[context]
# Degradation profile (from context-degradation spec)
sharp_ceiling_pct = 55                # context % where drift begins
shutdown_pct = 70                     # context % where human should trigger shutdown

[version]
compiled = "2026-03-08T14:30:00Z"
source_sessions = 23
source_corrections = 147
```

### Generation (`ostk learn human`)

```
ostk learn human [--seed CLAUDE.md] [--sessions N] [--dry-run]
```

1. Read `audit.jsonl` — extract correction events, timing data, signal patterns.
2. If `--seed`, parse CLAUDE.md for explicit preferences (these become entries with `sessions = "explicit"`).
3. Apply frequency threshold: pattern must appear in >= 3 sessions.
4. Merge with existing `humanfile.toml` — new entries added, existing entries updated with new counts. No deletions.
5. If `--dry-run`, print diff. Otherwise, write `humanfile.toml`.

### Privacy Rules

1. **Patterns only.** "Human corrects when agent presents options" is stored. The specific option text is not.
2. **Local only.** `humanfile.toml` never leaves the machine. No upload, no sync, no telemetry.
3. **Human-readable.** TOML. No embeddings, no vectors, no binary formats.
4. **Human-auditable.** `ostk show human` prints the current Humanfile with explanations.
5. **Human-deletable.** `rm ~/.ostk/humanfile.toml` and it's gone. The OS degrades to CLAUDE.md, not to broken.
6. **No content.** File paths, variable names, error messages, secrets — none of these appear. Only behavioral patterns.

### Versioning

Append-only evolution. Each `learn human` run timestamps its compilation. Previous patterns are never deleted — they're superseded when the count for a contradicting pattern exceeds them. The human changes. The Humanfile tracks direction, not snapshots.

### Relationship to CLAUDE.md and Agentfile

| File | Written by | Contains | Overrides |
|------|-----------|----------|-----------|
| CLAUDE.md | Human | Explicit instructions, conventions, rejections | Everything |
| Humanfile | OS (editable by human) | Compiled behavior patterns, convergence profile | Agentfile defaults |
| Agentfile | Human or OS | Agent constraints, tool whitelist, limits | Agent behavior |

Priority: **CLAUDE.md > Humanfile > Agentfile defaults**.

The human's explicit word always wins. The OS's compiled observations fill gaps the human didn't write down. The Agentfile constrains what's possible.

### Boot Integration

```
Stage 0: Agentfile        (what the agent CAN do)
Stage 0b: Humanfile       (HOW the agent does it)
Stage 1: ostk init    (filesystem state)
Stage 2: boot.md          (session orientation)
Stage 3: compile          (intent -> needles)
Stage 4: execute          (work)
```

The Humanfile loads at Stage 0b — after constraints, before orientation. The agent enters boot.md already calibrated to the human. Convergence starts at turn 1, not turn 8.

### Acceptance Criteria

- [ ] `ostk learn human` produces `humanfile.toml` from `audit.jsonl`
- [ ] `--seed CLAUDE.md` bootstraps from hand-written preferences
- [ ] `--dry-run` shows diff without writing
- [ ] Frequency threshold prevents single-session overfitting
- [ ] No file paths, secrets, or content in output — patterns only
- [ ] `humanfile.toml` is valid TOML, human-readable
- [ ] Boot sequence reads Humanfile and adjusts agent behavior pre-boot.md
- [ ] Deleting `humanfile.toml` degrades gracefully to CLAUDE.md-only boot
- [ ] `ostk show human` renders current Humanfile with explanations
- [ ] CLAUDE.md overrides Humanfile when they conflict
