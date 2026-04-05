---
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: this session — 7% context consumed by offload prompts, white papers, needles over 2 sprints
implements: []
---

# llmOS Memory Model

> Context is registers. Offloaded context is RAM. The filesystem is disk. The mapping is structural.

## The Memory Hierarchy

```
LAYER           UNIX            OSTK                   SPEED    SIZE      VOLATILE
─────────────────────────────────────────────────────────────────────────────────────
Registers       CPU regs        Active context window       1 tok    200k/1M   yes
L1 cache        L1              Recent tool results         1 tok    ~10k      yes (compacts)
L2 cache        L2              Conversation history        1 tok    ~50k      yes (compacts)
Swap            Swap            OS output before human       1 tok    varies    YES
                                  confirms. .ostk/swap/
                                  May be wrong. Human is MMU.
RAM             RAM             Confirmed offloaded context  100ms    unlimited no
                                  .ostk/prompts/
                                  .ostk/sessions/
                                  recovery digests
Disk            Disk            Filesystem (specs, audit,    100ms    unlimited no
                                  source code, specs
                                  audit.jsonl, gen_table
                                  needles, discussions
Swap            Swap            Recovery digest             200ms    ~2k tok   no
                                  compressed session →
                                  paged back on restart
```

## Registers: The Active Context Window

What the LLM sees RIGHT NOW. The conversation. The tool results. The system prompt. Fixed size (200k or 1M tokens depending on model). Volatile — agent dies, registers are gone.

Every token in registers is expensive. The kernel's job: keep registers clean.

- Read elision (mmap): don't load files into registers, reference them on disk → 5 tokens instead of 800
- Digest compression: 40-80 tokens of ambient awareness instead of full process table
- Output squashing: 77k compressed instead of 240k raw
- Nudge injection: 10 tokens of context instead of a full re-read

## RAM: Offloaded Context

Context that WAS in registers, got offloaded to `.ostk/`, can be paged back.

### What lives in RAM

| RAM location | Content | Paged in when |
|-------------|---------|---------------|
| `.ostk/prompts/` | Domain authority docs (offload prompts) | Agent starts with PROMPT file:// |
| `.ostk/sessions/<alias>.jsonl` | Tool call history | Agent reconnects (recovery) |
| `.ostk/discussions/` | Round table transcripts | Agent needs design context |
| `.ostk/audit.jsonl` | Event stream | Agent traces attribution |
| `docs/spec/` | Promoted specs | Agent needs requirements |
| `docs/draft/` | Unfinished thinking | Agent continues prior work |

### RAM is NOT disk

Disk is the source code, the test files, the Cargo.toml — the ARTIFACTS. RAM is the CONTEXT about the artifacts. The offload prompts don't contain code. They contain the decisions, patterns, and knowledge that make working on the code efficient.

### RAM allocation

```
ostk run agent.af
  1. Load PROMPT file:// into registers          (page in from RAM)
  2. Load WORK needle context                    (page in from RAM)
  3. Agent works (registers fill up)
  4. Agent compacts (registers evicted)
  5. Recovery digest written to RAM              (page out)
  6. Agent restarts → digest paged back in       (swap)
```

## Page Faults

When an agent needs context it doesn't have in registers:

| Fault | Unix equivalent | ostk | Cost |
|-------|----------------|----------|------|
| Agent reads file not in context | Page fault | Full read from disk | ~800 tokens |
| Agent re-reads unchanged file | TLB hit | mmap/304 elision | ~5 tokens |
| Agent needs spec context | Demand paging | PROMPT file:// loads from RAM | ~500 tokens |
| Agent detects [stale] | Invalidation | Page in fresh from disk | ~800 tokens |
| Agent gets [nudge] | IPI (inter-processor interrupt) | 10 tokens injected | ~10 tokens |

## Page Table: The HWM

The high-water mark table IS the page table. It tracks what each agent (process) has in registers (context) vs what's on disk:

```
hwm.jsonl:
  agent-1: src/main.rs → gen 7 (in registers)
  agent-1: src/lib.rs  → gen 3 (in registers)
  agent-2: src/main.rs → gen 5 (stale — disk has gen 7)
```

When agent-2 references src/main.rs, the kernel checks the page table: hwm=5, disk=7 → page fault → return fresh content → update hwm to 7.

When agent-1 references src/main.rs: hwm=7, disk=7 → TLB hit → return [304] → no register space consumed.

## Swap: Compiled Session View

When an agent compacts (OOM), the kernel runs `ostk compile` on the session transcript — producing a materialized view:

```
Full history (disk):    .ostk/sessions/agent-1.jsonl (4000 tool calls, 800k tokens)
Compiled swap:          .ostk/sessions/agent-1.swap  (2k tokens)
  "12 edits to src/main.rs (gen 5→8). 3 test runs (2 pass, 1 fail on line 47).
   2 conflicts resolved (Tier 1 auto-merge). Working on → fix-validation-bug."
```

The same compiler that turns hay into needles turns session logs into swap. Full history stays on disk. Swap is the cached compilation — a materialized view regenerable from source.

### Compilation levels for swap

```
-O0: raw session log (full page-in, expensive, for forensics)
-O1: deduplicated (remove repeated reads, failed retries, stale outputs)
-O2: structural summary (actions not reasoning — the default swap)
-O3: intent-aware (what the agent was TRYING to do, not what it DID)
```

### Swap is a view, not a copy

The swap file is DERIVED from the session log. If you need the full history, it's on disk. If the swap looks wrong, recompile it: `ostk compile --swap agent-1 -O3`. The source of truth is always the raw log. Swap is an optimization.

## mmap: Read Elision

The agent references a file without loading it into registers:

```
Agent asks for src/main.rs
  Kernel: hwm says you have gen 7. Disk has gen 7.
  Kernel returns: [304] src/main.rs:gen=7 (current)
  Registers consumed: 5 tokens (not 800)
```

The file stays on disk. The agent has a reference to it. This IS mmap — memory-mapped file access without copying into process memory.

## OOM and the Context Budget

When registers fill up (context exhaustion):

1. **Compaction** — the model evicts old conversation turns (automatic, lossy)
2. **Recovery digest** — kernel writes swap file before compaction completes
3. **Restart** — new agent with same alias, swap paged back in
4. **Reduced allocation** — LIMIT context_pct 80 prevents OOM by stopping work early

The Agentfile's LIMIT context_pct IS ulimit. The kernel enforces it by checking context consumption and stopping the agent from pulling new needles above the threshold.

## Evidence: This Session

We observed the memory hierarchy live:

- **Register pressure**: context crept up 7% over 2 sprints from offload prompts, white papers, needles, discussions accumulating in the conversation
- **Offload to RAM**: 6 domain authority prompts written to `.ostk/prompts/` — context knowledge preserved outside registers for future agents
- **mmap in action**: agents reading the same specs repeatedly got the context without re-reading (the orchestrator held it in registers, agents got it via file reference)
- **Swap working**: when the orchestrator's context gets heavy, a new session loads the offload prompts (RAM) and MEMORY.md (swap) — not the full conversation
- **Page faults**: every time an agent needed a spec it hadn't read, it did a full read from disk (~800 tokens). With [304], the second read would be 5 tokens.

## The Kernel's Job

Minimize register pressure. Maximize disk utilization. Page intelligently.

Every kernel feature maps to memory management:
- Read elision → mmap (don't copy to registers)
- Digest → compressed page table summary (40 tokens, not 4000)
- Nudge → IPI (inject 10 tokens, don't force a full page-in)
- Recovery → swap (compressed write on OOM, page back on restart)
- Offload prompts → RAM (persistent context, paged in on demand)
- Hot PR → DMA (write directly to disk without register involvement for auto-merge)

## Acceptance Criteria

- [ ] Context consumption tracked per agent (register utilization %)
- [ ] Offload prompts load via PROMPT file:// (page in from RAM)
- [ ] Recovery digest written on compaction (swap out)
- [ ] Recovery digest loaded on restart (swap in)
- [ ] Read elision returns [304] for unchanged files (mmap)
- [ ] HWM tracks per-agent per-file state (page table)
- [ ] [stale] signals trigger re-read (page invalidation)
- [ ] LIMIT context_pct enforced (ulimit / OOM prevention)
- [ ] Hot PR auto-merge doesn't consume agent context (DMA)
- [ ] Context pressure visible in ostk console (free/used/swap)
