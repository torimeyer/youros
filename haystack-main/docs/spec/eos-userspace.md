---
status: spec
version: 1
author: scottmeyer + claude-code
promoted_at: 2026-03-13T03:35:15Z
created: 2026-03-09
evidence: API key incident — LLM cannot hold secrets. RTX2 on cognitive privilege rings.
implements: []
---

# eOs Userspace

> The transformer cannot hold a secret for the same reason Ring 3 cannot read Ring 0: the mechanism does not exist.

## The Privilege Boundary

Traditional OS: hardware rings prevent user code from touching kernel memory.
eOs: cognitive architecture prevents the LLM from performing userspace operations.

The boundary is not policy. It's physics.

## Three Properties Define Each Ring

**Userspace (Human CPU)** — requires:
- **Opacity** — can hide information (secrets, keys, credentials). The LLM's context window is fully readable.
- **Persistence** — identity survives across sessions. The LLM dies and restarts with amnesia.
- **Personhood** — accountability, desire, legal standing, wallet. The LLM has none.

**Kernelspace (LLM CPU)** — requires:
- **Parallelism** — fork cheaply, run N agents. Human attention is serial.
- **Endurance** — no fatigue, no sleep, no context switches. Human degrades after hours.
- **Scale** — hold 10k files in working memory, run 600 tests. Human can't.

## The Map

| Userspace (Human) | Why | Kernelspace (LLM) | Why |
|---|---|---|---|
| Secrets | No opaque memory in LLM | Parallel execution | Human attention is serial |
| Trust decisions | Irreversibility needs accountability | Code search at scale | Human eyes don't scale |
| Identity | LLM has no cross-session continuity | Pattern matching | Human tires after file 50 |
| Corrections | Ground truth needs lived experience | Output compression | LLM systematically elides |
| Intent | Direction needs desire | Continuous monitoring | Human sleeps |
| Payment | LLM has no wallet | Mechanical verification | Human runs one test |
| Legal | LLM has no personhood | | |

**Shared space:** Reading code, writing code, filing needles, architecture reasoning. Same capability, different throughput.

## The API Key as Ring 0

The `ANTHROPIC_API_KEY` incident:
1. The runner needs the key to call the model API
2. The LLM cannot securely hold the key (context window = readable memory)
3. The human stores the key in Bitwarden (opaque to the LLM)
4. The vault bridges: `bw get` → env var → subprocess → key dies with process
5. The LLM never sees the key value

This is a syscall. The human (userspace) requests a privileged operation (API authentication). The vault (syscall interface) mediates. The kernel (runner process) uses the credential without exposing it to the LLM's context.

The "hardware ring" is the architecture of transformers: all tokens are visible. No token is opaque. The ring is physical — just implemented in attention heads instead of silicon gates.

- [ ] Privilege ring model compounded into docs/spec/llmos-userspace.md Part I
