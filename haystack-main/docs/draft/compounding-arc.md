# COMPOUNDING-ARC.md
> Scan date: 2026-03-10 | Agent: transcript-scan-agent | Sources: 6 transcript dirs, boot.md, RUNTIME.md, GAPS.md, NETWORK.md, compounding-dependency-order.md

---

## 1. What Was Built — Session Arc (fcp → haystack)

### Phase 0: Pre-haystack — mish + slipstream (pre-2026-03)
- mish: PTY-owning process kernel (forkpty, session table, shell supervision)
- slipstream: file coordination (CAS via str_replace, OCC)
- Conclusion: two repos, IPC boundary failures, needed absorption

### Phase 1: Haystack MVP Build — Sprints 1–2 (early March)
**The kernel arc** (discussions/haystack-mvp, 2026-03-07 transcripts):
- D1 (Runtime Architect): "Absorb mish + slipstream as library crates — one binary"
- D2 (Product Architect): "The demo is two named agents, one file, edits merge invisibly"
- D3 (Agent Experience): "Recovery digest — without it every compaction is a lobotomy"
- Convergence: gen table ships first. Identity + Hot PR are columns in same table. Three parallel tracks → one demo.

**What shipped in Sprints 1–2** (high-velocity, compounding intact):
- Wave 1: CLI commands (draft/promote/work next) — immediately used to track haystack itself
- Wave 2: Work queue, issue tracking — 17 organic needles filed from real friction (bd-001–bd-023)
- Wave 3: init, log, issue list — visibility into what was broken
- P0 fixes: flock, path normalization
- haystack commit — carried spec refs + bead IDs on every subsequent commit
- Makefile — every agent used same build/test pipeline
- decompose auto-locks — Tier A tracks got locks for free
- **MVP kernel (3 parallel tracks):**
  - Track 1: PTY + CAS + gen table (7 beads, commit 946be87)
  - Track 2: Hot PR T1+T3 + identity + heartbeat (3 beads, commit 9685efe)
  - Track 3: digest + recovery + 304 elision (8 beads)
- 304 elision cluster (bd-151–163): 13 beads, all closed, one commit (d26aed1)
- Nudge: kernel injects context into agent's next tool response (bd-100, commit 31789cb)
- Result: 152 tests, 25 commits

### Phase 2: Sprint 3–4 Collapse (retro documented in discussions/retro-sprints-3-4)
**What broke:**
- 3 specs promoted (pull-model, layer-boundary, spec-versioning) → 20 beads → 0 closed
- Autonomous window: orchestrator wrote documents instead of code
- Recovery + nudge built as libraries, never wired into dispatch.rs
- gen keyword collision (Rust 2024) — Track 2 repeated Track 1's mistake — no cross-track knowledge sharing
- Result: 14 tests, 9 commits (vs 152 + 25 in Sprints 1–2)
- Compounding score: 22% close rate (decompose exhaust) vs 44% (organic needles)

**What the retro produced** (retro-sprints-3-4/round2-synthesis.md):
1. Fix create_file overwrite bug (data-loss in critical path)
2. Run instrumented SWE-bench with full kernel
3. Wire recovery and nudge into dispatch (2-hour job, not design problem)
4. Add multi-process integration tests (two agents racing edits through flock)
5. Design multi-agent SWE-bench experiment

### Phase 3: Spec Consolidation (discussions/compounding-promotion, 2026-03-08)
- compounding-dependency-order.md promoted to docs/spec/ (v1)
- Tier DAG codified: 0 → 1 → A → B → C → D
- Three metrics for CI: fan-out count, critical-path depth, reuse count
- Rule: P0 urgency overrides compounding. Compounding is tiebreaker for P1/P2.

### Phase 4: Post-MVP Tier A Design (discussions/post-mvp-tier-a, 2026-03-08)
Three non-overlapping Tier A features designed:
- **Agentfile** (Dockerfile-style): FROM/PROMPT/TOOL/SKILL/LIMIT/WORK directives — reproducible agents
- **Hot PR Tier 2** (Assisted Merge): [conflict] response with diff + suggested merge — no new tools
- **Read elision** (304 Not Modified): per-agent HWM table, 5 tokens instead of 800 on re-read

### Phase 5: fcp → haystack Sessions (2026-03-07 transcripts)
**fcp-python v0.1.6 published** (fcp-publish.md):
- MCP server name standardized ("python-fcp" → "fcp-python")
- PyPI: 0.1.6 live, CI passing (26s/31s), marketplace.json updated

**fcp-haystack needle bench work** (needle-001, needle-002, explorer, code-quality, refine-os):
- Explored claude-code plugin architecture (13 plugins, 5 primitive types)
- Found: hookify is the unshipped compound needle — all plugins writing custom hooks instead of using it
- Found: hook ordering has no priority field — race condition between Stop hooks (hookify vs ralph-wiggum)
- code-quality: "unified declarative hook system" = most compounding change in claude-code
- needle-001: PR #1 open (hookify as canonical runtime, -207 lines immediately)
- needle-002: PR #2 open (hook priority schema: `priority: number` + `exclusionGroup`)
- refine-os: found missing Plugin→Kernel Agent Bridge, Boot Context not a live process table

### Phase 6: Governance + v1.0.x Release Series (2026-03-10)
**v1.0.0 (governance release):**
- ENTITYFILE round table (gemini + haiku.dual + claude-code consensus)
- .primefile dual GPG-signed (@scott 955AF54E + @haystack.prime 99B076C9)
- GOVERNANCE.md v1.0, MANIFESTO.md, KUP v1.0+v1.1, tack-grammar (37 verbs)

**Recovery event (Law 2 proved):**
- Haiku created governance, died at context boundary
- Opus recovered from ~/.haystack, merged append-only
- Agents ephemeral, kernel survives

**v1.0.1 → v1.0.2:**
- CI key issued (@haystack.prime.ci 6893C46C ed25519)
- GOVERNANCE.md v1.1 (Part 12: negotiate protocol, Rule 6: semver)
- PR #5 fcp-haystack merged (first negotiated, attested, append-only)
- ss/ss_session removed from MCP tool surface (Law 1 enforcement)
- Append-only violation audited (v1.0.1 tag replaced — lesson: tag AFTER all commits)

### Phase 7: Architecture Expansion — Night Session (2026-03-10)
**~/.haystack/.boot/ submodule created** (8 specs committed):
- INIT: 25-line tack boot protocol (replaces 159-line tori-boot prose)
- RUNTIME.md: full llmOS runtime reference (synthesized by Agent D)
- GAPS.md: 4 implementation gaps with Gemini round table precision
- NETWORK.md: store/ports/protocols — llmOS network layer
- RAM.md: autonomous memory management, ={command}(args) syntax
- identity-model.md, runtime-primitives.md, USERSPACE.md, TACK-GRAPH.md, TUI-PROTOCOL.md, FIRECRACKER.md

**New architecture produced:**
- login.gpg: — single-line boot authentication, OS selection by GPG key
- POST (Power-On Self Test): 7 checks before login prompt
- tack.t: test framework (compile must test before ship)
- Signed OS userspace: agents as process boundaries, any org publishes signed OS
- haystack^ prompt: 8-line OS status (boot confidence, RAM%, fleet, ports)
- ={command}(args): OS self-directs context loading under RAM pressure
- Autonomous RAM management: detect pressure → page → load relevant → swap delta
- Network layer: store (shared KV) + ports (service discovery) + protocols (.tack contracts)

**29 needles filed:** →579–608

---

## 2. Compounding Dependency Order — v1.1

### The DAG (what must ship before what)

```
[SHIPPED] Kernel foundation
  Gen table + CAS + Hot PR T1/T3 + identity + heartbeat + digest + 304 elision + nudge
  Output squashing + PTY + sh_run/spawn/interact + sh_lock
  CLI: draft/promote/decompose/trace/amend/commit/audit/needle
  Tack: 37 verbs + 3-tier resolution
  Governance: .primefile + GOVERNANCE.md v1.1 + KUP

  ↓
[TIER P0] Correctness + Boot Security (must ship before v1.1 is real)
  →576 CAS TOCTOU flock               BLOCKS: all concurrent work
  →601 POST command                   BLOCKS: login.gpg: (boot is not real without POST)
  →600 tack.t framework               BLOCKS: →601 (POST needs tack.t --post)
  →597 tack linter tier 0             BLOCKS: tack becoming load-bearing
  →590 signed boot.md as tack init    BLOCKS: trust chain completeness
  →596 boot confidence gradient       BLOCKS: login.gpg: UX

  ↓
[TIER P1a] Kernel Completeness (compounds every session after)
  →578 registry isolation             UNBLOCKS: parallel CI
  →608 autonomous RAM management      COMPOUNDS: all large sessions
  →579 shutdown compiles HUMANFILE    COMPOUNDS: tack .language quality
  →594 registry-import.jsonl loading  COMPOUNDS: fcp-* driver ecosystem

  ↓
[TIER P1b] Operator Experience (compounds human oversight)
  →571 haystack search                REPLACES: Grep/Glob (machine's first request)
  →572 haystack diff (session delta)  COMPOUNDS: boot/refine cycle
  →580 fcp-haystack tier enforcement  COMPOUNDS: fcp-* reliability
  →532 TUI text input                 COMPOUNDS: escape from harness

  ↓
[TIER P1c] Network Layer (compounds multi-agent capacity)
  →604 store (shared KV)              COMPOUNDS: agent coordination
  →605 ports (service discovery)      COMPOUNDS: agent-to-agent protocol
  →606 protocols (.tack contracts)    COMPOUNDS: typed agent interfaces
```

---

## 3. What's Already in the Kernel (Shipped — Use It)

| Primitive | Status | Source |
|-----------|--------|--------|
| Gen table (monotonic file versions) | SHIPPED | src/kernel/file.rs |
| CAS via str_replace | SHIPPED | src/kernel/file.rs |
| Hot PR T1 (auto-merge non-overlapping) | SHIPPED | src/kernel/file.rs |
| Hot PR T3 (conflict error) | SHIPPED | src/kernel/file.rs |
| Identity assignment (agent-N aliases) | SHIPPED | kernel |
| Heartbeat (gen table timestamp) | SHIPPED | kernel |
| Digest injection ([procs] + [files]) | SHIPPED | kernel |
| Read elision (304 Not Modified) | SHIPPED | kernel |
| Staleness detection ([stale]) | SHIPPED | kernel |
| Output squashing (VTE strip, dedup) | SHIPPED | kernel |
| PTY ownership (forkpty, no indirection) | SHIPPED | src/kernel/pty.rs |
| sh_run / sh_spawn / sh_interact | SHIPPED | src/serve/tools/ |
| sh_lock / sh_session / sh_help | SHIPPED | src/serve/tools/ |
| Nudge (IPI-style context injection) | SHIPPED | kernel |
| Tack (READ path — intent verification) | SHIPPED | MCP tool |
| Registry (@import os) | SHIPPED | src/kernel/registry.rs |
| CLI: 30+ commands | SHIPPED | src/commands/ |
| Governance: .primefile dual-signed | SHIPPED | .haystack/.primefile |
| CI: 4-target builds + .asc signatures | SHIPPED | .github/workflows/ |
| 566 tests | SHIPPED | tests/ |

**NOT shipped (inert or spec-only):**
- Hot PR T2 (assisted merge) — spec exists, not implemented
- Hot PR T4 (diagnostic-flagged) — spec only
- Bypass detection — PARTIAL
- POST sequence — spec in GAPS.md, not in binary
- tack.t framework — spec in GAPS.md, not in binary
- tack linter tier 0 — spec only
- login.gpg: authentication — spec, not in binary
- Autonomous RAM management — spec in RAM.md
- Network layer (store/ports/protocols) — spec in NETWORK.md
- haystack search / diff — not implemented

---

## 4. Tier A / B / C Build Order

### Tier A — Correctness Multipliers (build these first, they compound everything)

| Needle | What | Why First |
|--------|------|-----------|
| →576 | CAS TOCTOU flock | ONLY open P0. All concurrent work is unsafe without it. Every multi-agent test is measuring the wrong thing until this ships. |
| →600 | tack.t framework | POST needs it. Signed boot needs it. "Compile must test" is the law. Without tack.t, shipping new tack verbs is untested. |
| →601 | POST command | 7-check boot self-test. Until POST exists, login.gpg: is theater. Depends on →600 (tack.t --post). |
| →597 | tack linter tier 0 | Hallucination defense. Tack is becoming load-bearing (governs boot, recovery, agent dispatch). Needs syntax/existence validation before it gates real decisions. |

### Tier B — Kernel Completeness (build after T1 correctness, compound sessions)

| Needle | What | Why Now |
|--------|------|---------|
| →578 | Registry isolation | Unblocks parallel CI. Without it, CI contention is a design constraint, not a real limit. |
| →590 | Signed boot.md as tack init script | Completes the trust chain: boot.md is the swap file but it's not signed. GPG-sign boot.md = audit trail for intent, not just code. |
| →608 | Autonomous RAM management | Self-tuning context. Prevents OOM mid-session. Every large session is cheaper after this ships. |
| →579 | Shutdown compiles HUMANFILE | Tack .language quality compounds over sessions. Every correction written at shutdown improves the next session's tier-1 hit rate. |
| →571 | haystack search | Replaces Grep/Glob in agent workflow. Kernel-native search with gen-awareness. "The machine's first request" — already asked for, still pending. |

### Tier C — Operator + Multi-Agent (build after kernel is complete)

| Needle | What | Why Third |
|--------|------|-----------|
| →572 | haystack diff (session delta) | Boot/refine cycle gets faster. Agents see only what changed since last boot. |
| →532 | TUI text input | Compounds escape from harness. Agents interact with kernel directly without Claude Code wrapper. Deferred to v1.1 per original plan. |
| →604/605/606 | Network layer (store/ports/protocols) | Multi-agent coordination through filesystem. Compounds when there are multiple simultaneous agents — currently all crashed. Build when fleet is real. |
| →580 | fcp-haystack tier enforcement | Compounds fcp-* reliability. Ensures drivers don't bypass kernel coordination. Depends on fcp-* ecosystem being active. |
| →596 | Boot confidence gradient | UX for login.gpg: Restricted mode below 0.5. Depends on POST (→601) being implemented first. |

---

## 5. The 3 Highest-Leverage Needles — RIGHT NOW

### #1 — →576: CAS TOCTOU flock

**Compounding score: MAXIMUM**

The only open P0. str_replace IS the CAS — but without flock coverage over the read-modify-write cycle, two agents can race between the stat() and the write. Every multi-agent integration test, every fleet scenario, every SWE-bench measurement is measuring a racy system until this ships. Nothing else compounds correctly while this is open.

Fan-out: ~40 downstream needles depend on correct CAS behavior.
Close cost: estimated 2–4 hours (flock wrapping str_replace in src/kernel/file.rs).
Evidence: Sprints 3–4 collapse included at least one gen keyword collision caused by cross-track race. This is the documented root cause.

**Ship this first.**

### #2 — →600: tack.t test framework

**Compounding score: HIGH**

"Compile must test" — the law. tack.t is the test framework for the tack protocol itself. Without it:
- POST (→601) cannot run boot-critical checks
- Signed boot.md (→590) has no test path
- Every new tack verb shipped without regression protection
- Tack linter (→597) has nothing to validate against

The GAPS.md spec is precise: line-delimited format, `haystack bench --tack`, `.boot-critical` tags. This is a 1–2 day implementation against an already-complete spec. The runner must exist before any tack boot sequence is real.

Fan-out: POST, tack linter, signed boot, confidence gradient — all block on this.
Close cost: 1–2 days (spec in GAPS.md is complete, including format and runner integration).

**Ship second. POST follows immediately.**

### #3 — →571: haystack search

**Compounding score: HIGH (different axis)**

This is the machine's first compounding request — already asked for across multiple sessions (refine-os, MEMORY.md). It replaces Grep/Glob in agent workflow with a kernel-native, gen-aware search. Every session after this ships:
- Agents stop cold-reading whole directories
- Search results are gen-stamped (stale results flagged automatically)
- Token cost per exploration drops (read elision + search elision compound)

The sprint retro called it out explicitly (v1.1 plan item #9). The meta-analysis confirmed: agents using haystack tooling to track haystack is the compounding pattern that worked in Sprints 1–2. haystack search is that pattern applied to discovery.

Fan-out: every agent session. Every codebase exploration. Directly compounds token budget.
Close cost: 2–3 days (shell out to ripgrep + gen-stamp results + staleness annotation).

**Ship alongside tack.t — different subsystems, no dependencies between them.**

---

## Summary Table

| Priority | Needle | What | Compounding Multiplier |
|----------|--------|------|----------------------|
| P0 NOW | →576 | CAS TOCTOU flock | Correctness for ALL concurrent work |
| P0 NOW | →600 | tack.t framework | POST + tack verbs + boot confidence |
| P1 NEXT | →601 | POST command | login.gpg: becomes real (depends →600) |
| P1 NEXT | →571 | haystack search | Token budget + discovery (every session) |
| P1 NEXT | →578 | Registry isolation | Parallel CI (unblocks fleet parallelism) |
| P2 THEN | →597 | Tack linter | Hallucination defense for load-bearing tack |
| P2 THEN | →590 | Signed boot.md | Trust chain completeness |
| P2 THEN | →608 | Autonomous RAM | Self-tuning, large session survival |
| P2 THEN | →579 | Shutdown → HUMANFILE | .language quality compounds per session |
| P3 FLEET | →604–606 | Network layer | Multi-agent when fleet is real |
| P3 FLEET | →532 | TUI text input | Escape from harness |

---

*Generated by transcript-scan-agent | Sources scanned: 6 transcript dirs (14+ md files), boot.md, RUNTIME.md, GAPS.md, NETWORK.md, compounding-dependency-order.md, discussions synthesized*
*Lock: ~/projects/haystack/.haystack/locks/transcript-scan.lock — deleted after write*
