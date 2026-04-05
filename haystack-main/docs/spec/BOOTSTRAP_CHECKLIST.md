---
title: "BOOTSTRAP_CHECKLIST.md — Instance Startup Verification"
implements: []
---

# BOOTSTRAP_CHECKLIST.md — Instance Startup Verification

**Version:** 1.0
**Date:** 2026-03-10
**Scope:** All instances booting in @haystack.prime
**Status:** Mandatory verification (blocks startup on failure)

---

## Bootstrap Sequence

When instance boots, verify in order:

### Checkpoint 1: Kernel Lineage Exists ⚠️

```
✓ MUST have: .ostk/.primefile present
✓ MUST verify: GPG signature valid (signed by @scott)
✓ MUST match: Current kernel version (@haystack.prime v0.7.0)
✗ FAIL if: .primefile missing or corrupted
```

**Failure mode:** Boot proceeds with warning; alert @scott; core kernel work blocked

**Why:** Proves kernel is legitimate (not compromised)

---

### Checkpoint 2: Governance Rules Loaded ✓

```
✓ MUST have: GOVERNANCE.md present and readable
✓ MUST have: MANIFESTO.md present (context)
✓ MUST parse: Authority layers (what I can decide)
✓ MUST understand: Escalation paths (how to ask @scott)
✗ FAIL if: GOVERNANCE.md missing (fatal)
```

**Failure mode:** Cannot boot without governance rules (safety check)

**Why:** Without rules, instance doesn't know what it's allowed to do

---

### Checkpoint 3: Session Context Inherited ⏱️

```
✓ MUST have: .ostk/.SESSIONLOG (prior decisions)
✓ MUST parse: .SESSIONLOG as JSON (machine-readable)
✓ MUST understand: .PENDING_PHASES (what's waiting for me)
✓ MUST read: DECISION_RATIONALE.md (why prior decisions made)
⚠️ WARN if: .SESSIONLOG missing (first session OK, but log continuity broken)
```

**Failure mode:** Warn (missing context) but proceed (new session is valid)

**Why:** Enables learning from prior session without blocking startup

---

### Checkpoint 4: Authority Delegation Clear 📋

```
✓ MUST have: .ostk/.AUTHORITY_DELEGATION readable
✓ MUST parse: What authority I inherit
✓ MUST understand: What requires @scott approval
✓ MUST know: What kernel decides
✗ FAIL if: Authority boundaries undefined (ambiguous)
```

**Failure mode:** Boot blocked; cannot proceed without clear authority

**Why:** Prevents instances from exceeding their authority

---

### Checkpoint 5: Audit Trail Accessible 📊

```
✓ MUST have: audit trail location known (.ostk/audit.jsonl)
✓ MUST verify: audit trail is append-only (not corrupted)
✓ MUST test: Can write new entries (audit trail works)
✗ FAIL if: Audit trail is inaccessible or corrupted
```

**Failure mode:** Boot blocked; cannot proceed without working audit trail

**Why:** Without audit trail, decisions are not recorded. Unacceptable.

---

### Checkpoint 6: Kernel Version Compatible 🔄

```
✓ MUST verify: ENTITYFILE version matches
✓ MUST check: GOVERNANCE.md version matches expected
✓ ⚠️ WARN if: Governance rules have changed since last session
✓ MUST document: Rule changes to audit trail
```

**Failure mode:** Warn (new rules detected); document in audit trail; proceed

**Why:** Next session should know if governance changed

---

## Failure Handling

### Hard Fail (Block Startup)
**These checkpoints block boot if they fail:**
1. Kernel lineage invalid → Boot blocked until @scott intervenes
2. GOVERNANCE.md missing → Cannot proceed without rules
3. Authority delegation undefined → Cannot execute without clear authority
4. Audit trail corrupted → Cannot record decisions

**Action:** Alert @scott, wait for human intervention

### Soft Fail (Warn and Proceed)
**These checkpoints warn but allow boot:**
1. .SESSIONLOG missing → Missing context (but can start fresh)
2. Governance version changed → Document and proceed
3. Kernel signature stale (>3 session gap) → Re-verify but allow boot

**Action:** Log warning, alert @scott, proceed with non-critical work only

---

## Success State

**Bootstrap complete when:**
- ✓ All 6 checkpoints pass (or soft-fail appropriately)
- ✓ Instance understands its authority boundaries
- ✓ Instance can read audit trail
- ✓ Instance has context from prior session (if any)
- ✓ Instance has proof of kernel legitimacy (GPG signature)

**Then:** Instance is ready to execute work within governance constraints

---

## Implementation Details

### .SESSIONLOG Format
```json
[
  {
    "session_id": "unique_per_session",
    "timestamp": "2026-03-10T...",
    "phase": "EXECUTION",
    "decision": "merged_pr_3",
    "authority": "Level 1",
    "outcome": "async_mode_integrated",
    "rationale_ref": "DECISION_RATIONALE.md#async_mode"
  }
]
```

### .AUTHORITY_DELEGATION Format
```json
{
  "instance_type": "claude-haiku",
  "authority_level_1": ["execute_approved_work", "propose_improvements", "deny_unsafe_work"],
  "authority_level_2": ["requires_scott_approval"],
  "authority_level_3": ["kernel_decides_only"],
  "escalation_path": "@scott_via_HUMANFILE"
}
```

### Lineage Verification
```bash
# Check .primefile exists
ls -la .ostk/.primefile

# Verify GPG signature
gpg --verify .ostk/.primefile

# Check kernel version
grep "version" .ostk/.primefile
```

---

## Timeline

**At boot:**
- Checkpoints 1-6 execute in sequence
- Hard fails block immediately
- Soft fails warn and continue
- Total time: <1 second

**After boot:**
- Instance proceeds with governance understanding
- All decisions recorded to audit trail
- Next instance will read this context

---

## For Next Instance

When you boot, run this checklist:

1. [ ] Kernel lineage verified (GPG signature valid)
2. [ ] Governance rules loaded (GOVERNANCE.md present)
3. [ ] Session context inherited (SESSIONLOG readable)
4. [ ] Authority delegation clear (boundaries understood)
5. [ ] Audit trail accessible (can write entries)
6. [ ] Kernel version compatible (rules up to date)

If all pass → You can execute work
If any hard fails → Wait for @scott to intervene
If soft fails → Log warnings and proceed with caution

---

**Status:** BINDING bootstrap protocol

**Version:** 1.0

**Last updated:** 2026-03-10 (after Phase 4 implementation)
