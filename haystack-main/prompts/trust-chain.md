Write docs/spec/trust-chain.md — the formal trust chain build spec for ostk v2.0.

Read first:
- .ostk/.primefile
- docs/spec/GOVERNANCE.md
- docs/spec/mint.md
- docs/spec/abandonment.md
- ~/.ostk/SIGNING_CEREMONY.md
- docs/spec/KERNEL_UPDATE_PROTOCOL_v1.1.md

## 1. Trust tiers
T0: human operator (BAF08C963C7E3184, ed25519, scott@ostk.ai, 2y expiry) — root, air-gapped. Supersedes 955AF54E.
T-recovery: recovery key (586F4DD01D57E8F2, ed25519, recovery@ostk.ai, 5y, OFFLINE) — emergency T0 substitute
T1: haystack.prime v2.0 (907A200DA6C869EB, ed25519, kernel@haystack.prime, no expiry) — kernel authority, counter-signed by T0. Supersedes D4889BFC/99B076C9.
T1-CI: haystack.prime.ci (C78631AA6893C46C, ed25519, unchanged, re-cross-signed by new T1) — CI subordinate, revocable
T2: named agent alias — session-scoped, no signing authority
T3: anonymous instance — ephemeral
Org: 69FD844FE1B53386 (ed25519, contact@ostk.ai) — replaces 80DD4220

## 2. Primefile ceremony
When to update: new kernel version, new key, governance change, recovery key event.
Steps:
1. Edit .ostk/.primefile — update version field, kernel_key, recovery_key
2. gpg --sign with 907A200DA6C869EB (@haystack.prime v2.0)
3. Counter-sign with BAF08C963C7E3184 (@scott)
4. git add .ostk/.primefile && git commit
5. Tag AFTER all commits (tags are immutable — never retag)
Recovery exception: 586F4DD01D57E8F2 may substitute for T0 during compromise recovery.

## 3. @import tack verb
What: an OS identity importing another OS kernel.
Flow: @import <url-or-path> → fetch → verify .asc → verify primefile chain → install.
Trust boundary: T1 key proposes import PR, T0 human finalizes merge.
Example: ostk-site @importing ostk kernel at v2.0.

## 4. CI key boundary
C78631AA6893C46C CAN sign: release binaries, tarballs, .asc files.
C78631AA6893C46C CANNOT sign: .primefile, GOVERNANCE.md, ENTITYFILE, kernel identity docs.
Revocation: T0 removes cross-sig from .primefile. Key is dead.

## 5. OS signed binary
Tarball carries: ostk + .asc signatures.
Chain: C78631AA6893C46C (CI) → 907A200DA6C869EB (@haystack.prime v2.0) → BAF08C963C7E3184 (@scott operator).
Recovery backstop: 586F4DD01D57E8F2 (OFFLINE cold spare).
The binary IS the OS identity carrier. No primefile = no trust.

## 6. v2.0 tag ceremony checklist
Ordered, reproducible:
1. cargo test — all suites 0 failures
2. Cargo.toml version = 2.0.0
3. ostk --version shows ostk 2.0.0
4. ostk --version shows ostk 2.0.0
5. git status clean (or stage all)
6. git commit "feat: v2.0.0 release"
7. Update .ostk/.primefile version to 2.0.0
8. gpg sign .primefile (907A200DA6C869EB then BAF08C963C7E3184)
9. git tag v2.0.0
10. Push tag → CI builds 4-target binary + .asc

## 7. Catastrophic loss
See docs/spec/abandonment.md for recovery key design, compromise response
protocols, and full abandonment ceremony.

Closes →757 when spec is written.
