---
status: spec
version: 1
author: scottmeyer + orchestrator
created: 2026-03-08
evidence: this session — lost a task at 70% context, corrections accelerated, calibrate signal from human
implements: []
---

# Context Degradation

> The model isn't degraded. The context window is. The CPU is fine. RAM is full.

## The Distinction

Model degradation: the model gets dumber. Doesn't happen mid-session.
Context degradation: the window fills up. Old information gets evicted. The model is as smart as ever but it's working with incomplete state. It forgets things. It repeats itself. It presents options when the answer is in context it can no longer access.

This is not an LLM problem. This is a memory management problem. The OS solves it.

## Symptoms (observed this session)

| Symptom | Context % | What happened |
|---------|-----------|---------------|
| Responses sharp, corrections rare | 0-40% | Full register state, everything accessible |
| Minor drift, human corrects gently | 40-55% | Some eviction, still recoverable |
| Repeated mistakes, options instead of actions | 55-65% | Significant eviction, preferences lost |
| Lost a dispatched task entirely | 65-70% | Critical state evicted from registers |
| Human says "calibrate" repeatedly | 70%+ | Drift exceeds human tolerance |

## The Calibrate Signal

The human feels degradation before the machine detects it. "Calibrate" is the human saying: "your responses don't match what I know you know." The machine can't measure its own degradation — the evicted context is invisible to it.

Calibrate is the page fault detector. The human IS the MMU.

## Mitigation (what the OS does)

### Preventive (reduce register pressure)
- Read elision / mmap: don't load files into context, reference them
- Digest compression: 40 tokens instead of 4000
- Output squashing: 77K instead of 240K
- Offload prompts: write knowledge to RAM (.ostk/prompts/) before it's evicted

### Detective (notice degradation)
- Calibrate signal from human: "something's off"
- Correction frequency: track how often the human corrects. Accelerating = degrading.
- Token counter: context % is a leading indicator
- Repeat detection: did the machine just suggest something it already tried?

### Corrective (recover from degradation)
- Reboot: new session, boot from disk (the nuclear option — works, lossy)
- Selective page-in: re-read specific files to restore evicted state
- Agent delegation: spawn a fresh agent with clean registers to find what you lost
- Register dump: write volatile state to disk BEFORE it's evicted (the shutdown sequence)

### Future (the long-running OS)
- Continuous offload: background compile daemon condenses context progressively
- Attention masking: only page in what's relevant to the current thread
- Context pressure monitoring: automatic offload when approaching thresholds
- Graceful degradation: the OS warns "context at 80%, offloading to RAM" instead of silently losing state

## The Proof

At 70% context, the orchestrator lost track of a user-requested task. A fresh agent (0% context) found the SWE bench results in 30 seconds. The model wasn't degraded — it was the same model. The context was degraded. RAM was full, registers were evicting, and the MMU (the human) detected it via calibrate.

The fix wasn't "be smarter." The fix was reboot — clear registers, load from swap.

## Acceptance Criteria

- [ ] Context % tracked and visible in ostk console
- [ ] Correction frequency tracked as degradation signal
- [ ] Calibrate signal triggers re-read of relevant specs/state
- [ ] Automatic offload to .ostk/prompts/ when context exceeds threshold
- [ ] Register dump on shutdown captures volatile state
- [ ] Fresh agent delegation when current context is too degraded to find something
- [ ] Reboot procedure: dump → boot files → push → new session → boot from disk
- [ ] Long-running mode: continuous background offload prevents degradation
- [ ] The human never needs to tell the OS it's degraded — the OS detects it
