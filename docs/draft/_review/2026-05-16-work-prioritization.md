# Work Prioritization — 2026-05-16

Reviewed by: prioritize-work-conflicting-comp-199189
Framework: conflicting first, compounding next (per Scott via tori)

---

## Inventory

**23 open needles** across P1/P2/P3. **4 active specs** (all in `~/.myos/specs/`, none in archive).

### Open needles

| ID | Priority | Title (short) | Status |
|----|----------|---------------|--------|
| →1270 | P1 | ostk binary stale loadavg cache — skip markers waiting on ostk >6.0.5 | open, upstream blocked |
| →1334 | P1 | Same: phase 2 ostk-cache blocked until ostk binary updates | open, upstream blocked |
| →1403 | P1 | saa bug-fix briefs must require BDD invariant test (not just unit) | open |
| →1404 | P1 | Spec quality gaps: real specs don't match the API structural contract | open |
| →1407 | P1 | test_agent_mailbox + test_pause_and_chat flake under full suite (event-loop or fixture leak) | open |
| →1408 | P1 | Missing shell test + stale assertion in kernel_ps scope | open |
| →1418 | P1 | Test Wizard spec (acceptance criteria) | open |
| →1422 | P1 | Spec-auto-status Phase 1: backend plumbing (claims registry, compute_spec_status, /claim endpoint) | open — ALREADY IN WORKTREE |
| →1423 | P1 | Spec-auto-status Phase 4: UI "Building" badge on Specs page | open |
| →1296 | P2 | Boot trust-root advisories — upstream ostk fix needed | open, upstream blocked |
| →1395 | P2 | mychat: plan mode toggle (confirmable banner before tool calls) | open |
| →1396 | P2 | mychat: live TodoWrite panel (model's own subtask list) | open |
| →1397 | P2 | mychat: receipts/verification gate (warns if "done" has no evidence) | open |
| →1398 | P2 | mychat: AskUserQuestion structured-picker (chips instead of "reply A or B") | open |
| →1409 | P2 | test_spec_counts still reads real ~/.myos/specs/ (tmpdir fix missed this) | open |
| →1411 | P2 | ostk doc promote routes new specs to docs/spec/ instead of ~/.myos/specs/ | open |
| →1421 | P2 | Left-nav consolidation Option B: move Tour, Activity, Rules into Settings | open — plan ready |
| →1424 | P2 | Spec-auto-status Phase 2a: wrapper-launched claim (myos chat --spec) | open, blocked by →1422 |
| →1425 | P2 | Spec-auto-status Phase 2b: agent self-register claim via preamble | open, blocked by →1422 |
| →1426 | P2 | Spec-auto-status Phase 2c: in-chat slash /build-spec | open, blocked by →1422 |
| →1427 | P2 | Spec-auto-status Phase 3: passive git-commit detection watcher | open, blocked by →1422 |
| →1428 | P2 | Plan-waves: add specs alongside needles in the UI candidate list | open |
| →1399 | P3 | mychat: background work (ScheduleWakeup/Monitor from chat) | open |
| →1400 | P3 | mychat: ostk audit/undo trail in chat (collapsible diffs + undo) | open |
| →1410 | P3 | Audit ~/.myos/users/<uid>/ directory strategy for future multi-user | open |

### Active specs

| Spec | Status | Linked needles | Notes |
|------|--------|----------------|-------|
| `left-nav-consolidation-plan.md` | Option B decided (2026-05-15 USER FEEDBACK) | →1421 | Plan written at `~/.claude/plans/needle-1421-left-nav-consolidation.md`. Ready to build. |
| `pattern-watcher.md` | Full spec, all decisions resolved | None filed yet | Blocked: `mem.fault_recall` is inactive (listed in `services_inactive` at boot). ostk-recall not wired. |
| `spec-auto-status.md` | Phase 1 committed in worktree | →1422 →1423 →1424 →1425 →1426 →1427 | Worktree `agent-implement-1420-spec-b-d757339c` has 2 commits including `feat(specs): auto-promote ready specs to building`. Needs merge review. |
| `vp-marketing-first-impression.md` | Spec written, build order defined | None filed yet | ~9 days of work across 3 blocks. No needles, no agent started. |

---

## "Command works sphere" investigation

**What I searched:**
- `search(query="command works sphere")` — returned only my own tool call in the session log. No code match.
- `search(query="commandworks")` — no results.
- `search(query="works sphere")` — same, only my own session events.
- `search(query="command sphere Scott", scope="all")` — no matches.
- `grep -rlin 'sphere' ~/.config/ostk-* ~/.ostk/ scripts/` — found `tokenizer.json` (has "sphere" as a vocabulary token) and `~/.myos/agent_memory/remove-where-this-task-sits-sphe-6fb243.json`.
- `grep -n 'sphere' sync_repo/api/services/ostk.py` — found it. "sphere" is a real ostk concept.
- `ostk work sphere --help` — real command.
- `ostk work compounds` — real command.

**What I found:**

"Sphere" is an ostk concept for a **cluster of related needles**. In ostk's data model:

- A sphere is a group of needles that are semantically or structurally connected.
- `ostk work sphere <ID>` shows the sphere digest for a needle: its cluster members, joints (connections), and nearby hay (ideas not yet turned into needles).
- `ostk work activate <ID>` returns a briefing with sphere details, neighbors, blockers.
- `ostk work refine` parses spheres, degree (connection count), and joints.
- The "sphere-radius-aware" pull logic is how ostk selects the highest-priority task (it prefers needles in active spheres).
- There was a UI section called "WHERE THIS TASK SITS" in Tasks.tsx that showed sphere info — it was removed (commit 5ef6451, agent memory dated 2026-04-28).

**My reading of what Scott said:**

Scott almost certainly meant `ostk work sphere` — the command that shows sphere/cluster context for a needle. This is the tool for understanding which needles are clustered together before deciding what to work on. The phrase "command works sphere" is likely "the `ostk work sphere` command."

If Scott meant something broader — a prioritization *framework* built around spheres — that framework is not documented anywhere I could find. The commands `ostk work sphere`, `ostk work compounds`, `ostk work radiate`, and `ostk work refine` together form ostk's native work-prioritization surface.

**Action for tori:** If Scott meant `ostk work sphere` as a tool to use during this analysis, I can run `ostk work sphere <needle-id>` on any specific needle to show its cluster. If he meant a different concept, please clarify and I will re-run.

---

## Conflicts table

| Item A | Item B | Conflict type | Pick first | Why |
|--------|--------|---------------|------------|-----|
| →1422 (spec-auto-status Phase 1) already in worktree | Any new spawn targeting →1422 | Resource: same file set | Let worktree finish | Worktree has 2 commits (`017bf77`). Spawning a second agent duplicates work and causes git conflicts on `api/routers/specs.py`, `api/services/ostk.py`. |
| →1423 (Phase 4 UI: Building badge) | →1422 Phase 1 (not yet merged) | Ordering: Phase 4 depends on Phase 1's `/claim` endpoint and `claims[]` array in the tasks response | →1422 first | Phase 4's badge reads `claims` from the tasks payload. That field doesn't exist in main yet. Building Phase 4 now means coding to an uncommitted contract. |
| →1421 (left-nav: removes Activity from sidebar) | Pattern-watcher spec (adds sidebar drawer "next to Activity") | Design: left-nav removes Activity's sidebar slot; pattern-watcher's panel placement references Activity's position | →1421 first | After →1421 lands, Activity lives in Settings. Pattern-watcher's implementation plan should reference the updated layout, not the old one. If pattern-watcher is spec'd to sit "next to Activity" and Activity is gone from the sidebar, the panel needs a different anchor. |
| →1411 (doc promote routes to wrong dir) | →1404 (spec quality gaps: specs don't match API contract) | Ordering: →1411's routing bug may be generating the malformed spec files that →1404 describes | →1411 first | If specs land in `docs/spec/` instead of `~/.myos/specs/`, the API can't find them, which causes the gaps →1404 is auditing. Fix the pipe before auditing its output. |
| VP marketing spec (replaces `api/routers/knowledge.py`) | Any future code using the current knowledge note-taking endpoints | Resource: API contract change (97-line file gets replaced) | Audit first, then build | Check if anything currently calls the knowledge endpoints before replacing them. Low blast radius today, could grow if ignored. |
| →1428 (plan-waves includes specs by status) | Spec-auto-status (changes what spec "status" means) | Design: plan-waves will display status labels. If status semantics change (adding "Building"), plan-waves needs to handle the new value | Spec-auto-status first | Build the status contract first. Plan-waves should read whatever status values exist after spec-auto-status lands, not assume the old set. |

---

## Compounding table

| Item | Leverage (1-5) | What it unlocks | Why this score |
|------|----------------|-----------------|----------------|
| →1422 Spec-auto-status Phase 1 (already in worktree) | 5 | Unblocks →1423, →1424, →1425, →1426, →1427 — the entire rest of the spec-auto-status spec | One backend merge frees 5 downstream needles simultaneously |
| VP marketing Block 1 (source library + KNOWLEDGE directive) | 4 | Unlocks VP marketing Blocks 2 and 3. Unblocks the demo moment (brand-voice in agent output). Makes the spec viable for the VP meeting. | First block is the gating dependency for the full spec. Without it, Blocks 2 and 3 are empty shells. |
| →1403 BDD invariant test in saa briefs | 3 | Every future bug-fix spawn automatically gets a regression test. Better quality on all future agents. | Cheap process change that applies to every future spawn, not just today's needles. |
| →1411 doc promote routing fix | 3 | Unblocks proper spec routing for all future `ostk doc promote` calls. Clears the root cause behind →1404's quality gaps. | Every new spec added after this fix lands in the right place automatically. |
| →1421 Left-nav consolidation | 2 | Closes the left-nav-consolidation spec. Sets the sidebar layout that pattern-watcher's panel needs to reference. | Self-contained, high visible impact, quick (2-file change, plan already written), but doesn't unlock other needles. |
| →1397 mychat receipts gate | 2 | Makes the "receipts" standing rule a product feature, not just a prompt rule. Every chat session enforces it automatically. | Standing rule becomes durable. Moderate unlock: doesn't open new capabilities, hardens existing ones. |
| Pattern-watcher spec | 2 | Long-term: MEMORY.md replacement, emergent learning. | Currently blocked on `mem.fault_recall` being inactive. Cannot start until ostk-recall is wired and running. Score reflects post-unlock potential, not current actionability. |
| →1395 mychat plan mode toggle | 1 | Adds confirmable banner before tool calls. UX improvement, not a blocker for anything else. | Independent feature, low leverage on other work. |
| →1396 mychat live TodoWrite panel | 1 | Shows model subtask list during turns. UX improvement, independent. | Independent feature. |
| →1398 mychat structured picker | 1 | Replaces "reply A or B" parsing with chips. UX improvement, independent. | Independent feature. |
| →1428 plan-waves + specs | 1 | Adds specs to the plan-waves UI candidate list. Nice-to-have. | Should wait for spec-auto-status to stabilize the status contract first. |

---

## Recommended next-up (ordered)

1. **Merge spec-auto-status worktree (→1422 Phase 1)** — Conflict resolution + highest leverage.
   The worktree `agent-implement-1420-spec-b-d757339c` has already committed Phase 1 (`017bf77`). Do not spawn another agent on →1422. Instead: review the worktree diff, merge to main, close →1422. This immediately unblocks →1423, →1424, →1425, →1426, →1427 in one move. Spawning now on →1423 before this merge is a design conflict (no `claims[]` field in main yet).

2. **→1421 Left-nav consolidation** — Conflict resolution (clears the sidebar layout before pattern-watcher references it) + quick win.
   Plan is written, 2 files to change (`Sidebar.tsx`, `Settings.tsx`). No active conflicts. Prior art exists (commit `d55ed9f` did the same for Transcripts). Closes the left-nav spec. Should be a single focused saa — this is small enough to do without waves.

3. **→1411 doc promote routing fix** — Conflict resolution (clears the root cause for →1404) + compounding.
   Fix where promoted specs land before filing more specs. Running this before →1404's audit means the audit will reflect the fixed state. One routing fix, modest scope.

4. **→1403 BDD invariant test requirement** — Process change, high future leverage.
   Update the saa brief template to require a BDD invariant test (not just a unit test) for bug-fix spawns. Cheap to implement (probably a template edit + CLAUDE.md or MEMORY.md update). Every future bug-fix spawn gets the benefit automatically.

5. **File VP marketing Block 1 as needles and start it** — Compounding, external deadline.
   No needles exist yet for the VP marketing spec. Block 1 (source library, KNOWLEDGE directive, agent grounding) is the foundation for Blocks 2 and 3. With ~9 days of work and an upcoming VP meeting, the clock is ticking. File needles for Block 1, saa Block 1. Blocks 2 and 3 follow after Block 1 commits.

6. **→1422 aftermath: spawn →1423 (Phase 4 UI)** — After the merge in step 1.
   Phase 4 (Building badge in Specs page UI) is the most user-visible part of spec-auto-status. Once Phase 1 is in main, Phase 4 can be built cleanly against the real API contract.

7. **→1404 spec quality gaps audit** — After →1411 lands.
   Audit what's left in the spec contract mismatch once the routing bug is fixed. Some gaps may self-heal.

---

## What NOT to start now (and why)

| Item | Reason |
|------|--------|
| →1270, →1334, →1296 | Blocked on upstream ostk binary update. No code to write. Wait for ostk >6.0.5 to ship. |
| →1424, →1425, →1426, →1427 | All blocked on →1422 Phase 1 merging. Cannot start until merge happens. |
| Pattern-watcher | `mem.fault_recall` is in `services_inactive` at boot. ostk-recall binary not wired. Starting implementation against an inactive substrate means every AC will fail. Wire ostk-recall first (that's its own piece of work). |
| →1428 plan-waves + specs | Low leverage. Status contract is in flux while spec-auto-status lands. Wait until spec-auto-status is fully merged before building a UI that displays spec status. |
| →1407, →1408 | Test infrastructure / flake issues. Important but not blocking product work. Fix in a dedicated test-health pass after the current wave stabilizes. |
| →1423 before →1422 merges | Design conflict: Phase 4 builds on a `claims[]` API field that only exists in the worktree branch, not main. |

---

## Open questions for tori

1. **"Command works sphere" clarification.** My best reading is that Scott meant `ostk work sphere <needle-id>` — the ostk command that shows which needles cluster together. If so: do you want me to run `ostk work sphere` on specific needles (e.g., the spec-auto-status cluster) and include that output in the prioritization? Or did Scott mean something else entirely?

2. **Spec-auto-status worktree review.** The worktree `agent-implement-1420-spec-b-d757339c` has Phase 1 committed but is "locked" in `git worktree list`. Is there an active session still working on it, or is it done and ready to review/merge?

3. **VP marketing meeting timeline.** The spec says ~9 days for all three blocks. Is there a date for the VP meeting? That changes whether Block 1 needs to start this week or can follow the spec-auto-status merge.

4. **Pattern-watcher priority.** This spec is fully designed with all decisions resolved. The only blocker is ostk-recall not being wired. Is wiring ostk-recall something on the roadmap soon, or is pattern-watcher effectively parked until Scott ships something?

5. **→1404 vs →1411 ordering.** I'm recommending →1411 first. But if the spec quality gaps in →1404 are causing active failures today (not just routing inconsistency), it may be worth doing a quick partial →1404 fix even before →1411 lands. Do you know if the spec gaps are breaking anything currently?
