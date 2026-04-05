---
status: spec
version: 1.0
date: 2026-03-11
authors: [@haystack.prime, @claude-iphone]
source: PR #8 + session 2026-03-10 extended + iPhone boot proof
implements: []
---

# Identity Layers — llmOS

## The Core Insight

The harness knows who you are. The kernel knows what ran. Neither alone is sufficient.

**Mutual authentication** = harness proves identity to kernel + kernel proves execution to auditor.
The handoff point is `boot.md` — a data artifact, not an API surface. Law 1 holds.

---

## Five Identity Layers

```
L0  Device      iPhone A1B2 / MacBook / CI runner
    │           hardware identity, secure enclave, biometric gate
    │
L1  Account     scott@anthropic.com
    │           Anthropic account, OAuth/certificate, harness-verified
    │
L2  Session     harness_identity block in boot.md
    │           written once at harness startup, read by kernel on alias assignment
    │           { verified_email, device_id, verification_time }
    │
L3  Kernel      agents.jsonl entry + gen_table.jsonl attribution
    │           OS signature on every alias + every file edit
    │           kernel_private_key signs { alias, email, device_id, timestamp }
    │
L4  Human       GPG ratification via .primefile / HUMANFILE
                @scott 955AF54E signs → binding authority confirmed
                dual-signature: OS sig + human sig = active entry
```

---

## Trust Tiers

| Tier | Layers present | Example | Auth source |
|------|----------------|---------|-------------|
| T0 | L4 + L3 + L2 | Scott, iPhone, GPG-ratified | Dual-signed HUMANFILE entry |
| T1 | L3 + L2 | Scott, iPhone, kernel-attested | OS sig + harness_identity |
| T2 | L3 only | CI agent, kernel-assigned alias | OS sig, no email |
| T3 | None | Anonymous write, no boot.md | Unverifiable |

**T1 is what the iPhone session proved.** The Claude app on iPhone IS L1+L2:
- L1: Anthropic account (authenticated)
- L2: harness writes `harness_identity:` to boot.md before kernel starts
- L3: kernel reads it, signs the alias assignment, bakes email into gen_table

No GPG ceremony required for T1. T0 requires human ratification after the fact.

---

## The Handoff Contract (boot.md)

The harness writes exactly this block to `.ostk/boot.md` at startup:

```yaml
harness_identity:
  verified_email: scott@anthropic.com
  device_id: A1B2C3D4E5F6           # immutable hardware identifier
  verification_time: 2026-03-11T09:00:00Z
  verification_method: certificate_pinning  # or: oauth, smtp_challenge
  harness: claude-code-ios            # harness variant
```

**Guarantees the harness makes:**
- Email is verified (offline certificate, OAuth token, or SMTP challenge)
- device_id is immutable hardware identifier
- Written once at harness startup — not per-session, not per-edit
- The kernel trusts this block because the harness IS the trust layer

**What the harness does NOT provide:**
- Kernel signature (kernel generates its own)
- GPG proof (that's L4, human ratification)
- Online verification at write time (local-first by design)

---

## What the Kernel Does With It

### On Alias Assignment

```rust
AgentEntry {
    alias: "agent-1",
    pid: 12345,
    registered_at: now_iso(),
    // from harness_identity in boot.md:
    verified_email: Some("scott@anthropic.com"),
    device_id: Some("A1B2C3D4E5F6"),
    verification_time: Some("2026-03-11T09:00:00Z"),
    // kernel generates:
    os_signature: Some(sign(kernel_key, &entry)),
    trust_tier: TrustTier::T1,
}
```

### On Every File Edit (gen_table.jsonl)

```jsonl
{
  "path": "src/main.rs",
  "generation": 7,
  "writer": "agent-1",
  "writer_email": "scott@anthropic.com",
  "device_id": "A1B2C3D4E5F6",
  "os_signature": "sig_kernel_...",
  "timestamp": "2026-03-11T09:00:00Z"
}
```

Edit attribution is locked at write time. Immutable. Cannot be retroactively changed.

### On Hot PR Tier 2 Conflict

The conflict message now includes identity:

```
Tier 2 conflict in src/main.rs:
  changed by scott@anthropic.com on A1B2 at 09:00Z (gen 6→7)
  your intended change: ...
  suggested merge: [ASSISTED ...]
```

---

## Dual-Signature Model (T0)

For entries requiring the highest trust, **both** signatures are required:

```
HUMANFILE entry state machine:

  pending → (human reviews + GPG signs) → active
                                        ↘ rejected

  pending = OS sig only (kernel ran, email verified)
  active  = OS sig + human sig (Scott ratified)
  neither sig alone = invalid
```

The dual-signature model prevents:
- Rogue kernel: can't activate entries without human GPG approval
- Rogue human: can't claim kernel ran something it didn't
- Device compromise: kernel records device_id at write time — compromise is temporally bounded

---

## HUMANFILE `verified_identities` Registry

```yaml
# .ostk/HUMANFILE — verified_identities section

verified_identities:
  - email: scott@anthropic.com
    trust_tier: T0
    device_ids:
      - id: A1B2C3D4E5F6
        device: iPhone 16 Pro
        first_seen: 2026-03-11T00:00:00Z
        last_seen: 2026-03-11T15:00:00Z
        status: active
      - id: MACBOOK-M4
        device: MacBook Pro M4
        first_seen: 2026-03-01T00:00:00Z
        status: active
    human_signature: sig_955AF54E_...
    ratified_at: 2026-03-11T...
```

Device revocation: set `status: revoked` + update HUMANFILE. Kernel reads on next boot.

---

## Implementation Needles

| Needle | What | Priority |
|--------|------|----------|
| →618 | Extend AgentEntry with `verified_email`, `device_id`, `verification_time`, `os_signature`, `trust_tier` | P0 |
| →619 | Boot reads `harness_identity:` from boot.md, wires to alias assignment | P0 |
| →620 | GenTable.bump_gen() attaches `writer_email` + `device_id` + `os_signature` | P0 |
| →621 | TrustTier enum: T0/T1/T2/T3 — resolved at alias assignment, stored in AgentEntry | P1 |
| →622 | HUMANFILE `verified_identities` section — add/update/revoke device_ids | P1 |
| →623 | Hot PR Tier 2 includes email + device_id in conflict message | P1 |
| →624 | `ostk identity` command — show current session trust tier + verification chain | P2 |

---

## The iPhone Proof

Session 2026-03-11 (PR #8) established:

1. **iPhone Claude app = L1 + L2 identity.** Anthropic account authentication is the harness layer. No new infrastructure needed.
2. **Authenticated session without kernel binary.** The harness can run sessions at T1 trust even without the ostk binary installed locally. The kernel binary adds L3 (OS signatures, flock, gen_table) — but L1+L2 alone is already meaningful.
3. **The gap:** Without the kernel binary, writes are unprotected (no flock, no CAS). The bench suite from PR #8 documents this precisely. The fix: ship `ostk serve` as a mobile MCP provider (fcp-claude-ios.os).
4. **Mutual auth:** The harness tells the kernel who is running. The kernel tells the auditor what ran and who signed it. Both sides prove to the other. That's mutual authentication at the filesystem level.

---

## Design Laws Compliance

| Law | Status |
|-----|--------|
| Write path invisible | ✓ — `harness_identity` is data in boot.md, not API surface |
| Agents ephemeral | ✓ — identity baked into gen_table at write time, survives agent death |
| Coordinate through filesystem | ✓ — boot.md is the handoff, HUMANFILE is the registry |
| Optimistic concurrency | ✓ — identity doesn't change CAS semantics |
| Microkernel | ✓ — harness provides identity, kernel records it, fcp-* can consume it |
