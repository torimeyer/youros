# ostk + taynik — the invisible stack

status: draft
thread: invisible-infrastructure, auth-domain, distributed-operating-system
needles: →568, →574, →575
date: 2026-03-09

## Two products, one principle

| Layer | Product | Domain | What it does |
|-------|---------|--------|-------------|
| OS | ostk | ostk.ai | Coordination, execution, filesystem |
| Auth | taynik | taynik.net | Identity, trust, encryption |

Both invisible. Both derive everything from existing state.
No new credentials. No new accounts. No new surfaces.

## The principle

Law 5: invisible infrastructure, always.

This extends beyond the OS. The auth layer is invisible too.
The agent never authenticates. The key is derived from what's
already there. The session is secure before the agent knows
it exists.

## How taynik works (the wormhole pattern)

Wormhole (taynik's first product):
```
GPS coordinates → geohash → BLAKE3 KDF → XChaCha20 → encrypted content
Physical presence → key derivation → decryption
No server trust. The math is the lock.
```

Applied to agent auth:
```
HUMANFILE + GPG fingerprint + repo path → BLAKE3 KDF → session key
Agent presence (boot) → key derivation → authenticated session
No server trust. The identity is the lock.
```

Same pattern. Different coordinates:
- Wormhole: physical location (GPS)
- ostk: agent context (HUMANFILE + repo + GPG)

## The auth flow

### Local (single machine)
```
ostk boot
  → reads .ostk/HUMANFILE
  → reads GPG fingerprint (from git config or HUMANFILE)
  → derives session key: BLAKE3(fingerprint || repo_path || boot_timestamp)
  → agent is authenticated
  → no prompt, no token, no config
```

### Remote (cross-machine hand-off)
```
Machine A: ostk shutdown --handoff
  → serializes session state (registers, in-progress work, context snapshot)
  → encrypts with taynik: BLAKE3(shared_HUMANFILE_fingerprint) → XChaCha20
  → writes encrypted bundle to transfer location (file, URL, nudge)

Machine B: ostk boot --receive
  → reads encrypted bundle
  → derives same key from own HUMANFILE (same human = same GPG = same key)
  → decrypts session state
  → boots with full context from Machine A
  → authenticated hand-off. Zero server.
```

### Cross-OS (different repos, same machine)
```
OS-A: ostk nudge @os-b --sign "needle filed"
  → resolves @os-b via ~/.ostk/registry.jsonl
  → signs nudge with GPG key
  → encrypts payload: BLAKE3(sender_fingerprint || receiver_path)
  → writes to os-b's nudge queue

OS-B: ostk boot
  → reads nudge queue
  → verifies GPG signature
  → decrypts with derived key (same fingerprint in registry)
  → surfaces: [nudge] from os-a: "needle filed" (verified ✓)
```

## The domain boundary

ostk never handles keys. taynik never handles coordination.

| Responsibility | ostk | taynik |
|---------------|------|--------|
| File editing | ✓ | |
| Conflict resolution | ✓ | |
| Process supervision | ✓ | |
| Output compression | ✓ | |
| Key derivation | | ✓ |
| Encryption/decryption | | ✓ |
| Signature verification | | ✓ |
| Identity attestation | | ✓ |
| Audit trail | ✓ (events) | ✓ (signatures) |

## Invisible auth vs visible auth

| | Visible (OAuth, API keys) | Invisible (taynik) |
|---|---|---|
| Setup | Create account, get token, set env var | Nothing. GPG key already exists. |
| Per-request | Attach bearer token | Nothing. Session key derived at boot. |
| Rotation | Manage key lifecycle, refresh tokens | Nothing. Key derived from immutable identity. |
| Cross-machine | Exchange credentials | Same HUMANFILE = same key. |
| Server dependency | Auth server must be reachable | No server. Math is local. |
| Agent awareness | Agent must know about auth | Agent doesn't know it's authenticated. |

## Compounds

- **Invisible infrastructure** — auth is invisible, extending law 5 to the full stack
- **Hand-off (→574)** — encrypted session transfer between agents/machines
- **GPG trust (→568)** — GPG signing as identity, now also as encryption input
- **OS registration (→544)** — registry resolves names, taynik authenticates the connection
- **Adoption** — zero auth setup = zero adoption friction
- **Wormhole** — same crypto pattern, different coordinate space

## The tagline

**ostk + taynik: infrastructure you don't see, auth you don't configure.**
