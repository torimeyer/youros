# ostk Distributed Protocol (HDP) v1 — Implementation Plan

**Status:** Design complete, ready for phase-by-phase implementation
**Timeline:** 11-17 weeks (11-13 with parallelization)
**PR Strategy:** 5 grouped PRs (see below)

## Executive Summary

HDP v1 scales ostk from local (shared filesystem) to distributed (network-coordinated) agents while preserving OCC semantics, Hot PR conflict resolution, and end-to-end identity visibility.

**Architecture:**
- **Transport:** gRPC + HTTP/REST (fallback)
- **Coordinator:** Centralized, fail-stop partition semantics (CAP: Consistency)
- **Write Path:** Server-computed CAS + Tier 1-4 Hot PR
- **Identity:** TLS + JWT tokens, kernel-assigned aliases
- **Backward Compatibility:** Local agents unaffected, remote agents opt-in

**Can deploy Phase 1-3 independently** without auth/Hot PR Tiers 2-4.

---

## 7-Phase Breakdown

### Phase 1: Protocol & Transport (1-2 weeks) — MEDIUM
gRPC stubs, message definitions, coordinator skeleton

**Commit:** Phase 1 — gRPC transport + protocol definition
**PR 1 of 5**

### Phase 2: Core Coordinator (2-3 weeks) — LARGE
In-memory gen_table, server-side CAS, Tier 1 Hot PR

**Commit:** Phase 2 — Core coordinator + server-side CAS
**PR 1 of 5** (with Phase 1)

### Phase 3: Read Path & Caching (1-2 weeks) — MEDIUM
GetFile RPC, per-agent HWM, read elision (304 Not Modified)

**Commit:** Phase 3 — Read path + read elision + staleness detection
**PR 2 of 5**

### Phase 4: Identity & Auth (1 week) — MEDIUM
RegisterRequest, JWT tokens, TLS, heartbeat monitoring

**Commit:** Phase 4 — Identity + auth + heartbeat
**PR 3 of 5** (with Phase 5)

### Phase 5: Hot PR Integration (2-3 weeks) — LARGE
Tier 2 (assisted), Tier 3 (conflict), Tier 4 (diagnostics), contention backpressure

**Commit:** Phase 5 — Hot PR Tiers 2-4 + conflict resolution
**PR 3 of 5** (with Phase 4)

### Phase 6: Agent Client (2-3 weeks) — LARGE
Rust gRPC client, retry logic, connection pooling, transparent fallback

**Commit:** Phase 6 — Remote agent client + transparent fallback
**PR 4 of 5**

### Phase 7: Testing & Docs (2-3 weeks) — LARGE
Integration tests, load tests, backwards compatibility, deployment guide

**Commit:** Phase 7 — Integration tests + deployment guide
**PR 5 of 5**

---

## PR Strategy (5 Grouped PRs)

| PR | Phases | Scope | Shippable | Duration |
|----|--------|-------|-----------|----------|
| PR-1 | 1-2 | Protocol + Core Coordinator | Yes (local agents unaffected) | 3-5 weeks |
| PR-2 | 3 | Read path + elision | Yes (improves perf, no new semantics) | 1-2 weeks |
| PR-3 | 4-5 | Auth + Hot PR Tiers | Yes (complete conflict resolution) | 3-4 weeks |
| PR-4 | 6 | Remote Agent Client | Yes (transparent fallback) | 2-3 weeks |
| PR-5 | 7 | Tests + Docs | Yes (production-ready) | 2-3 weeks |

**Each PR is independently testable, reviewable, and deployable.**

---

## Critical Implementation Files

1. **`proto/hdp_v1.proto`** — Protocol definition (gRPC messages)
2. **`src/hdp/coordinator/state.rs`** — Coordinator state machine
3. **`src/hdp/coordinator/cas.rs`** — CAS implementation + Tier 1 Hot PR
4. **`src/hdp/client/grpc.rs`** — Remote agent client
5. **`src/hdp/gen_table.rs`** — Moved from kernel, shared between local + coordinator

---

## Success Criteria (Phase-by-Phase)

### PR-1 (Phases 1-2)
- [ ] gRPC server listening on :9999
- [ ] Single-agent CAS works (success path)
- [ ] Gen table bumps correctly
- [ ] Tier 1 auto-merge detects non-overlapping edits
- [ ] Backwards compatible: local agents unaffected
- [ ] Unit tests pass

### PR-2 (Phase 3)
- [ ] GetFileRequest returns file content + generation
- [ ] 304 Not Modified on cache hit (~5 tokens vs. 200)
- [ ] HWM invalidation on remote write
- [ ] Integration test: agent reads, reads again (expect 304)

### PR-3 (Phases 4-5)
- [ ] RegisterRequest assigns aliases + JWT token
- [ ] Tier 2 suggestion generated for overlapping edits
- [ ] Tier 3 escalation for deep conflicts
- [ ] All 4 tiers functional (1: silent, 2: assisted, 3-4: errors/warnings)
- [ ] Load test: 50 agents × 100 edits, conflict rate <5%

### PR-4 (Phase 6)
- [ ] Client connects, registers, gets token
- [ ] Client sends CAS, GetFile with auth
- [ ] Fallback to local kernel on coordinator down
- [ ] Retry logic with exponential backoff
- [ ] Backwards compatibility: existing tests pass

### PR-5 (Phase 7)
- [ ] Integration tests: single agent, multi-agent, conflicts
- [ ] Load test: 50 agents, latency p95 <100ms
- [ ] Deployment guide complete
- [ ] Operational playbooks (upgrade, failover, partition recovery)
- [ ] 80%+ code coverage in hdp/ modules

---

## Integration Points with Existing Codebase

- **`src/kernel/gen_table.rs`** → move to `src/hdp/gen_table.rs` (shared)
- **`src/kernel/hotpr.rs`** → move to `src/hdp/hotpr.rs` (extended for server-side)
- **`src/serve/tools/ss.rs`** → conditional dispatch (check `OSTK_HDP_COORDINATOR` env var)
- **`src/serve/dispatch.rs`** → track agent alias for audit + digest
- **`src/kernel/identity.rs`** → shared with coordinator

**All changes are backwards compatible.** Local agents (no HDP_COORDINATOR env var) run unchanged.

---

## Deployment Timeline

**Phases 1-2:** 3-5 weeks → Deploy PR-1 (core coordinator stable)
**Phase 3:** 1-2 weeks → Deploy PR-2 (read path optimization)
**Phases 4-5:** 3-4 weeks → Deploy PR-3 (auth + conflict resolution)
**Phase 6:** 2-3 weeks → Deploy PR-4 (remote agents go live)
**Phase 7:** 2-3 weeks → Deploy PR-5 (production-ready with full testing)

**Total:** 11-17 weeks (11-13 with Phase 4||5 parallelization)

---

## Questions for Stakeholders

1. **PR cadence:** Prefer fast (weekly) or slow (biweekly) merges?
2. **Parallel implementation:** Can teams work on Phases 4+5 simultaneously?
3. **Testing budget:** Load test scope (10, 50, 100 agents)?
4. **Deployment targets:** Dev (local), staging, or prod-ready?
5. **Feature flags:** Should HDP be behind a feature flag (hdp_enabled)?

---

## Next Steps

1. **Review this plan** → feedback/adjustments
2. **Create PR-1 skeleton** → Phase 1-2 implementation begins
3. **Parallel work:** Phase 3 design while PR-1 under review
4. **Weekly syncs:** Status + blockers
5. **Deploy Phase 1-2** → validate coordinator stability before Phase 3+

---

**Full design doc:** See `docs/distributed-ostk-protocol.md` (generated during design phase)
