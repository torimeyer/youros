---
promoted_at: 2026-04-02T00:48:47Z
status: spec
---
# Approval Policy Chain — Draft v0.3

## Design Philosophy

The approval chain is **one layer** in a composable security model. It doesn't
solve everything alone — it composes with existing mechanisms:

| Layer | Handles | Example |
|-------|---------|---------|
| **pin.caps** | Path-level deny/allow | Agents can't write to `src/` or create Agentfiles |
| **Agentfile signing** | Provenance + integrity | Unsigned Agentfiles can't grant privileged classes |
| **HUMANFILE** | Identity + governance + trust | Who can sign, who can promote, operator trust overrides |
| **Approval chain** | Per-call policy evaluation | Should this tool call execute right now? |

The chain assumes pin.caps and signing are enforced externally. Threats like
"poisoned Agentfile" are handled by composition (pin.caps denying agent writes
to `agents/`, plus signature verification), not by the chain re-checking integrity.

The goal is **initial userbase satisfaction**: prevent glaring holes, provide
the scaffolding so that when users need tighter controls, they're already there.

## Status Quo

Approval is currently a **flat gate** at `agent_loop.rs:910` that checks:

1. Is `permission_mode == Governed`? If not, skip everything.
2. Is the tool in `KERNEL_READ_TOOLS` or starts with `ostk_`? Auto-approve.
3. Is the tool in `runtime_allowed`? Auto-approve (except destructive Bash).
4. Otherwise, prompt the user (Y/N/A).

### What's disconnected

| System | Purpose | Connected to approval? |
|--------|---------|----------------------|
| `pin.caps deny:` | Restrict write paths | **No** — errors at execution, not at approval |
| `HUMANFILE` | Human identity + governance | **No** — governs ceremonies, not tool calls |
| Agentfile `TOOL shell` | Tool visibility in API call | **No** — only controls what model can see |
| Agentfile `LIMIT permissions` | Sets PermissionMode | **Yes** — gates the entire approval block |
| `detect_destructive` | Flag dangerous Bash commands | **Yes** — overrides AlwaysAllow for Bash |

### Scope problems

| Issue | Current behavior |
|-------|-----------------|
| AlwaysAllow scope | Tool name only (e.g. "Bash"), no pattern matching |
| AlwaysAllow persistence | Per-dispatch — resets on next user message |
| No session persistence | Can't say "always allow Bash for this session" |
| No deny-list integration | pin.caps denials don't auto-deny in approval modal |
| Auto mode stub | PermissionMode::Auto documented as "file edits auto-approved, Bash gated" but treated identically to Governed |
| No pattern matching | Can't say "allow shell gpg*" or "allow Read for src/" |
| Shell is all-or-nothing | "Always Allow shell" grants cat and rm -rf equally |

## Tool Capability Classes

Tools are decomposed into capability classes. Each class represents a
distinct privilege level. The approval chain evaluates against the class,
not the raw tool name.

### File tools (existing)

| Class | Operations | Privilege |
|-------|-----------|-----------|
| `file:read` | Read, Glob, Grep | Read-only, no side effects |
| `file:edit` | Edit (in-place replacement) | Mutates existing files |
| `file:write` | Write (create/overwrite) | Creates or replaces files |

### Shell tools (new decomposition)

| Class | Examples | Privilege |
|-------|---------|-----------|
| `shell:read` | `cat`, `ls`, `ps`, `grep`, `find`, `git log`, `git diff`, `head`, `wc`, `env` | Read-only — no filesystem mutation, no process creation |
| `shell:write` | `echo > file`, `cp`, `mv`, `mkdir`, `rm`, `chmod`, `git commit`, `git push` | Filesystem mutation — creates, modifies, or deletes files |
| `shell:exec` | `spawn`, background processes, `curl`, `wget`, `npm install`, `cargo build` | Process creation — spawns new processes, network access |
| `shell:secret` | `ostk secret`, `gpg`, `ssh-keygen`, key material operations | Secret/key operations — touches credentials or key material |

### Classification rules

Classification is done by `detect_capability_class()` in the approval gate,
before the chain evaluates. The chain never sees raw "shell", only a class.
This builds on the existing `detect_destructive` classifier.

1. Commands are classified by their **primary effect**, not their name
2. A command that both reads and writes is classified at the **highest** privilege
3. Pipe chains are classified by the **highest-privilege segment**
4. Unknown commands default to `shell:exec` (conservative — over-promotes, never under-promotes)

#### Classifier layers (evaluated in order)

```
Layer 1: Operator scan (shell syntax)
  - Presence of >, >>, |, $(), `` → escalate to at least shell:write
  - This catches "cat foo > /etc/passwd" regardless of the command name

Layer 2: Known-dangerous flags
  - sed -i, perl -e, python -c, ruby -e → shell:exec (arbitrary code)
  - git checkout, git reset, git clean → shell:write (filesystem mutation)
  - curl, wget, nc → shell:exec (network access)
  - gpg, ssh-keygen, ostk secret → shell:secret

Layer 3: Command allowlist (known-read commands)
  - cat, ls, head, tail, wc, find, grep, ps, env, pwd, which, file,
    git log, git diff, git status, git branch → shell:read
  - Only matched when Layer 1 and Layer 2 didn't escalate

Layer 4: Default
  - Anything not matched → shell:exec
```

This is a **conservative classifier** — it will over-promote (classify reads
as writes) but never under-promote (classify writes as reads). False positives
cause an extra prompt; false negatives cause a security hole.

### Kernel tools

| Class | Operations | Privilege |
|-------|-----------|-----------|
| `kernel:read` | `ostk needle list`, `ostk ps`, `ostk clock`, `ostk history` | Read-only kernel state |
| `kernel:write` | `ostk needle add`, `ostk commit`, `ostk hay` | Mutates kernel state |
| `kernel:spawn` | `ostk run`, `ostk spawn` | Creates agent processes |
| `kernel:secret` | `ostk secret` | Key material operations |

`kernel:read` is auto-approved (no side effects).
`kernel:write` falls through to the mode gate — OCC protects integrity
(no concurrent overwrites) but not policy (an agent writing malicious
needles/hay to poison its own future context is a policy question).
`kernel:spawn` and `kernel:secret` are privileged operations governed
by the full chain.

## Proposed: Unified Policy Chain

Approval is a **policy chain** evaluated top-to-bottom. First match wins.
Every step returns one of: `ALLOW`, `DENY(reason)`, `PROMPT(reason)`, or `CONTINUE`.

```
1. HARD DENY (pin — fail-closed on parse error)
   -> pin.caps deny: tokens -> auto-deny, no modal
   -> Applies uniformly: file:write, file:edit, shell:write all check
      against pin.caps path rules — same paths, same denials
   -> e.g. pin.caps says "deny: write-src" -> any write to src/ auto-denied
      regardless of whether it's file:write or shell:write
   -> pin TRUST: signed -> OS provider requires signed Agentfiles.
      Sets a floor that HUMANFILE TRUST unsigned cannot override.
      Evaluated at step 6 — unsigned Agentfile patterns for privileged
      classes are silently ignored when pin enforces signing.
   -> If pin fails to parse: DENY("pin parse error — fail-closed")

2. DESTRUCTIVE CHECK (detect_destructive — never bypassed)
   -> Runs on shell:write, shell:exec, shell:secret, kernel:spawn
   -> Matches against existing destructive patterns (rm -rf, git push --force,
      DROP TABLE, etc.) — same classifier we already ship
   -> Returns PROMPT("destructive: {pattern}") — always asks, even if
      session allow-list or Agentfile would otherwise auto-approve
   -> Skipped for shell:read, file:read, kernel:read (no false positives on reads)

3. KERNEL TOOLS (trust boundary for ostk_* verbs)
   -> kernel:read -> ALLOW (no side effects)
   -> kernel:write -> CONTINUE (fall through to mode gate — policy, not just integrity)
   -> kernel:spawn, kernel:secret -> CONTINUE (privileged, full chain)

4. PERMISSION MODE GATE (sets the ceiling)
   -> Autonomous: ALLOW everything remaining
   -> Auto: ALLOW file:read, file:edit, file:write, shell:read
            (file:edit and file:write only within pin.caps-allowed paths —
             step 1 already denied anything outside, so this is safe)
            CONTINUE for shell:write, shell:exec, shell:secret, kernel:*
   -> Governed: CONTINUE (everything falls through to allow-list/prompt)
   -> Plan: DENY("plan mode — tools disabled")

5. SESSION ALLOW-LIST (persisted in AgentSession, revocable)
   -> runtime_allowed survives across turns within a session
   -> Keyed by capability class: "shell:read", "file:edit", etc.
   -> Can also key by class+pattern: "shell:read(git *)", "file:edit(src/)"
   -> User can revoke mid-session (removes entry from allow-list)
   -> Returns ALLOW if class (or class+pattern) matches

6. AGENTFILE PATTERNS (pre-authorized within mode ceiling)
   -> TOOL shell:read         -> approve all shell:read calls
   -> TOOL shell:write(src/)  -> approve shell:write targeting src/
   -> TOOL file:edit           -> approve all file edits
   -> Only consulted if mode gate returned CONTINUE (not in Autonomous)
   -> Privileged classes (shell:exec, shell:secret, kernel:spawn) require
      trust resolution before auto-approving:

      Trust hierarchy (evaluated top-to-bottom, first match wins):
        a. pin TRUST: signed  -> require signature, period. OS provider floor.
                                 HUMANFILE cannot relax this.
        b. HUMANFILE TRUST unsigned -> operator vouches for unsigned Agentfiles.
                                       Privileged patterns allowed without signing.
        c. Default (no directive) -> require signed Agentfile for privileged classes.

      Without trust clearance, privileged patterns are ignored and the call
      falls through to user prompt (step 7).
   -> Returns ALLOW if pattern matches

7. USER PROMPT (last resort)
   -> Show modal with: tool name, capability class, target, denial reason chain
   -> Y: allow this call
   -> N: deny this call
   -> A: allow this call + add to session allow-list (step 5)
        Prompt shows scope: "Always allow shell:read(git *) for this session?"
        Default scope = class + first-arg pattern (not bare class)
   -> Undo window: hold for 3s, Ctrl+Z to switch (P4)
```

### Chain invariants

- **Fail-closed**: if any step errors, the result is DENY, not skip
- **Reason chain**: every DENY and PROMPT carries a reason string for transparency
- **Isolated per agent**: each agent gets its own session allow-list — no cross-contamination
- **Classify first**: raw "shell" is decomposed into a capability class before the chain runs
- **Conservative classifier**: over-promotes (extra prompts) but never under-promotes (security holes)
- **Uniform path checks**: pin.caps applies to all write classes equally (file:write, shell:write, etc.)
- **Signed Agentfiles**: step 6 requires `ostk sign` for privileged classes, OR `TRUST unsigned` in HUMANFILE as operator override

### Composition model (what the chain does NOT handle)

| Threat | Handled by | Not by |
|--------|-----------|--------|
| Poisoned Agentfile | pin.caps (deny agent writes to agents/), Agentfile signing | Approval chain |
| Forged identity | HUMANFILE GPG chain | Approval chain |
| Agent self-modification | pin.caps (deny writes to .ostk/), kernel OCC | Approval chain |
| Cross-agent context poisoning | Session isolation (separate AgentSession per agent) | Approval chain |
| Unsigned code execution | pin TRUST (OS floor) + HUMANFILE TRUST (operator override) + Agentfile signing (step 6) | Approval chain alone |

## Acceptance Criteria

- [ ] P0: `runtime_allowed` persists across turns in `AgentSession`
- [ ] P0: `CapabilityClass` enum exists and all tool calls are classified before entering the chain
- [ ] P1: `detect_capability_class()` correctly classifies shell commands via 4-layer classifier
- [ ] P1: Auto mode auto-approves file:* + shell:read, gates shell:write/exec/secret
- [ ] P2: Destructive check runs at Step 2 — cannot be bypassed by allow-list or Agentfile
- [ ] P2: pin.caps deny applies uniformly across file:write, file:edit, shell:write
- [ ] P2: pin.caps parse error produces DENY, not skip
- [ ] P3: Agentfile TOOL directive accepts capability class syntax with optional pattern
- [ ] P3: Privileged classes require trust resolution: pin TRUST:signed (OS floor) > HUMANFILE TRUST unsigned (operator) > default (require signing)
- [ ] P4: User can revoke session allow-list entries mid-session
- [ ] P4: "A" prompt shows scoped pattern, not bare class
- [ ] P5: kernel:read auto-approved, kernel:write falls through to mode gate
- [ ] Every DENY and PROMPT carries a reason string
- [ ] Each agent has isolated session allow-list — no cross-contamination

## Implementation Priority

### P0: Fix AlwaysAllow persistence + capability class skeleton

Two changes shipped together:

1. Move `runtime_allowed` from local variable in `run_loop()` to `AgentSession` field
   so it persists across turns within a session.
2. Introduce `CapabilityClass` enum and `detect_capability_class()` that classifies
   tool calls. Initially maps everything to the existing flat behavior — just
   the type system, no behavioral change yet.

**Files**: `src/cpu/session.rs` (add field + enum), `src/cpu/agent_loop.rs` (classify + pass)

### P1: Shell classifier + Auto mode

Two changes that depend on P0's CapabilityClass:

1. Ship `detect_capability_class()` with real classification logic — the 4-layer
   classifier (operator scan, dangerous flags, command allowlist, default).
   Builds on existing `detect_destructive` infrastructure.
2. Auto mode uses capability classes: auto-approve file:*, shell:read within
   pin.caps-allowed paths. Gate shell:write, shell:exec, shell:secret.

**Files**: `src/cpu/classify.rs` (new — classifier), `src/cpu/agent_loop.rs` (Auto logic)

### P2: Chain reorder + pin.caps integration

- Promote destructive check to Step 2 (before allow-list and Agentfile patterns)
- pin.caps deny applies uniformly across all write classes at Step 1
- pin.caps parse errors -> fail-closed DENY
- kernel:write falls through to mode gate (not auto-approved)

**Files**: `src/cpu/agent_loop.rs` (reorder), `src/kernel/policy.rs` (pin.caps integration)

### P3: Agentfile TOOL patterns with capability classes

Add capability class syntax to TOOL directive:
- `TOOL shell:read` — approve all read-only shell
- `TOOL shell:write(src/)` — approve writes under src/
- `TOOL file:edit` — approve all edits

Parser extracts class + optional pattern. Approval gate checks against classified call.
Privileged classes (shell:exec, shell:secret, kernel:spawn) require trust resolution:

1. Check pin TRUST — if `signed`, require signature regardless of HUMANFILE (OS floor)
2. Check HUMANFILE TRUST — if `unsigned`, allow unsigned Agentfiles (operator override)
3. Default — require signed Agentfile

The HUMANFILE TRUST directive (already parsed by another session) lets the operator
override signature requirements for their own agents. The pin TRUST directive ships
in bail packages, letting OS providers enforce signing as a non-negotiable floor.

**Files**: `src/agentfile/parser.rs`, `src/cpu/agent_loop.rs`, `src/humanfile/parser.rs` (TRUST parsed), pin format (TRUST field)

### P4: Session deny-list + scoped "A" + undo window

- Revocation: user can remove entries from session allow-list mid-session
- "A" prompt defaults to scoped pattern: "Always allow shell:read(git *) for this session?"
  instead of bare "Always allow shell:read?"
- Undo window: hold denial for 3s, allow Ctrl+Z to switch to Allow

**Files**: `src/cpu/session.rs` (deny-list + scoped keys), `src/fcp_screen/app.rs` (undo + scope UI), `src/fcp_screen/action.rs`

### P5: Kernel tool governance

- kernel:write falls through to mode gate (shipped in P2)
- kernel:spawn and kernel:secret governed by full chain
- kernel:read remains auto-approved
- Document trust assumptions and composition model

**Files**: `src/cpu/agent_loop.rs` (kernel tool routing)
