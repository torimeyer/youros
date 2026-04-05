# HDP v1 Implementation — Team Delegation

**Status:** Draft → **PROMOTED** ✅ (2026-03-09)
**Timeline:** 11-17 weeks (11-13 with parallelization)
**Coordinator:** Need assignment
**Updated:** 2026-03-09

---

## Compiled Needles (5 PR Tickets)

All 5 PRs are **shippable independently**. Teams can work in parallel after PR-1 ships.

| # | Title | Phases | Owner | Duration | Dependency | Branch |
|---|-------|--------|-------|----------|-----------|--------|
| **PR-1** | gRPC Transport + Core Coordinator | 1-2 | **UNASSIGNED** | 3-5w | None | `pr-1-hdp-v1-grpc` |
| **PR-2** | Read Path + Elision | 3 | **UNASSIGNED** | 1-2w | PR-1 shipped | `pr-2-hdp-v1-read` |
| **PR-3** | Identity + Auth + Hot PR Tiers 2-4 | 4-5 | **UNASSIGNED** | 3-4w | PR-1 shipped | `pr-3-hdp-v1-auth` |
| **PR-4** | Remote Agent Client | 6 | **UNASSIGNED** | 2-3w | PR-2 + PR-3 shipped | `pr-4-hdp-v1-client` |
| **PR-5** | Integration Tests + Deployment Guide | 7 | **UNASSIGNED** | 2-3w | PR-1,2,3 shipped | `pr-5-hdp-v1-tests` |

---

## Critical Path

```
PR-1 (3-5w) ──────────┬──→ PR-4 (2-3w) ──┐
              ├──────→ PR-2 (1-2w) ──────┤
              └──────→ PR-3 (3-4w) ──────┤
                                         ↓
                                      PR-5 (2-3w)
```

**Fast path:** PR-1 ships week 5 → PR-2,3,4 parallel → PR-5 ships week 13
**Slow path:** Sequential merges → week 17

---

## Compound Dependencies

### PR-1 Unblocks
- PR-2 can design read path (RPC signatures exist)
- PR-3 can design auth layer (Identity traits exist)
- PR-4 can stub client (transport exists)

### PR-2,3 Block PR-4
- Read path HWM logic needed
- Auth token flow needed
- Together: client knows how to register + read

### All 1-4 Block PR-5
- Integration tests exercise all phases
- Load tests validate all tiers
- Deployment guide covers full stack

---

## File Scaffolding (Ready Now)

Teams start with these stubs already created:

```
proto/
├── hdp_v1.proto          [STUB READY]

src/hdp/
├── mod.rs                [STUB READY]
├── gen_table.rs          [IMPORT FROM kernel/]
├── hotpr.rs              [IMPORT FROM kernel/]
├── coordinator/
│   ├── mod.rs            [STUB READY]
│   ├── state.rs          [STUB READY]
│   └── cas.rs            [STUB READY]
├── client/
│   ├── mod.rs            [STUB READY]
│   └── grpc.rs           [STUB READY]
└── tests/
    ├── integration.rs     [STUB READY]
    └── load.rs           [STUB READY]
```

**Action:** Confirm scaffold creation before teams start.

---

## Delegation Strategy

### Phase 0: Scaffold + Assignments (This Week)
- [ ] Create PR-1 skeleton (proto stubs, mod.rs)
- [ ] Confirm team leads (suggest: 1 lead per PR, rotate mentors)
- [ ] Create GitHub issues (→511 through →515) linking this doc
- [ ] Pin DELEGATION.md in repo root for visibility

### Phase 1-2: PR-1 (Weeks 1-5)
- **Owner:** ASSIGN
- **Mentor:** Code review + design advice
- **Blocker:** None — start immediately
- **Success:** gRPC server listening, CAS working, Tier 1 auto-merge passes tests
- **Milestone:** `hdp-v1-phase-1-2-shipped`

### Phase 3: PR-2 (Weeks 2-6, parallel with PR-1 design)
- **Owner:** ASSIGN
- **Blocker:** PR-1 ships
- **Dependency:** Gen table + CAS logic (comes from PR-1)
- **Advantage:** Can design + spec during PR-1 review
- **Success:** 304 elision, HWM tracking, read tests
- **Milestone:** `hdp-v1-phase-3-shipped`

### Phase 4-5: PR-3 (Weeks 6-10, parallel with PR-1,2)
- **Owner:** ASSIGN
- **Blocker:** PR-1 ships
- **Complexity:** Auth + 3 new Hot PR tiers (hardest PR)
- **Advantage:** Can parallelize 4 + 5 internally
- **Success:** Tier 2 suggestions, Tier 3-4 diagnostics, load test 50 agents
- **Milestone:** `hdp-v1-phase-4-5-shipped`

### Phase 6: PR-4 (Weeks 8-11)
- **Owner:** ASSIGN
- **Blocker:** PR-2 + PR-3 ship
- **Dependency:** Both read + auth logic
- **Advantage:** Integrate with CI/CD early for fallback testing
- **Success:** Client registers, auth, fallback on coordinator down
- **Milestone:** `hdp-v1-phase-6-shipped`

### Phase 7: PR-5 (Weeks 10-13)
- **Owner:** ASSIGN
- **Blocker:** PR-1,2,3,4 ship
- **Scope:**
  - Integration test suite (single + multi-agent)
  - Load test (50 agents, latency p95 <100ms)
  - Deployment guide (upgrade, failover, partition recovery)
  - Operational playbooks
- **Success:** 80%+ coverage in hdp/, all existing tests pass
- **Milestone:** `hdp-v1-phase-7-shipped`

---

## Success Metrics (Go/No-Go per PR)

| PR | Go Criteria | No-Go Criteria |
|----|-------------|----------------|
| **PR-1** | gRPC listening, CAS works, Tier 1 merges | Server crashes, gen_table loses state |
| **PR-2** | 304 responses, HWM invalidation works | Read stalls, cache corruption |
| **PR-3** | JWT tokens issued, all 4 tiers functional | Auth bypass, conflict loss |
| **PR-4** | Client connects, fallback triggers | Connection leak, stale cache |
| **PR-5** | Load test p95 <100ms, 80% coverage | Latency >200ms, coverage <60% |

---

## Checklist: Promote → Compile → Delegate

### Promote (Verify Draft Is Ready)
- [x] Design doc complete: `docs/hdp_v1_implementation_plan.md`
- [x] Hot PR Tier 1 harness proven (→508-510)
- [x] Protocol spec exists: `docs/distributed-ostk-protocol.md`
- [x] File structure mapped

### Compile (Create Executable Work)
- [x] 5 PR branches created (`pr-1-hdp-v1-grpc` through `pr-5-hdp-v1-tests`)
- [x] This delegation document written
- [ ] GitHub issues created (→511-→515) with PR descriptions
- [ ] File stubs committed to branches

### Compound (Set Dependencies)
- [x] Critical path mapped (PR-1 → PR-2,3,4 → PR-5)
- [x] Parallel work windows identified (PR-1 & design PR-2,3 simultaneous)
- [ ] GitHub PR dependencies configured (requires PR merge for next to open)
- [ ] Milestone milestones created

### Delegate (Handoff Ready)
- [ ] Team leads assigned (5 owners)
- [ ] Mentors/reviewers assigned (suggest 2 per PR)
- [ ] Kickoff meeting scheduled
- [ ] DELEGATION.md pinned in repo

---

## Kickoff Checklist for Team Leads

**Before first commit:**

- [ ] Clone latest main
- [ ] Checkout your PR branch (`git checkout pr-N-hdp-v1-*`)
- [ ] Read `docs/hdp_v1_implementation_plan.md` (your phases)
- [ ] Read `docs/distributed-ostk-protocol.md` (full protocol)
- [ ] Run `cargo test` (baseline passes)
- [ ] Create scaffold stubs (see File Scaffolding above)
- [ ] Commit: `feat: PR-N scaffold — [phases]. (#→NNN)`
- [ ] Open draft PR, tag mentor for async feedback

---

## Questions to Resolve Before Starting

1. **PR Cadence:** Weekly merges (fast) or biweekly (thorough)?
2. **Parallel Work:** Can PR-4 team start client design during PR-1 review?
3. **Load Testing:** Scope (10, 50, 100 agents)?
4. **Deployment Targets:** Dev only, staging, or production?
5. **Feature Flags:** HDP behind flag (hdp_enabled) or always-on?
6. **Review SLO:** Target review turnaround (48h, 72h)?

---

## Communication

- **Weekly sync:** Monday 10:00 AM (20 min check-in)
- **Async updates:** PR descriptions + comments
- **Blockers:** Raise in #ostk-hdp Slack
- **Handoff point:** This file + 5 GitHub issues

---

**Status: READY FOR TEAM ASSIGNMENT** ✅

Branches exist. Plan is compiled. Waiting for team leads to claim work.
