---
status: spec
version: 1.0
date: 2026-03-19
implements: []
---

# Catastrophic Loss Ceremony Spec

> Closes →758

This document specifies the response protocols for catastrophic key
loss or compromise events in the ostk trust chain.  It is
referenced by `docs/spec/trust-chain.md` (v2.0) and
`docs/spec/GOVERNANCE.md` (v1.3, Part 14).

**Design principle:** Exhaust expiry before revocation before
abandonment.  Each tier is more disruptive than the last.  Use the
lightest response that restores chain integrity.

---

## 1. Threat Model

| Scenario | Severity | Recovery path | Chain intact? |
|----------|----------|--------------|---------------|
| **T0 lost** (key inaccessible, not compromised) | HIGH | Recovery key activates as T0 substitute → mint new T0 → re-seal recovery | Yes, after re-mint |
| **T0 compromised** (adversary holds T0 private key) | CRITICAL | Revoke T0 immediately → recovery key mints new T0 → re-sign full chain → audit all T0-signed artifacts | Yes, after full re-sign |
| **T1 compromised** (adversary holds T1 private key) | HIGH | T0 revokes T1 → mint new T1 → re-cross-sign CI → re-sign primefile | Yes, T0 still authoritative |
| **T1-CI compromised** (adversary holds CI private key) | MEDIUM | T0 removes CI cross-sig from primefile → re-run primefile ceremony → mint new CI key | Yes, minimal disruption |
| **T0 + recovery compromised** (both held by adversary) | CATASTROPHIC | Full abandonment — genesis reset required (Section 5) | No — chain is void |
| **T0 + T1 compromised** (both held, recovery intact) | CRITICAL | Recovery key revokes both → mint new T0 and T1 → full chain rebuild | Yes, after full rebuild |

---

## 2. Recovery Key Design

### Identity

- **Key:** `586F4DD01D57E8F2` (ed25519, recovery@ostk.ai, 5y expiry)
- **Tier:** T-recovery (between T0 and T1 in the trust hierarchy)
- **Status:** OFFLINE at all times during normal operations

### Storage

- **Primary:** Bitwarden vault (encrypted, hardware-key-protected)
- **Secondary:** Paper backup (printed ASCII-armored private key, sealed
  envelope, physically secured)
- **Tertiary:** No third copy.  Two copies limit blast radius.

### Activation conditions

The recovery key activates **only** when:

1. T0 (`BAF08C963C7E3184`) is confirmed lost or compromised, OR
2. T0 + T1 are both compromised and recovery is the last standing
   authority, OR
3. @scott explicitly authorises activation for a planned T0 key
   rotation ceremony

The recovery key MUST NOT be used for:

- Routine primefile signing
- CI key cross-signing
- Any operation where T0 is available and uncompromised

### Rotation

The recovery key is rotated when:

- It has been activated (always rotate after use)
- It approaches expiry (rotate at 4y, 1y before 5y expiry)
- Storage medium is suspected compromised

---

## 3. Compromise Response: T0 Compromised

**Severity:** CRITICAL
**Time target:** Complete within 24 hours of detection.

### Steps (ordered, mandatory)

```
1. DECLARE
     Post signed advisory (using recovery key) to governance channel.
     State: T0 key BAF08C963C7E3184 is compromised.
     All artifacts signed by this key after <timestamp> are suspect.

2. RETRIEVE RECOVERY KEY
     Retrieve 586F4DD01D57E8F2 from Bitwarden vault.
     Verify fingerprint against paper backup before use.

3. MINT NEW T0
     gpg --full-gen-key → ed25519, scott@ostk.ai, 2y expiry
     Record new fingerprint.
     Recovery key certifies new T0:
       gpg --local-user 586F4DD01D57E8F2 --sign-key <new_T0_fingerprint>

4. RE-SIGN CHAIN
     New T0 counter-signs current primefile:
       gpg --armor --detach-sign --local-user <new_T0> .ostk/.primefile
     New T0 cross-signs T1 (907A200DA6C869EB):
       gpg --local-user <new_T0> --sign-key 907A200DA6C869EB
     Run full primefile ceremony (trust-chain.md Section 2).

5. AUDIT
     Review all artifacts signed by old T0 since last known-good timestamp.
     Any artifact signed after compromise window: re-sign with new T0 or void.

6. GOVERNANCE UPDATE
     Update docs/spec/trust-chain.md — replace old T0 fingerprint.
     Update docs/spec/GOVERNANCE.md — replace @scott key reference.
     Update docs/spec/mint.md — replace key references.
     Update prompts/trust-chain.md — replace key references.
     Commit with message: "security: T0 key rotation — compromise response"

7. RE-SEAL RECOVERY
     Generate NEW recovery key (old one was activated, must rotate).
     Store per Section 2 storage protocol.
     Update trust-chain.md with new recovery fingerprint.
     Run primefile ceremony with updated recovery_key field.
```

---

## 4. Compromise Response: T1 Compromised

**Severity:** HIGH
**Time target:** Complete within 48 hours of detection.

This is simpler because T0 is still authoritative and can unilaterally
revoke and replace T1.

### Steps (ordered, mandatory)

```
1. DECLARE
     Post signed advisory (using T0 key) to governance channel.
     State: T1 key 907A200DA6C869EB is compromised.

2. REVOKE T1
     T0 removes T1 cross-sig from primefile.
     Push revocation commit.

3. MINT NEW T1
     gpg --full-gen-key → ed25519, kernel@haystack.prime, no expiry
     T0 certifies new T1:
       gpg --local-user BAF08C963C7E3184 --sign-key <new_T1_fingerprint>

4. RE-CROSS-SIGN CI
     New T1 cross-signs CI key (C78631AA6893C46C):
       gpg --local-user <new_T1> --sign-key C78631AA6893C46C

5. PRIMEFILE CEREMONY
     Run full ceremony (trust-chain.md Section 2) with new T1.

6. UPDATE DOCS
     Replace T1 fingerprint in trust-chain.md, GOVERNANCE.md, mint.md,
     prompts/trust-chain.md.
     Commit: "security: T1 key rotation — compromise response"
```

---

## 5. Full Abandonment Protocol

**Trigger:** Both T0 and recovery key are compromised or irrecoverably
lost.  No authority remains to certify a replacement.

**Severity:** CATASTROPHIC — the entire trust chain is void.

### What abandonment means

The current trust chain cannot be repaired.  There is no key that can
authoritatively mint a replacement T0.  All existing signatures are
suspect.  A genesis reset is required.

### Steps

```
1. DECLARE ABANDONMENT
     Public advisory: the ostk trust chain rooted at
     BAF08C963C7E3184 is void as of <timestamp>.
     All releases signed under this chain should be treated as
     unverified from this point forward.

2. GENESIS RESET
     Generate entirely new key hierarchy:
       — New T0 (ed25519, scott@ostk.ai, 2y expiry)
       — New T-recovery (ed25519, recovery@ostk.ai, 5y expiry, OFFLINE)
       — New T1 (ed25519, kernel@haystack.prime, no expiry)
       — New T1-CI (ed25519, re-cross-signed by new T1)
       — New Org key (ed25519, contact@ostk.ai)

3. OUT-OF-BAND VERIFICATION
     The new T0 must be verified through an out-of-band channel:
       — In-person key signing with known collaborators
       — Video call with screen-shared fingerprint verification
       — Multiple independent communication channels
     This step cannot be automated.  It is the human trust anchor.

4. RE-SIGN ALL GOVERNANCE
     New T0 + new T1 sign:
       — .ostk/.primefile
       — docs/spec/GOVERNANCE.md
       — docs/spec/trust-chain.md
       — ENTITYFILE
     All prior signed artifacts under the old chain are void.

5. NEW TAG
     The abandoned version range is dead.  The new chain starts at
     the next major version (e.g. v2.x chain abandoned → v3.0.0).

6. PUBLISH ADVISORY
     Include: old chain fingerprints, new chain fingerprints,
     void version range, out-of-band verification method used.
```

---

## 6. Expiry vs Revocation vs Abandonment

| Mechanism | Trigger | Disruption | Chain survives? | Who acts? |
|-----------|---------|-----------|-----------------|-----------|
| **Expiry** | Key reaches expiry date | Minimal — planned rotation | Yes | Key holder (routine) |
| **Revocation** | Key compromised or role changed | Moderate — re-sign affected artifacts | Yes | Superior tier revokes inferior |
| **Abandonment** | Root + recovery both lost/compromised | Total — genesis reset, all sigs void | No — new chain created | Out-of-band human verification |

### Decision tree

```
Key event detected
  │
  ├─ Key approaching expiry?
  │     → Rotate key before expiry (planned ceremony)
  │     → Exhaust expiry: this is the lightest path
  │
  ├─ Key compromised, but superior tier intact?
  │     → Revoke: superior tier mints replacement
  │     → Revocation before abandonment
  │
  ├─ T0 compromised, recovery intact?
  │     → Recovery key substitutes for T0 (Section 3)
  │     → Still revocation tier — chain survives
  │
  └─ T0 + recovery both compromised?
        → Abandonment (Section 5)
        → Last resort only
```

---

## 7. T1-CI Compromise Response

**Severity:** MEDIUM
**Time target:** Complete within 72 hours.

The simplest recovery — CI key has no authority over governance
documents.

```
1. T0 removes CI cross-sig from .ostk/.primefile
2. Commit and push (CI key is immediately dead)
3. Generate new CI key
4. T1 cross-signs new CI key
5. Update primefile with new CI key block
6. Run full primefile ceremony
7. Update docs with new CI fingerprint
```

Existing release artifacts signed by the old CI key remain valid IF
the primefile inside the tarball was signed by valid T1 + T0.  The CI
sig is a convenience check, not the root of trust.

---

## 8. Audit Requirements

Every activation of the recovery key or execution of a compromise
response MUST be recorded in `.ostk/audit.jsonl`:

```json
{
  "event": "trust.compromise_response",
  "timestamp": "ISO8601",
  "affected_key": "<fingerprint>",
  "scenario": "t0_compromised | t1_compromised | t1ci_compromised | abandonment",
  "recovery_key_activated": true,
  "new_key_fingerprint": "<fingerprint>",
  "advisory_published": true,
  "completed_by": "@scott"
}
```
