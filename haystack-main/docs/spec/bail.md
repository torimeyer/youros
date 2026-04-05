---
title: "bail — Signed OS Package"
implements: []
---

# bail — Signed OS Package

**Status**: implemented (→bail)
**Command**: `ostk bail pack | unpack | verify`

---

## What bail is

A bail is a signed, portable OS package. Like a VM snapshot but GPG-gated. It lets an OS instance export its identity and boot state into a single file that another instance (on any harness, any machine) can receive and boot from.

The name comes from a bail bond — a guarantee. The package is a verifiable guarantee of OS state.

---

## Two modes

### Public mode: `ostk bail pack --public`

Produces a public bundle containing:
- `boot.md` — the swap file (public OS state)
- `.boot/INIT` — 25-line tack boot protocol (if present)
- `.primefile` — dual-signed identity (public key fingerprints, NOT private keys)

Signed by `@haystack.prime`. Any instance on any harness can boot from this — no decryption needed. **This solves adoption**: share the bail, the recipient can boot into your OS state immediately.

### Full mode: `ostk bail pack`

Produces everything in public mode, plus:
- `os.bin` — GPG-encrypted tarball of OS state files, encrypted to the prime recipient key

Prime-key-to-prime-key state transfer. Internal state never leaks to unsigned receivers.

---

## Package format

```
ostk.bail/
  manifest.json       — { version, created, signer, mode: "public"|"full", files: [...] }
  boot.md             — verbatim copy
  .boot/INIT          — verbatim copy (if present)
  .primefile          — verbatim copy (public key fingerprints only)
  os.bin              — (full mode only) GPG-encrypted tarball of OS state
  manifest.json.asc   — GPG detached signature over manifest.json
```

Packed as a `.tar.gz` renamed to `.bail`.

---

## What bail NEVER contains

These files are **excluded from all bail packages** — they are internal kernel state that must not leak:

| File / Dir | Why excluded |
|-----------|-------------|
| `audit.jsonl` | Internal audit trail |
| `sessions/` | Ephemeral session logs |
| `agents.jsonl` | Runtime agent state |
| `hwm.jsonl`, `hwm.lock` | High-water mark (runtime) |
| `gen_table.jsonl`, `gen_table.lock` | OCC generation counters |
| `identity_counter`, `identity.lock` | Runtime identity counters |
| `dispatch.json` | Kernel dispatch table |
| `*.secret` | Any secret files |
| `secrets/` | Secret directory |

Private keys never appear in any file, log, or error message.

---

## How Gemini (or any instance) can boot from a public bail

1. Receive `ostk.bail`
2. Run `ostk bail verify ostk.bail` — checks GPG signature
3. Run `ostk bail unpack ostk.bail` — applies boot.md + .primefile to current project
4. Run `ostk boot` — reads boot.md and reports OS state
5. Instance is now booted into the OS state of the original instance

No prime key needed for public mode. No decryption. The signature tells you the state is authentic.

---

## Verification flow

```
ostk bail verify ostk.bail
  -> extract manifest.json + manifest.json.asc
  -> gpg --verify manifest.json.asc manifest.json
     -> VERIFIED: print signer fingerprint
     -> UNVERIFIED: print reason, non-fatal
  -> print: created, mode, file count
```

Verification is **non-fatal** during unpack — the bail is still applied, but the operator is warned if unsigned. This lets unsigned bails circulate for development without blocking adoption.

---

## Commands

```
ostk bail pack [--public]    # pack a bail from current project
ostk bail unpack <path>      # verify + apply a bail
ostk bail verify <path>      # verify signature only, no changes
```

---

## Laws hold

All five design laws apply:

1. **Write path invisible** — bail uses standard fs operations, no new coordination APIs
2. **Agents ephemeral** — bail is the recovery mechanism when an agent dies across a machine boundary
3. **Coordinate through filesystem** — bail is a file; the OS state is in the filesystem
4. **Optimistic concurrency** — bail packing reads optimistically; unpack writes directly
5. **Invisible infrastructure** — `ostk bail` is a standard CLI command, not a new primitive
