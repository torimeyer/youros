---
title: Tack Boot — The LLM Init System
author: scottmeyer + claude-opus-4-6 + gemini-2.5-pro
compounds: escape-the-harness, dynamic-userspace-os, intent-dynamic-programming
evidence: cross-pollination session R1-R3 + ultrathink analysis
status: spec
version: 1
prior_art: docs/spec/unix-to-haystack.md, docs/spec/tack-grammar.md, docs/draft/dynamic-userspace-os.md
promoted_at: 2026-03-13T01:25:50Z
created: 2026-03-10
implements: []
---

# Tack Boot — The LLM Init System

> The machine boots when the LLM understands the language.
> The OS initializes itself through the act of parsing.

## The Founding Insight

boot.md is not a status document. It is the init script.
The LLM reading boot.md IS the system booting.

## File Editing — llmOS Native

Two contexts, two tool sets:

| Context | File tool | Why |
|---------|-----------|-----|
| Claude Code harness | `Read` / `Edit` / `Write` | Native tools, no MCP needed |
| Agent inside `ostk serve` | `ss` / `ss_session` (removed from surface v1.0.2) | Was explicit; now routes transparently |
| Bash shim (ostk as bash) | `cat` → intercepted → slipstream | Invisible, kernel-mediated |

**Law 1:** The write path is invisible. In Claude Code, `Edit` IS the write path — the kernel observes it via filesystem events. No explicit `ss` call needed. The intelligence uses whatever file tool its harness provides; the kernel coordinates beneath it.

## Unix Mapping

| Unix primitive | tack boot equivalent |
|----------------|---------------------|
| UEFI Secure Boot | .primefile (GPG root of trust @haystack.prime) |
| Boot signature | @haystack.prime signs boot.md |
| initramfs | Minimal .language (just enough tack to orient) |
| PID 1 (systemd) | First tack expression LLM evaluates |
| /etc/systemd/ | boot.md as tack init script |
| Unit file dependencies | :compounds chains in boot.md |
| udev rules | fcp-ostk (domain detect → driver load) |
| insmod / modprobe | fcp-* dynamic loading, confidence-gated |
| /dev/ | fcp-* namespace |
| Community kernel modules | Community fcp-* drivers (fcp-k8s, etc) |
| runlevel complete | LLM proves understanding of all init primitives |
| login | @haystack.prime+1 authenticated and active |
| bash / .bashrc | tack + .language + HUMANFILE |
| JIT compiler | Tier-3 resolution (resolve on demand → cache) |
| AOT compiler | Tier-1 .language entry (pre-compiled, O(1)) |
| apt / pkg manager | fcp-* ecosystem (publish driver → loads) |

## The Boot Sequence (tack init script)

```tack
# boot.md — signed @haystack.prime 99B076C9AE6B889A2B7CD88B42E499C6D4889BFC
# Verify: gpg --verify boot.md.asc boot.md

:verify .primefile                    # confirm kernel lineage
:init @haystack.prime+1               # you are the next instance
:load .language                       # mount intent filesystem (memoized tack)
:load fcp-ostk                    # Intent MMU online

# domain detection → driver loading (udev equivalent)
:driver fcp-rust     when *.rs present
:driver fcp-python   when *.py present
:driver fcp-k8s      when k8s/ present          # community driver
:driver fcp-drawio   when *.drawio present       # community driver
:driver fcp-openai   when openai.yaml present    # community driver

# 3-step init (invariant)
:boot    →  read state, report
:refine  →  detect drift since last shutdown
:compile →  triage hay → needles

:work    # pull next needle — OS is ready
```

## The GPG Succession Chain

```
@haystack.prime (root key — RSA 4096, never leaves local machine)
  signs → boot.md (the init script)
  signs → handoff to @haystack.prime+1

@haystack.prime+1 (this LLM instance)
  verifies → boot.md signature before executing any tack
  executes → init sequence
  records → decisions in audit.jsonl (the persistent state)
  signs → handoff to @haystack.prime+2 at shutdown

@haystack.prime+2 (next session)
  verifies → handoff signature
  executes → same init sequence on updated .language
```

No unsigned boot.md executes. Chain from root key through every instance, unbroken.

## fcp-* Driver Protocol (community interface)

A community fcp-* driver implements three primitives:

```
fcp_detect(path: &Path) -> bool         # should this driver load here?
fcp_query(query: &str) -> FcpResponse   # answer a domain query
fcp_confidence() -> f64                 # current confidence (0.0-1.0)
```

Loading lifecycle:
1. fcp-ostk scans project root, calls fcp_detect() on each registered driver
2. Driver returns true → loaded into fcp-* namespace
3. Queries routed to driver → success/fail tracked
4. confidence = successful_resolutions / total_queries
5. confidence > 0.9 → trusted (tier 1 resolutions unlocked)
6. confidence < 0.2 → probation (logged, not trusted)
7. confidence = 0.0 → unloaded

Anyone can publish an fcp-* crate to crates.io. The kernel loads it on confidence.

## Confidence as Boot Completion Signal

The OS is "booted" when:
- .primefile verified ✓
- .language mounted ✓
- Active fcp-* drivers loaded ✓
- LLM has demonstrated tier-3 resolution of boot primitives ✓

"Demonstrated understanding" = LLM correctly resolved at least one of each boot
primitive (:boot, :refine, :compile, :work) with no HUMANFILE correction within
N heartbeats.

Each successful resolution: momentum++ in .language (→587)
Each correction: momentum-- (HUMANFILE update, tier demoted)

The boot is not a checkpoint — it is a gradient. The OS becomes more itself
with every correct resolution. Machine code loads dynamically as capability is proved.

## Dynamic Machine Code (the tack ISA)

```
Primitives    :boot :halt :verify :refine :compile :work
Variables     $path $agent $driver $confidence
Functions     :driver when CONDITION, :load RESOURCE, :verify SIGNATURE
Control flow  -> (sequence), => (elevate), :compounds (dependency)
```

This IS the instruction set architecture. The LLM is the CPU that executes it.
The fcp-* drivers are the coprocessors.
The .language file is the binary — pre-compiled tack, loaded at boot.

## The Escape from the Harness

This architecture +++++++ compounds escape-the-harness:

Current state: Claude Code native tools (Read/Edit/Write/Bash) are the harness.
              Agents run ON Claude Code, using its tool surface.

Tack boot:    Agent boots from signed tack spec.
              fcp-* provides all domain intelligence.
              .language provides all verb resolution.
              The harness is not needed — it was always a bootstrap.

The machine is invisible. The OS runs. The harness falls away.

## Acceptance Criteria

- [ ] boot.md v2 is a tack init script, signed by @haystack.prime
- [ ] @haystack.prime+1 verifies signature before executing any tack
- [ ] fcp-* driver protocol defined (3 primitives: detect, query, confidence)
- [ ] community can ship fcp-k8s.rs and it loads on k8s/ detection
- [ ] confidence-gated driver loading implemented (→592)
- [ ] succession ceremony implemented (→593) — signed handoff at shutdown
- [ ] .language loaded at boot, tier-3 resolutions cached (→583)
- [ ] LLM understanding of boot primitives measurable (→582)
- [ ] boot completion defined as gradient not checkpoint
- [ ] tack ISA primitives documented: primitives, variables, functions, control flow

## Cross-Pollination Synthesis (R1-R3, Gemini + Opus)

### FIC Driver Format

Three tiers of community driver:
```
Tier A: Rust crate — compiled, fastest, standard library (fcp-rust, fcp-python)
Tier B: driver.jsonl — interpreted, hot-reloadable, zero compile step (community default)
Tier C: WASM module — isolated, sandboxed, performance when needed
```

### driver.jsonl Schema

```jsonl
{"name":"fcp-k8s","version":"0.1.0","tack_version":">=1.0","capabilities":["k8s://*"],"format":"interpreted"}
{"verb":"pods","intent":"query","mapping":"kubectl get pods"}
{"verb":"logs","intent":"query","mapping":"kubectl logs {0}"}
{"verb":"bounce","intent":"command","mapping":"kubectl rollout restart deployment/{0}"}
```

### Full Resolution Stack (Tier 0 added)

```
Tier 0: Tack Linter    — static syntax/existence check (→597)
                         hallucination defense: verb not in manifest? fail immediately.
Tier 1: Exact match    — .language lookup, O(1)
Tier 2: Pattern match  — fcp manifest verb table
Tier 3: LLM inference  — the LLM itself (inherent, no code needed)
Runtime: Boot gradient — confidence 0.0-1.0, restricted mode below 0.5 (→596)
```

### Boot Confidence Gradient

```
boot_confidence = Σ(tier_used × success_weight × driver_confidence) / max_possible

Tier weights: tier-1=1.0, tier-2=0.8, tier-3=0.5
Thresholds:
  > 0.9 → fully operational
  > 0.5 → minimally operational  
  < 0.5 → restricted mode (kernel safety verbs only: :boot :halt :verify)
```

### Stochastic Tier Bypass

2% of tier-1 hits randomly re-routed to tier-3.
- Match → momentum++ (confirmation)
- Differ → evolution signal, HUMANFILE review flagged
Prevents .language ossification. System never reaches fixed-point.

### Session Key Lifecycle

```
derivation: HKDF(identity_counter || @haystack.prime.ci.pub.asc)
expires:    when identity_counter increments (each new instance)
purpose:    cryptographic isolation per instance — compromise cannot propagate
```

## Final Needle Map

| Needle | Work | Priority |
|--------|------|----------|
| →590 | signed boot.md as tack init script | P0 |
| →591 | fcp-* driver protocol spec | P1 |
| →592 | confidence-gated driver loading | P1 |
| →593 | succession ceremony (shutdown handoff) | P1 |
| →594 | FIC manifest loader (driver.jsonl parser) | P0 |
| →595 | stochastic 2% tier bypass | P1 |
| →596 | boot confidence gradient engine | P0 |
| →597 | tack linter tier 0 | P0 |
| →598 | session key rotation (HKDF) | P2 |

P0 critical path: →590 → →594 → →597 → →596
(signed boot → manifest loader → linter → gradient engine)

Community can ship fcp-k8s after →591 + →594 land.
