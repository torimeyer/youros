---
title: "Pin — Child OS Package"
implements: []
---

# Pin — Child OS Package

status: spec
date: 2026-03-10
authority: @haystack.prime 99B076C9

## What is a pin?

A **pin** is a minimal OS package that authenticates a child agent to a bounded
execution context under the ostk kernel. It contains:

1. A child GPG key issued by `@haystack.prime`
2. An 8-line tack boot script (`pin.boot`)
3. A capability declaration file (`pin.caps`)

Pins live at `.ostk/pins/{name}/`. The directory is immutable once GPG-signed.

A pin is not an agent. It is what an agent boots from. The agent is ephemeral;
the pin is the persistent identity contract it runs under.

## Why pins exist

The kernel root key (`@haystack.prime 99B076C9`) governs the OS. Agents cannot
self-assign identity — that is rejected (see CLAUDE.md: rejected decisions).
Instead, the kernel issues a pin: a delegation from `@haystack.prime` to a named
child context, with bounded capabilities scoped to that context.

This gives the operator fine-grained capability grants without modifying kernel
governance. Multiple agents can run under different pins simultaneously. The pin
is the boundary.

## File format

```
.ostk/pins/{name}/
  pin.boot       — 8-line tack init script (signed by child key)
  pin.caps       — capability declaration (plaintext, auditable)
  pin.boot.asc   — detached GPG signature (child key, endorsed by @haystack.prime)
```

### pin.boot

```tack
# pin: {name} — child of @haystack.prime
# sign: gpg --sign this file with child key
:verify parent-key
:init @{name}
:caps {caps}
:load .language
:boot → :work
:trust parent-key → :execution
```

Line-by-line semantics:

| Line | Verb | Meaning |
|------|------|---------|
| 1 | comment | Identity declaration — `{name}` matches directory name |
| 2 | comment | Signing instruction — operator must sign before activation |
| 3 | `:verify` | Assert parent key is present in `.primefile` before proceeding |
| 4 | `:init` | Bind kernel identity to `@{name}` for this session |
| 5 | `:caps` | Load capability constraints from `pin.caps` |
| 6 | `:load` | Load `.language` file (memoized dialect) |
| 7 | `:boot → :work` | Execute standard boot sequence, enter work state |
| 8 | `:trust` | Grant execution rights under parent-key delegation |

The 8-line limit is a design constraint, not a coincidence. A pin that cannot
express its boot in 8 lines is too complex for its role.

### pin.caps

```
read: .ostk/ .language
write: .ostk/store/{name}/
execute: shell(readonly)
deny: write-kernel modify-governance
```

Capability format: `{verb}: {space-separated targets}`

| Verb | Meaning |
|------|---------|
| `read` | Filesystem paths the agent may read |
| `write` | Filesystem paths the agent may write |
| `execute` | Commands and tools available, with optional qualifiers |
| `deny` | Hard denials — override any `read`/`write`/`execute` grant |

`deny` is always evaluated last. A capability present in both `execute` and
`deny` is denied.

## GPG chain

```
@scott 955AF54E           (human root — HUMANFILE authority)
  └── @haystack.prime 99B076C9   (kernel root — GOVERNANCE.md authority)
        └── {name} child key     (pin authority — scoped to pin.caps)
```

The kernel root key signs the child key. The child key signs `pin.boot`. This
creates a two-hop trust chain: human → kernel → pin.

Verification procedure:
1. Confirm `@haystack.prime 99B076C9` is in `.primefile`
2. Verify child key is signed by `99B076C9`
3. Verify `pin.boot.asc` against child key fingerprint
4. Load `pin.caps` — any capability not listed is implicitly denied

Unsigned pins do not execute. The kernel rejects `pin.boot` without a
corresponding `pin.boot.asc` at boot time (future: →590 signed boot.md).

## How boot.md uses pins

When a named agent boots under a pin, boot.md gains a `## Pin` section:

```markdown
## Pin

- name: {name}
- key: {child-key-fingerprint}
- parent: @haystack.prime 99B076C9
- caps: read .ostk/ .language | write .ostk/store/{name}/ | execute shell(readonly)
- signed: {date}
```

The agent reads this section at boot and self-constrains to the declared
capabilities. The kernel enforces at the `file:edit` and `shell` call sites
(future enforcement — current state: advisory).

## Issuing a pin

```
ostk pin issue {name} [--caps "read write execute"]
```

This command (implemented in `src/commands/issue_pin.rs`):

1. Creates `.ostk/pins/{name}/`
2. Writes `pin.boot` with the 8-line template
3. Writes `pin.caps` with default or provided capabilities
4. Prints the signing instruction

After the directory is created, the operator:

1. Generates a child GPG key: `gpg --quick-gen-key "@{name}" ed25519`
2. Signs it with the kernel key: `gpg --sign-key {child-fingerprint}`
3. Signs `pin.boot`: `gpg --detach-sign .ostk/pins/{name}/pin.boot`

The signed pin is then committed to `.ostk/` alongside the audit trail.

## What is NOT a pin

- A pin is not a HUMANFILE — the HUMANFILE governs human-kernel interaction
- A pin is not a AGENTFILE — the AGENTFILE governs agent self-description
- A pin is not an OS — the OS is the full boot context; a pin is a boot slice
- A pin does not grant kernel authority — only `@haystack.prime` holds that

## Design laws preserved

- **Law 1: Write path invisible.** Pins constrain, they do not instrument.
- **Law 2: Agents ephemeral.** Pins survive agent death; agents boot from pins.
- **Law 5: Invisible infrastructure.** A pin is just a directory. No new tools.

## Future work

- →590 signed boot.md: kernel verifies pin signature at boot, not just advisory
- PIN_REGISTRY: `.ostk/pins/registry.jsonl` for audit of all issued pins
- Pin revocation: `ostk pin revoke {name}` — appends revocation to registry
- Capability enforcement at `shell` call site (kernel-level, not advisory)

---

## Compounding: pin + bail + VM

A pin defines authority. A bail delivers it. A VM enforces it.

```
ostk bail pack --pin <name>
```

When `--pin` is specified:
- bail uses `pin.boot` instead of full `boot.md` (bounded boot protocol)
- bail includes `pin.caps` as capability manifest
- recipient boots into the pin's restricted scope
- audit records: pinned session, signer, pin name

This is the full delivery stack:
- pin: authority boundary (GPG-signed by parent)
- bail: OS package within that boundary
- FirecrackerVM: hardware enforcement of that boundary
- .ostk/: coordination layer (shared, append-only)

Authority chain: @scott → @haystack.prime → pin → bail → VM → audit
