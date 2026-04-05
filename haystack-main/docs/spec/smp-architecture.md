---
status: spec
version: 1
author: scottmeyer + orchestrator + round-table (os-architect, cognitive-scientist, product-architect, systems-engineer)
created: 2026-03-08
evidence: this session — human corrected "user=MMU" to "user=CPU, ostk=SMP/MMU"
implements: []
---

# ostk SMP Architecture

> ostk: SMP coordinator for human and machine processors, sharing a filesystem as memory.

## The Reframe

Previous model: the human is the MMU, the LLM is the CPU.

**Corrected model:**
- The human is a CPU (slow, high-precision — big core)
- The LLM is a CPU (fast, approximate — LITTLE core)
- ostk is the SMP coordinator + MMU
- The user is NOT the MMU — ostk is

Two processors. Same instruction set (intent → action). Different clock speeds. The OS coordinates.

## Why This Matters

The old model positioned the human as infrastructure — a translation layer the machine routes through. Humans don't translate. They process. They reason, prioritize, notice patterns the LLM misses, make judgment calls under ambiguity. The human's contribution is computation, not management.

Neither CPU controls the other. Both contribute work products to shared memory. The OS arbitrates.

## big.LITTLE

The human is the big core: slow clock, high precision, runs the hard problems (design decisions, ambiguity resolution, course correction). The LLM is the LITTLE core: fast clock, approximate, runs the bulk work (file edits, searches, compilation, parallel dispatch).

The scheduler (ostk compile + work next) should exploit this asymmetry. Don't send precision work to the fast core. Don't send bulk work to the slow core.

## Mapping

| SMP Concept | ostk Primitive |
|---|---|
| CPU 0 (slow, precise) | Human |
| CPU 1 (fast, approximate) | LLM agent |
| Shared memory | Filesystem (code, specs, needles) |
| Memory bus | Write path (ss / editor → filesystem → audit) |
| Cache line | File @ generation (path:gen) |
| Cache coherency protocol | Hot PR tiers (T1=silent, T2=assisted, T3=retry, T4=diagnostic) |
| CAS / atomic instruction | str_replace with match string |
| Cache line tag | gen counter |
| TLB | Needle index (intent → file + test) |
| TLB miss | compile (hay → needles) |
| IPI (inter-processor interrupt) | Nudge |
| Scheduler | compile + work next |
| Write-back flush | commit |
| Core architecture | big.LITTLE (asymmetric clocks, same ISA) |
| ISA (instruction set) | Intent → action (both CPUs speak it) |
| Spinlock | OCC retry loop (Hot PR Tier 3) |
| Memory-mapped I/O | fcp-* device drivers |
| /proc filesystem | Audit trail (audit.jsonl) |
| Context switch | Session boundary (offload → swap → recover) |

## Design Implications

1. **One interface, two processors.** ostk should never have "human mode" vs "agent mode." Same write path, same CAS, same coherency protocol.

2. **The console is a cache viewer.** `ostk show` displays shared state both CPUs operate on — what's dirty, what's flushed, what's coherent.

3. **Minimize bus transactions.** Every cross-CPU sync (human reviews LLM work, LLM reads human edit) costs tokens. Let each CPU run in local cache, flush on commit boundaries.

4. **Trust comes from coherency, not control.** The human doesn't need to manage the LLM. They need to trust that the OCC guarantees shared state is consistent.

5. **The scheduler exploits asymmetry.** Precision work → human. Bulk work → LLM. Compile decides.
