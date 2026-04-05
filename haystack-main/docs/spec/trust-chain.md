---
title: "ostk Trust Chain Build Spec — v2.0"
implements: []
---

# ostk Trust Chain Build Spec — v2.0

> Closes →757

This document is the authoritative specification for the ostk trust
chain: who can sign what, when ceremonies are required, and the exact
ordered steps to produce a v2.0.0 release.  All claims below are
binding on every tier of the chain.

---

## 1. Trust Tiers

Five tiers exist.  Each tier is strictly subordinate to the tier above
it.  Authority flows downward; revocation flows upward.

| Tier | Identity | Key | Scope |
|------|----------|-----|-------|
| **T0** | Human operator — @scott | `BAF08C963C7E3184` (ed25519, scott@ostk.ai, 2y expiry) | Root authority. Air-gapped. Signs nothing routine; counter-signs T1 primefile only. Supersedes `955AF54E`. |
| **T-recovery** | Recovery key | `586F4DD01D57E8F2` (ed25519, recovery@ostk.ai, 5y expiry, OFFLINE) | Emergency T0 substitute. Cold spare. Activates only during catastrophic loss. See `docs/spec/abandonment.md`. |
| **T1** | `haystack.prime` v2.0 | `907A200DA6C869EB` (ed25519, kernel@haystack.prime, no expiry) | Kernel authority. Signs primefile, GOVERNANCE.md, ENTITYFILE, kernel identity docs, and cross-signs CI key. Supersedes `D4889BFC`/`99B076C9`. |
| **T1 CI** | `haystack.prime.ci` | `C78631AA6893C46C` (ed25519, unchanged, re-cross-signed by new T1) | CI subordinate. Cross-signed by `907A200DA6C869EB`. Revocable by T0 removing cross-sig from primefile. |
| **T2** | Named agent alias | _(none)_ | Session-scoped. No signing authority. |
| **T3** | Anonymous instance | _(none)_ | Ephemeral. No signing authority. |

### Org key

| Role | Key |
|------|-----|
| **Org** | `69FD844FE1B53386` (ed25519, contact@ostk.ai) — replaces `80DD4220` |

The org key is not part of the signing hierarchy.  It is used for
organisational contact verification only.

### Tier invariants

- **T0 is air-gapped.**  `BAF08C963C7E3184` never touches a networked
  machine during a signing session.  The counter-sign step for primefile
  requires physical access.
- **T-recovery is OFFLINE.**  `586F4DD01D57E8F2` is stored in Bitwarden
  vault + paper backup.  It never participates in routine operations.
  It activates only when T0 is lost or compromised.  See
  `docs/spec/abandonment.md` for the full catastrophic loss protocol.
- **T1 (`907A200DA6C869EB`) is online-capable** but should not be on an
  automated CI runner.  It is the kernel identity; treat the private
  key accordingly.
- **T1 CI (`C78631AA6893C46C`) is fully automated** and lives on the CI
  runner.  Its trust derives entirely from the cross-signature by
  `907A200DA6C869EB`.  Removing that cross-sig from `.ostk/.primefile`
  is sufficient to revoke it globally — no CRL needed.
- **T2/T3 have zero cryptographic standing.**  They cannot produce
  artifacts that downstream consumers should trust.

---

## 2. Primefile Ceremony

`.ostk/.primefile` is the root-of-trust document for a running
ostk instance.  It encodes the current kernel version and the full
key chain.  A primefile with a broken signature chain MUST be rejected
at boot.

### When to run the ceremony

Run the primefile ceremony when **any** of the following change:

- Kernel version number (e.g. 1.3.x → 2.0.0)
- Any key in the chain (new key, rotation, revocation)
- GOVERNANCE.md (content or signers)
- Cross-sig grant or revocation for `C78631AA6893C46C`
- Recovery key rotation or activation event

### Ceremony steps

Perform these steps **in order**.  Do not skip or reorder.

```
1.  Edit .ostk/.primefile
      — update `version` field to the new version string
      — update `kernel_key` to 907A200DA6C869EB if key changed
      — update `ci_key` cross-sig block if CI key changed
      — update `recovery_key` field if recovery key changed
      — record timestamp (ISO 8601, UTC)

2.  Sign with T1 kernel key
      gpg --armor --detach-sign \
          --local-user 907A200DA6C869EB \
          .ostk/.primefile
      # produces .ostk/.primefile.asc (T1 sig)

3.  Counter-sign with T0 operator key (air-gapped)
      # transport .primefile + .primefile.asc to air-gapped machine
      gpg --armor --detach-sign \
          --local-user BAF08C963C7E3184 \
          .ostk/.primefile
      # produces .ostk/.primefile.t0.asc (T0 counter-sig)
      # transport both .asc files back

4.  Stage and commit
      git add .ostk/.primefile \
              .ostk/.primefile.asc \
              .ostk/.primefile.t0.asc
      git commit -m "chore: primefile ceremony — v<VERSION>"

5.  Tag AFTER the commit
      git tag v<VERSION>
      # Tags are immutable.  Never retag an existing version.
      # A retag voids the chain for that version.
```

### Verification (any consumer)

```
gpg --verify .ostk/.primefile.asc    .ostk/.primefile
gpg --verify .ostk/.primefile.t0.asc .ostk/.primefile
```

Both verifications MUST pass.  A single-sig primefile (T1 only, no T0
counter-sig) is **not trusted** for production use.

**Recovery key exception:** During a T0 compromise recovery (see
`docs/spec/abandonment.md`), the recovery key `586F4DD01D57E8F2` may
substitute for `BAF08C963C7E3184` in step 3 until a new T0 key is minted.

---

## 3. `@import` Tack Verb

`@import` allows one OS identity to pull in another OS kernel.  It is
the primary mechanism by which a project (e.g. `ostk-site`) adopts a
ostk kernel release.

### Flow

```
@import <url-or-path>
  │
  ├─ fetch tarball + .asc sidecar
  │
  ├─ verify .asc against known-good key ring
  │     gpg --verify <tarball>.asc <tarball>
  │     MUST be signed by C78631AA6893C46C (CI) with chain to 907A200DA6C869EB
  │
  ├─ extract .ostk/.primefile from tarball
  │
  ├─ verify primefile chain
  │     T1 sig (907A200DA6C869EB) present and valid?  → pass
  │     T0 counter-sig (BAF08C963C7E3184) present?    → pass
  │     Both fail?                                     → ABORT, do not install
  │
  └─ install kernel into target OS
```

### Trust boundary at import time

| Actor | Role |
|-------|------|
| T1 key (`907A200DA6C869EB`) | Proposes import PR (automated or manual) |
| T0 human (@scott)            | Reviews diff, merges PR — **final gate** |

The import PR MUST NOT be merged by an automated process alone.  A T0
human merge is required.  This applies even when the source tarball
has a valid full chain.

### Example: ostk-site imports ostk v2.0

```
# Inside ostk-site OS session:
ostk @import https://releases.ostk.tools/v2.0.0/ostk-v2.0.0-x86_64-linux.tar.gz

# ostk fetches, verifies, opens a PR:
#   "import ostk kernel v2.0.0 (C78631AA6893C46C → 907A200DA6C869EB → BAF08C963C7E3184)"

# @scott reviews and merges — trust chain accepted.
```

---

## 4. CI Key Boundary

`C78631AA6893C46C` (`haystack.prime.ci`) is an **automation-only** subordinate
key.  Its signing authority is narrow and hard-bounded.

### What `C78631AA6893C46C` CAN sign

- Release binaries (all target triples)
- Release tarballs
- `.asc` sidecar files for the above

### What `C78631AA6893C46C` CANNOT sign

The following documents require T1 (`907A200DA6C869EB`) or higher:

| Document | Minimum signer |
|----------|---------------|
| `.ostk/.primefile` | T1 + T0 counter-sig |
| `docs/spec/GOVERNANCE.md` | T1 |
| `ENTITYFILE` | T1 |
| Kernel identity docs | T1 |
| Any document that confers signing authority | T1 |

A CI-signed primefile MUST be rejected.  Consumers verifying the chain
SHOULD check that the primefile sigs are `907A200DA6C869EB` and
`BAF08C963C7E3184` and explicitly MUST NOT accept `C78631AA6893C46C`
for that file.

### Revocation

To revoke the CI key:

```
1.  T0 opens .ostk/.primefile
2.  Remove the cross-sig block for C78631AA6893C46C
3.  Run full primefile ceremony (Section 2)
4.  Push — key is dead from this commit forward
```

No separate CRL publication is required.  Any consumer re-verifying
against a current primefile will find the cross-sig absent and MUST
reject artifacts signed by `C78631AA6893C46C` from that point on.

---

## 5. OS Signed Binary

The ostk release tarball is the **OS identity carrier**.  A binary
not accompanied by a valid primefile has no trust standing.

### Tarball contents

```
ostk-v2.0.0-<target>.tar.gz
├── ostk           (binary — ostk CLI)
├── ostk               (binary — OS toolkit)
├── .ostk/
│   ├── .primefile
│   ├── .primefile.asc      (T1 sig)
│   └── .primefile.t0.asc   (T0 counter-sig)
└── SHA256SUMS

ostk-v2.0.0-<target>.tar.gz.asc   (CI sig, C78631AA6893C46C)
```

### Trust chain in the binary

```
C78631AA6893C46C (haystack.prime.ci — CI runner)
    │  cross-signed by
    ▼
907A200DA6C869EB (haystack.prime v2.0 — kernel authority)
    │  counter-signed by
    ▼
BAF08C963C7E3184 (@scott — human operator, T0, air-gapped)
    │  recovery backstop
    ▼
586F4DD01D57E8F2 (recovery@ostk.ai — OFFLINE cold spare, T-recovery)
```

The tarball `.asc` (CI sig) proves the binary was built by the
authorised CI system.  The primefile inside the tarball proves that CI
system is trusted by the kernel authority, which is trusted by the
human operator.

**No primefile = no trust.**  A binary distributed without a
co-packaged primefile MUST NOT be installed by any conforming `@import`
implementation.

---

## 6. v2.0 Tag Ceremony Checklist

This checklist is **ordered and reproducible**.  Run each step to
completion before proceeding to the next.  A release with steps
reordered or skipped is not a valid v2.0.0 release.

```
 1.  cargo test
       All test suites: 0 failures.
       Do not proceed with any failure.

 2.  Cargo.toml
       Set version = "2.0.0"

 3.  ostk binary version check
       ostk --version
       Must print: ostk 2.0.0

 4.  ostk binary version check
       ostk --version
       Must print: ostk 2.0.0

 5.  Working tree
       git status
       Clean, or stage all intended changes.
       No untracked files that belong in the release.

 6.  Release commit
       git commit -m "feat: v2.0.0 release"

 7.  Primefile update
       Edit .ostk/.primefile — set version to 2.0.0

 8.  Primefile ceremony
       Sign with 907A200DA6C869EB  →  .primefile.asc
       Counter-sign with BAF08C963C7E3184  →  .primefile.t0.asc
       (Full ceremony per Section 2)
       git add .ostk/.primefile \
               .ostk/.primefile.asc \
               .ostk/.primefile.t0.asc
       git commit -m "chore: primefile ceremony — v2.0.0"

 9.  Tag
       git tag v2.0.0
       Tags are immutable.  Never retag v2.0.0 for any reason.
       If a tag must change, the release is void and a new patch
       version (v2.0.1) is required.

10.  Push
       git push origin main v2.0.0
       CI picks up the tag, builds 4-target binaries, signs each
       with C78631AA6893C46C, publishes tarballs + .asc sidecars.
```

### Build targets (step 10)

| Triple | Notes |
|--------|-------|
| `x86_64-unknown-linux-gnu` | Primary Linux |
| `aarch64-unknown-linux-gnu` | ARM64 Linux |
| `x86_64-apple-darwin` | macOS Intel |
| `aarch64-apple-darwin` | macOS Apple Silicon |

---

## 7. Verification Reference

Quick-reference commands for any consumer validating a release.

```bash
# 1. Verify tarball was built by CI
gpg --verify ostk-v2.0.0-<target>.tar.gz.asc \
             ostk-v2.0.0-<target>.tar.gz

# 2. Extract and verify primefile chain
tar xf ostk-v2.0.0-<target>.tar.gz .ostk/

gpg --verify .ostk/.primefile.asc    .ostk/.primefile
gpg --verify .ostk/.primefile.t0.asc .ostk/.primefile

# 3. Check SHA256SUMS
sha256sum -c SHA256SUMS

# All three checks must pass.  Any failure = abort install.
```

---

## 8. Invariants Summary

| Rule | Binding on |
|------|-----------|
| T0 is air-gapped; never automated | All |
| T-recovery is OFFLINE; activates only during catastrophic loss | All |
| T1 signs primefile; T0 counter-signs | Release ceremony |
| T1 CI cross-sig is revocable via primefile | CI key lifecycle |
| No primefile = no trust | All consumers |
| Tags are immutable; retag voids the release | All |
| Recovery key cannot substitute for T0 in routine operations | All |
| For catastrophic loss protocol, see `docs/spec/abandonment.md` | All |
