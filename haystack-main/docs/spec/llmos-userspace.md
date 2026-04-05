---
title: llmOS Userspace
status: spec
version: 1
author: promote-userspace (@haystack.prime+1088)
created: 2026-03-12
compounds:
  - docs/draft/llmOS-sh.md
  - docs/draft/eos-userspace.md
prior_art:
  - docs/spec/smp-architecture.md
  - docs/spec/bootloader.md
  - docs/spec/agent-lifecycle.md
  - docs/spec/identity-layers.md
implements: []
---

# llmOS Userspace

> The transformer cannot hold a secret for the same reason Ring 3 cannot read Ring 0: the mechanism does not exist. The login shell is where the human becomes the operator.

This spec unifies two converging insights: the **privilege ring model** and the **login shell**. The bridge syscall (:request/:grant) is the mechanism that makes them talk.

---
## Part I -- The Privilege Rings

### The Boundary Is Not Policy. It is Physics.

Traditional OS: hardware rings prevent user code from touching kernel memory.
llmOS: cognitive architecture prevents the LLM from performing userspace operations.

The ring is implemented in attention heads instead of silicon gates. All tokens are visible. No token is opaque.

### Three Properties Define Each Ring

**Userspace (Human CPU)** requires:
- **Opacity** -- can hold information invisibly. The LLM context window is fully readable; nothing is hidden.
- **Persistence** -- identity survives across sessions. The LLM dies and restarts with amnesia.
- **Personhood** -- accountability, desire, legal standing, wallet. The LLM has none of these.

**Kernelspace (LLM CPU)** requires:
- **Parallelism** -- fork cheaply, run N agents concurrently. Human attention is serial.
- **Endurance** -- no fatigue, no sleep, no context switches. Human degrades after hours.
- **Scale** -- hold 10k files in working memory, run 600 tests. Human cannot.

### The Ring Map

| Userspace (Human) | Why userspace | Kernelspace (LLM) | Why kernelspace |
|---|---|---|---|
| Secrets | No opaque memory in LLM | Parallel execution | Human attention is serial |
| Trust decisions | Irreversibility needs accountability | Code search at scale | Human eyes do not scale |
| Identity | LLM has no cross-session continuity | Pattern matching | Human tires after file 50 |
| Corrections | Ground truth needs lived experience | Output compression | LLM systematically elides |
| Intent | Direction needs desire | Continuous monitoring | Human sleeps |
| Payment | LLM has no wallet | Mechanical verification | Human runs one test |
| Legal standing | LLM has no personhood | Multi-file reasoning | Human context window is narrow |

**Shared space:** Reading code, writing code, filing needles, architecture reasoning. Same capability, different throughput.

### The API Key as Ring 0

The ANTHROPIC_API_KEY incident is the canonical demonstration:

```
1. Runner needs the key to call the model API
2. LLM cannot hold the key securely (context window = readable memory)
3. Human stores the key in Bitwarden (opaque to the LLM)
4. Vault bridges: bw get -> env var -> subprocess -> key dies with process
5. The LLM never sees the key value
```

This is a syscall. The human (userspace) owns the credential. The vault (syscall interface) mediates. The kernel (runner process) uses the credential without exposing it to LLM context.

---

## Part II -- The Login Shell

### The Problem

ostk has a kernel. It has a bootloader (boot.md). It has a process table, file CAS, compression, reap. What it does not have is a **login shell** -- the sequence that sits between power-on and productive work, and that correctly exercises the ring boundary on first contact.

Today the boot sequence is manual: human types ostk boot, reads output, types intent. Three steps, all human-initiated. The LLM does not know it is booting. No pre-login hygiene. No post-login calibration. The human IS the shell.

### The Unix Mapping

```
llmOS.sh
  PRE-LOGIN
    ostk reap              (GC dead agents -- PAM = identity hygiene)
    ostk boot              (kernel state check -- fsck)
    env setup                  (OSTK_ROOT, PATH, OSTK_AGENT)

  LOGIN
    read Humanfile             (who is this human?  -- .profile)
    read Agentfile             (what can this agent do? -- capabilities)
    read boot.md               (session state -- motd)

  POST-LOGIN
    convergence check          (how many corrections last session?)
    compile ready              (hay -> needles if stale)
    nudge queue                (pending nudges for this agent)

  INTERACTIVE LOOP
    prompt (tack input)        (human types compressed intent)
    parse (tack grammar)       (LLM decompresses)
    execute (kernel ops)       (ostk commands)
    display (compressed out)   (squasher output)
```

### Attention and Distraction

**++attention** -- The login shell FOCUSES the LLM. boot.md is the world. Humanfile is the posture. Dispatch queue surfaces open P1s. Nudge queue surfaces what the OS thinks matters.

**--distraction** -- The login shell REMOVES noise. Reap runs before boot.md is read (500 ghosts never enter context). Read elision skips files the agent already has. Squasher strips VTE codes (60% token reduction). Humanfile calibration prevents option-generation.

### The Boot Lifecycle (Complete)

```
SHUTDOWN (previous session)
  registers-dump.md written
  boot.md regenerated
  commit + push

         --- session boundary ---

POWER ON (new session)
  llmOS.sh / ostk login / ostk serve
    PRE-LOGIN
      reap (GC dead agents)
      verify (boot.md current?)
    LOGIN
      Humanfile -> calibrate posture       (<- :request identity)
      Agentfile -> constrain capabilities  (<- :request capability)
      boot.md -> orient to state
    POST-LOGIN
      convergence check
      compile if stale
      load nudge queue
    READY
      first prompt is productive

         --- interactive loop ---

SHUTDOWN (this session)
  ...
```

### Three Implementation Forms

**Form 1: Shell script (prototype)** -- reap, boot verify, exec claude. Prototype for transparency.

**Form 2: Kernel subcommand (product)** -- `ostk login` runs the same sequence inside the Rust binary.

**Form 3: Implicit (the real target)** -- The login sequence runs automatically when `ostk serve` starts. The MCP server first response includes boot context. No script. No subcommand. The login IS the connection.

---

## Part III -- The Bridge Syscall

### :request / :grant

The ring model explains what belongs in each ring. The login shell is the first time the rings must communicate. :request/:grant is the interface.

```
:request <domain> [reason]
:grant   <domain> [resolution]
```

This is the tack verb pair for ring-boundary operations. When the LLM (kernelspace) encounters something it structurally cannot do -- hold a secret, make an irreversible trust decision, authorize payment -- it surfaces a :request. The human (userspace) responds with :grant and the kernel mediates the result without exposing the payload to LLM context.

### Request Domains

| Domain | Example | How grant flows |
|--------|---------|-----------------|
| credential | API key, token, password | Vault -> env var -> subprocess; LLM never sees value |
| trust | Deploy to prod? | Human confirms; kernel executes; audit record written |
| identity | Which user is this? | Humanfile read; kernel injects calibration context |
| payment | API quota extension | Human approves; kernel updates quota record |
| correction | Human issues :correct | Ground truth injected; LLM calibrates |

### The Canonical Flow

```
LLM encounters operation beyond its ring
  |
  v
:request credential anthropic-api-key
  |
  v
Kernel surfaces to human (TUI notification or nudge)
  |
  v
Human: :grant credential
  |
  v
Kernel: vault-mediated injection -> env var -> subprocess
  |
  v
Credential used; never enters LLM context; audit record written
```


### :correct Is a Grant

The :correct verb is a specific form of :grant truth. When the human issues :correct, they are exercising ring-0 authority. :correct stops everything. :confirm proceeds. Both are grants.

### Connection to the Login Shell

The login shell is the first exercise of :request/:grant in a session. Reading Humanfile is implicit :request identity. Reading Agentfile is implicit :request capability. Nudge queue surfaces :request attention.

Login grants are implicit (kernel reads Humanfile/Agentfile directly). Runtime grants are explicit (human types :grant). Same protocol, different invocation.

---

## The Unified Picture

```
+-------------------------------------------------------------------+
|  USERSPACE (Human CPU)                                            |
|  Opacity . Persistence . Personhood                               |
|                                                                   |
|  Humanfile   Agentfile   vault   corrections   trust              |
|                           ring boundary                           |
|                            :request / :grant                      |
|                                                                   |
|  KERNELSPACE (LLM CPU)                                            |
|  Parallelism . Endurance . Scale                                  |
|                                                                   |
|  ostk kernel  tack grammar  agents  compression               |
|                                                                   |
|  +------------------------------------------------------------+  |
|  | LOGIN SHELL -- first ring exercise each session        |  |
|  | PRE-LOGIN: reap, verify                                |  |
|  | LOGIN: :request identity, :request capability          |  |
|  | POST-LOGIN: convergence, compile, nudge queue          |  |
|  | INTERACTIVE: tack -> parse -> execute -> output        |  |
|  +------------------------------------------------------------+  |
+-------------------------------------------------------------------+
```

Three components, one system:
1. **Rings** define what belongs where and why.
2. **Login shell** is the sequence that initializes the session across the boundary.
3. **:request/:grant** is the formal verb at the boundary the session uses throughout.


---

## Acceptance Criteria

### Privilege Rings
- [ ] Ring map documented (userspace/kernelspace with rationale per operation)
- [ ] API key incident documented as canonical :request credential example
- [ ] :correct defined as :grant truth (ring-0 human authority)

### Login Shell
- [ ] ostk login subcommand: reap -> verify -> Humanfile -> Agentfile -> boot.md -> convergence -> nudge
- [ ] Form 3 (implicit): ostk serve sends boot context in first MCP response
- [ ] Multi-agent: each agent gets own Agentfile scope; boot.md and Humanfile are shared
- [ ] Graceful degradation: no Humanfile -> CLAUDE.md only -> still boots
- [ ] First prompt after login is productive (no orientation dance)

### Bridge Syscall
- [ ] :request <domain> [reason] tack verb resolves to TUI notification + kernel hold
- [ ] :grant <domain> tack verb resolves kernel hold, routes payload appropriately
- [ ] Credential domain: vault-mediated -- payload never enters LLM context
- [ ] Trust domain: human confirmation -> kernel executes -> audit record written
- [ ] All :request/:grant events written to audit.jsonl with domain + outcome
- [ ] Login-time grants are implicit (Humanfile/Agentfile) -- no explicit :grant required at boot