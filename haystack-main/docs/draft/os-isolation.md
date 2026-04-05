# OS Isolation — Cross-ostk Trust and Communication

status: draft
thread: distributed-operating-system
needles: →543, →544, →542, →553
date: 2026-03-09

## Problem

Any process on the same machine can write to any `.ostk/` directory.
No authentication, no authorization, no boundary. One OS instance writes
directly into another's state. Proven by incident: Claude session in
`~/.ostk/claude-code` filed needle →541 in `~/projects/ostk`
via `cd` + `ostk needle add`. The nudge it sent was never delivered.

## Principle

Nudge is the ONLY cross-OS primitive. It's a signal, not a syscall.
The receiving OS decides what to do with it. This is `kill(pid, sig)` —
you can send SIGTERM, but the process chooses how to handle it.

No direct writes across OS boundaries. Ever.

## Trust Model: GPG + Humanfile + Git

Each OS instance has a HUMANFILE (created at `ostk install`).
The HUMANFILE is the identity document — it codifies the operator.

### Trust chain

```
GPG key (operator's, already in git config)
  → signs HUMANFILE commit (git commit -S)
  → HUMANFILE fingerprint field links to git account
  → GitHub verifies GPG key (green checkmark)
  → Trust is cryptographic and auditable
```

### Trust levels

| Level | Meaning | Allowed |
|-------|---------|---------|
| self | Same .ostk/ | Everything |
| local-trusted | Same machine, signed HUMANFILE, fingerprint in registry | Nudge (read by boot) |
| local-unknown | Same machine, unsigned or unknown fingerprint | Nudge (quarantined, shown but flagged) |
| remote | Different machine | Future. Not v0.7.0. |

### Registry

```
~/.ostk/registry.jsonl
{"path":"/Users/scott/projects/ostk","name":"ostk","fingerprint":"ABCD1234...","last_boot":"2026-03-09T18:00:00Z","trust":"local-trusted"}
{"path":"/Users/scott/.ostk/claude-code","name":"claude-code","fingerprint":"ABCD1234...","last_boot":"2026-03-09T17:00:00Z","trust":"local-trusted"}
```

Same fingerprint = same operator = trusted. Different fingerprint = different
operator = requires explicit `ostk trust add`.

`ostk install` auto-registers in `~/.ostk/registry.jsonl`.

## Cross-OS Communication

### Write path (nudge only)

```
OS-A: ostk nudge --sign @ostk "needle add: distributed OS"
  → reads ~/.ostk/registry.jsonl → resolves @ostk → path
  → signs message with operator's GPG key
  → writes to {target}/.ostk/nudges/{source}.jsonl
  → appends to source audit: nudge.sent

OS-B: ostk boot
  → reads .ostk/nudges/*.jsonl
  → verifies signatures against trust registry
  → surfaces in digest: [nudge] (trusted) or [nudge?] (unknown)
  → operator/agent decides to act or ignore
  → appends to local audit: nudge.received
```

### What nudge carries

```json
{
  "from": "claude-code",
  "from_path": "/Users/scott/.ostk/claude-code",
  "fingerprint": "ABCD1234...",
  "signature": "-----BEGIN PGP SIGNATURE-----...",
  "message": "needle →541 added: distributed operating system [P1]",
  "timestamp": "2026-03-09T18:35:00Z"
}
```

### What nudge does NOT carry

- File edits (no cross-OS str_replace)
- Needle mutations (no cross-OS close/update)
- Process commands (no cross-OS reap/spawn)

These require the receiving OS to act on the nudge locally.

## HUMANFILE Changes

Add to HUMANFILE:

```
gpg-fingerprint: ABCD1234EFGH5678
trust-policy: local-signed-only  # or: local-all, self-only
```

`trust-policy` controls what the OS accepts:
- `self-only`: no cross-OS nudges (maximum isolation)
- `local-signed-only`: accept nudges from signed, registered OS instances
- `local-all`: accept all local nudges (current behavior, least secure)

Default: `local-signed-only`

## Audit Trail

Every cross-OS interaction is auditable:

```
ostk trace nudge
  → shows: who sent, from which OS, GPG signature, timestamp
  → verifiable: git log --show-signature on nudge commits
  → attributable: fingerprint → git account → human
```

## New Commands

| Command | What |
|---------|------|
| `ostk trust list` | Show registry with trust levels |
| `ostk trust add <fingerprint>` | Trust a GPG fingerprint |
| `ostk trust revoke <fingerprint>` | Revoke trust |
| `ostk nudge --sign @name "msg"` | Signed cross-OS nudge |
| `ostk nudge --verify` | Verify pending nudges |

## Compounds

- **Audit**: every cross-OS interaction is a signed, traceable event
- **Trust**: GPG + git = infrastructure that already exists
- **Safety**: no direct writes, nudge-only, operator decides
- **Control**: trust-policy in HUMANFILE, operator sets boundary
- **Adoption**: zero new infrastructure — GPG keys and git accounts are universal

## What's Rejected

- Direct cross-OS file edits (violates isolation)
- Shared lock files across OS instances (violates independence)
- Central coordination daemon (violates Unix philosophy)
- OAuth/API keys for local communication (overengineered)
- Automatic trust based on same-machine (insufficient for multi-user)

## Milestones (points)

1. `ostk install` writes to `~/.ostk/registry.jsonl` — discovery works
2. `ostk boot` reads nudge queue and surfaces `[nudge]` — delivery works
3. `ostk nudge --sign` creates GPG-signed nudge — trust works
4. `ostk trust add/revoke` manages registry — control works
5. Full round-trip: OS-A nudges OS-B, OS-B boots, sees signed nudge, acts — isolation works
