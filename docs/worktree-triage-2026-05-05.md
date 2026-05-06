# Worktree Triage — 2026-05-05

**Date**: 2026-05-05 (triage run 2026-05-06)
**Worktrees inspected**: 52 (all `agent-*` except the triage worktree itself)
**Dead branches inspected**: 3 (`nr-enterprise-rebase-*`)
**Total rows**: 55
**Counts**: KEEP=0 DROP=42 INVESTIGATE=10 (worktrees) + DROP=3 (branches)

> READ-ONLY. No deletions or cherry-picks performed. Action pass is a separate agent.

---

## Classification key

- **clean** — no commits ahead of main, no dirty files
- **dirty-N** — no commits ahead, N dirty (staged/unstaged) files
- **unmerged-N** — N commits ahead of main
- **unmerged-N+dirty-M** — both

---

## Per-Worktree Table

| worktree name | state | latest commit msg (≤80 chars) | rec | evidence |
|---|---|---|---|---|
| agent-955-genai-sdk-migration-finish-088f9f | dirty-1 | diag(hooks): add EXIT trace to all 8 PreToolUse hooks | DROP | Dirty file is `api/services/chat_providers.py` (4-line swap). Main has complete 4-wave google-genai migration (waves 1-4 committed); this partial edit is superseded. |
| agent-962-register-agentsh-curl-timeou-7bf0b3 | unmerged-2 | fix(→962): update all curls in register-agent.sh to --connect-timeout 3 | DROP | Needle →962 appears in main log. Both commits absorbed. |
| agent-971-worktree-stale-hooks-fix-34becb | unmerged-1 | →971 symlink .claude/hooks into worktrees so hook edits on main land | DROP | Needle →971 appears in main log. Work absorbed. |
| agent-972-diagnose-claude-chat-shallow-eb095c | unmerged-2 | →972 fix Claude chat shallow investigation + regression tests | DROP | Main has "fix(chat): 972+973 calibrate Claude in toriOS Chat" and "fix(chat): commit missing 972 calibration block". Work superseded by those commits. |
| agent-973-step-efficiency-cap-behavior-cf23d2 | unmerged-2 | →973 fix step efficiency + cap behavior. Closes →973 | DROP | Main has "fix(chat): 972+973 calibrate Claude in toriOS Chat — investigate before editing, raise step cap to 40". Work superseded. |
| agent-a09eed52 | unmerged-3+dirty-7 | feat(observability): full event coverage | INVESTIGATE | 3 committed observability commits superseded by main ("feat(observability): full event coverage across routers and services"). But dirty diff has 42 new insertions into `app/src/pages/Activity.tsx` not yet committed or on main. |
| agent-a983626a | unmerged-1 | feat: receipts-required protocol for claims of done | DROP | Main has "feat: receipts-required protocol for claims of done" — identical subject. Work absorbed. |
| agent-add-exportwipe-data-button-to-se-55843e | dirty-2 | feat(settings): custom commands UI — add/remove tack verbs from Settings | INVESTIGATE | Dirty diff: 54 insertions across `api/routers/settings.py` + `app/src/pages/Settings.tsx` adding a "Wipe Data" button. Main has export-config in Settings but no wipe-data endpoint or button. Potentially real unshipped feature. |
| agent-agentfile-mcp-declarations-audit-eab141 | dirty-10 | fix(hooks): ostk-first allows native fallback in worktree without local socket | INVESTIGATE | Dirty diff: 130 insertions — adds `MCP:` directive to 8 agent files + updates `agentfile_parser.py` + 101-line test file. Main has 0 agent files with `MCP:` directive. Substantial unshipped work. |
| agent-apply-timing-instrumentation-and-d6ce13 | unmerged-1 | fix(agents): skip transcript I/O for old stopped agents, cuts cold-cache from 17 | DROP | Main has "fix(agents): skip transcript I/O for old stopped agents, cuts cold-cache from 17s to 0.3s" — identical. Absorbed. |
| agent-atlassian-wave-3b-2-way-frontend-b40561 | dirty-4 | feat(atlassian): Phase 3a 2-way actions on Jira issues | DROP | Dirty staged diff includes `JiraCommentComposer.tsx` + `JiraCommentComposer.test.tsx`. These files already exist at `app/src/components/JiraCommentComposer.tsx` on main. Work superseded. |
| agent-deep-diagnose-apiagents-stall-af-1b1096 | dirty-1 | fix(notifications): guard roadmap-ready notification on roadmap existence | INVESTIGATE | Dirty diff: 23 insertions to `api/routers/agents.py`. No matching commit on main. Could be real WIP agents router change worth reviewing. |
| agent-diagnose-adhd-mode-cadence-enfor-34d58b | unmerged-2 | fix(hooks): move cadence injection before backend calls so it fires on every tur | DROP | Main has "fix(hooks): move cadence injection before backend calls so it fires every turn" — identical intent. Absorbed. |
| agent-diagnose-backend-death-after-com-e51c49 | unmerged-1 | fix(watchdog): stop dual-watchdog kill cascade via ownership-check cleanup | DROP | Needle →942 appears in main log. Work absorbed. |
| agent-diagnose-dead-process-detection-892d5e | unmerged-1 | feat(agents): detect stalled subagents (transcript flatlined for 2min) | DROP | Main has "feat(agents): detect stalled subagents (transcript flatlined for 2min)" — identical. Absorbed. |
| agent-diagnose-in-app-chat-skipping-os-1b1206 | unmerged-1 | fix(chat): set subprocess cwd to repo root so ostk hooks and MCP load | DROP | Main has "fix(chat): set subprocess cwd to repo root so ostk hooks and MCP load" — identical. Absorbed. |
| agent-diagnose-monitor-as-read-misuse-9a7178 | unmerged-1 | fix(hooks): block Monitor misuse as file-read substitute | DROP | Main has "fix(hooks): block Monitor misuse as file-read substitute" — identical. Absorbed. |
| agent-diagnose-random-feature-is-live-7a3862 | unmerged-2+dirty-2 | fix(specs): prevent spurious "Your feature is live" modal on repeat fires | DROP | Both commits superseded by main. Dirty diff is 8-line edit to `.claude/hooks/ostk-first.sh` — hook artifact, not real WIP. |
| agent-diagnose-reaper-deleting-worktre-9c7829 | unmerged-2 | fix(reaper): use rev-list to detect unmerged commits instead of git diff | DROP | Main has "fix(reaper): use two-dot rev-list to detect unmerged commits" (80d19cf). Same fix, different wording. Work absorbed. |
| agent-diagnose-stale-your-feature-is-l-7258db | unmerged-1 | fix(release-notes): preserve in-memory dedup on demo reset to prevent re-fires | DROP | Main has "fix(release-notes): preserve in-memory dedup on demo reset to prevent re-fires" — identical. Absorbed. |
| agent-e3-org-settings-home-784248 | unmerged-1 | feat(admin): E3 org settings home | DROP | Main has "feat(admin): E3 org settings home" — identical. Absorbed. |
| agent-e4-team-adoption-rollup-fa3ba9 | unmerged-2 | feat(enterprise): E4 wire TeamAdoption into admin nav + frontend tests | DROP | Main has "feat(enterprise): E4 team adoption rollup with intensity buckets". Same feature, absorbed. |
| agent-e7-admin-customized-starter-pack-cd214b | unmerged-1 | feat(admin): E7 admin-customized org starter pack | DROP | Main has "feat(admin): E7 admin-customized org starter pack" — identical. Absorbed. |
| agent-eager-taco-wave-2-retrofit-denyi-67b419 | unmerged-1 | feat(hooks): wave 2 — retrofit 4 PreToolUse hooks to use lib/deny.sh | DROP | Main has "feat(hooks): wave 2 — retrofit 4 PreToolUse hooks to use lib/deny.sh" — identical. Absorbed. |
| agent-f3-quicklook-full-modal-7c7202 | unmerged-1 | feat(files): F3 QuickLook full preview modal across types | DROP | Main has multiple QuickLook commits including "feat(files): F3+F6 QuickLook preview modal and attachment picker wiring". Work superseded by fuller implementation. |
| agent-finish-970-buildstate-wiring-b86384 | unmerged-1 | →970-finish scaffold: copy stalled worktree files + wire build_state | DROP | Needle →970 appears in main log. Main has "→970 →968 wire build_state into spawn response". Absorbed. |
| agent-fix-gemini-connection-dropped-mi-dea20c | unmerged-1 | fix(gemini): handle mid-stream disconnect gracefully, prevent bare ws close | DROP | Main has "fix(gemini): handle mid-stream disconnect gracefully, prevent bare ws close on API errors" — identical. Absorbed. |
| agent-fix-gray-bg-on-personal-label-in-0da393 | unmerged-1 | fix(ui): theme-aware background on Personal mode label pill | DROP | Main has "fix(ui): theme-aware background on Personal mode label pill" — identical. Absorbed. |
| agent-fix-needle-974-ac91ad | dirty-71 | fix(chat): commit missing 972 calibration block referenced by e5e0988 | DROP | 71 dirty files are all deleted `.claude/hooks/` entries — symlink-artifact state showing hook files absent in this stale worktree. No real uncommitted work. Base commit is on main. |
| agent-fix-needle-975-a112a2 | dirty-71 | fix(chat): commit missing 972 calibration block referenced by e5e0988 | DROP | Same as 974 worktree — 71 dirty files are hook symlink artifacts. Base commit on main. |
| agent-fix-needle-976-007ac1 | dirty-71 | fix(chat): commit missing 972 calibration block referenced by e5e0988 | DROP | Same — 71 dirty files are hook symlink artifacts. Base commit on main. |
| agent-fix-needle-976-retry-ca39da | unmerged-1+dirty-71 | scaffold: →976 retry starting | DROP | Needle →976 on main. 71 dirty files are hook symlink artifacts (same as 974-976 cluster). Scaffold commit only, real work landed elsewhere. |
| agent-fix-silent-error-catches-in-appt-6d9353 | dirty-2 | fix: verb delete tracking, asyncio deprecation, retro suite registration | INVESTIGATE | Dirty diff: 13 insertions + 11 deletions across `app/src/components/OnboardingWizard.tsx` + `app/src/stores/app.ts`. No matching commit on main. May be real UI fix not yet landed. |
| agent-fix-smoke-test-failures-from-thi-96534d | unmerged-3 | fix(specs): make specs journey robust when AI key is absent | DROP | Main has "fix(specs): make specs journey robust when AI key is absent" — identical. Absorbed. |
| agent-fix-tests-a26c8d | unmerged-1 | fix(hooks): source deny.sh from hook's own dir, not CLAUDE_PROJECT_DIR | INVESTIGATE | No matching commit found on main. This hook sourcing fix could be real: deny.sh hooks that fail when CLAUDE_PROJECT_DIR is unset. Worth checking if still needed. |
| agent-fix-tests-acc9e1 | dirty-2 | merge: wave A — 6 builtin template rewrites | DROP | Dirty files: `test-task-isolation-bridge.sh` (modified hook test) + `agents/marketplace/interactive-debug.agent` (untracked). Trivial artifacts — committed wave A work is on main. |
| agent-implement-946 | unmerged-1 | fix(tori): restore visible ostk boot output; fix incomplete →947 reaper impl | DROP | Needle →947 appears in main log. Work absorbed. |
| agent-implement-956 | unmerged-1 | feat: upgrade dev venv from Python 3.9 to 3.11 | INVESTIGATE | No matching Python version upgrade on main (v3.11.x releases are myOS app versions, not Python runtime). Single commit upgrading the dev venv Python requirement. Could be real unmerged work. |
| agent-implement-964 | unmerged-2 | fix(hooks): accept MYOS_SPAWNED_AT override to fix false-positive watcher test | INVESTIGATE | Neither "scaffold-commit-watcher" hook nor "MYOS_SPAWNED_AT" override found on main. Two commits add a new hook enforcing the 2-min subagent commit rule. Real unshipped hook functionality. |
| agent-inbox-page-sidebar-wiring-a19715 | unmerged-1 | feat(inbox): top-level Inbox page + sidebar badge | DROP | Main has "feat(inbox): wire Inbox page into sidebar and routing". Same feature, absorbed. |
| agent-integration-tests-for-windows-co-20c1ac | dirty-5 | merge(windows): →864 + →865 Windows runtime compatibility fixes | DROP | Main has "test(windows): integration tests for →864 Windows compat features". Dirty diff (190 insertions) is uncommitted test additions that are superseded by main's integration test commit. |
| agent-merge-sequence-to-main-push-for-7807f2 | dirty-1 | chore(spawn): remove per-spawn USD budget cap on subscription auth | DROP | Main has "chore(spawn): remove per-spawn USD budget cap on subscription auth" — identical. Dirty file is 1-line deletion in `register-agent.sh` — trivial hook artifact. |
| agent-migrate-googlegenerativeai-goog-7d232a | dirty-6 | feat(hooks): inject time-since-last-reply tag in standing rules | DROP | Dirty diff: 6 files (requirements.txt, gemini.py, chat_providers.py, gemini_drafter.py, 2 test files). Main has complete 4-wave google-genai migration. This partial uncommitted migration is superseded. |
| agent-needle-956-python-311-venv-upgra-2af5f4 | unmerged-3 | →956 upgrade Python requirement from 3.9 to 3.11+ | DROP | Needle →956 appears in main log. All 3 commits absorbed. |
| agent-needle-970-concurrent-comprehens-04751b | unmerged-1+dirty-5 | →970 scaffold: build_queue + comprehensive_build services | INVESTIGATE | Base commit (→970) is on main. But dirty diff has 124 insertions across `api/routers/agents.py`, `api/services/build_queue.py`, `app/src/pages/Tasks.tsx` — could be follow-up concurrent-build work not yet committed or merged. |
| agent-p2-my-ai-setup-page-f8f175 | unmerged-1 | feat(personal): P2 my AI setup page with shareable summary | DROP | Main has "feat(personal): P2 my AI setup page with shareable summary" — identical. Absorbed. |
| agent-p6-undo-agent-action-1ba14f | unmerged-1 | feat(agents): P6 plain-language undo for agent commits | DROP | Main has "feat(agents): P6 plain-language undo for agent commits" — identical. Absorbed. |
| agent-rename-plansspecs-in-ui-copy-829b70 | dirty-7 | feat(agents): pause-and-chat upgrade for inline agent nudge replies (→857) | DROP | All 7 staged dirty files rename "Specs" to "Plans" in UI components. Main already has "refactor(nav): rename 'Specs' to 'Plans' in sidebar". Work superseded. |
| agent-saa-calendar-aware-tasks-2-9d1919 | unmerged-1 | feat(calendar): add calendar-aware task prioritization service, router, and comp | DROP | Main has "feat(calendar): add calendar-aware task prioritization service, router, and component" — identical. Absorbed. |
| agent-spawn-isolation-wire | dirty-10 | feat: worktree pre-merge gate | INVESTIGATE | No commits ahead of main. Dirty diff: new files `api/services/spawn_isolation.py`, `api/tests/test_spawn_isolation.py`, `api/tests/test_spawn_worktree_fork.py` + modified `api/models/schemas.py`, `api/routers/agents.py` + 5 untracked files. Substantial new spawn-isolation service not on main. Branch name is non-standard (`spawn-isolation-wire`, not `worktree-agent-*`). |
| agent-wave-1-hook-fail-open-fixes-7a27be | unmerged-1 | fix(hooks): wave 1, fail-open against missing infra | DROP | Main has "fix(hooks): wave 1, fail-open hooks against missing infra" — identical. Absorbed. |
| agent-write-e2eintegration-tests-for-w-b515dd | unmerged-1 | test(windows): integration tests for →864 Windows compat features | DROP | Needle →864 on main. Main also has "test(windows): integration tests for →864 Windows compat features" — identical subject. Absorbed. |

---

## Per-Branch Table (3 leftover `nr-enterprise-rebase-*` branches)

| branch name | state | latest commit msg | rec | evidence |
|---|---|---|---|---|
| nr-enterprise-rebase-try | unmerged-1 | feat: NR enterprise overlay rebased on current main | DROP | Name ("rebase-try") signals an experimental attempt. Commit is a full-overlay rebase snapshot. Superseded by v3 and v3-inline attempts, none of which are the canonical `nr-enterprise` branch. |
| nr-enterprise-rebase-v3 | unmerged-1 | feat: NR enterprise overlay rebased on current main (includes v3.10.0) | DROP | One of three redundant rebase-attempt branches. Not the live `nr-enterprise` branch. No unique content vs. v3-inline — both include v3.10.0 overlay on the same base message. |
| nr-enterprise-rebase-v3-inline | unmerged-1 | feat: NR enterprise overlay rebased on current main (includes v3.10.0) | DROP | Identical commit message to v3. Likely a second rebase attempt inlining the overlay differently. No unique content recoverable from either v3 branch beyond what `nr-enterprise` already carries. |

---

## Summary counts

| recommendation | worktrees | branches | total |
|---|---|---|---|
| DROP | 42 | 3 | 45 |
| INVESTIGATE | 10 | 0 | 10 |
| KEEP | 0 | 0 | 0 |
| **Total** | **52** | **3** | **55** |

> Note: 52 worktrees are triaged here (53 total agent-* minus the triage worktree itself). The task brief cited 51 — the discrepancy is `agent-spawn-isolation-wire` which lacks the `worktree-agent-*` branch prefix and may not have been counted by the worktree-reaper script.

---

## Action list for the action agent

### DROP (45 items) — safe to delete

#### Worktrees to remove (branches absorbed by main)

These all have their work on main (needle match, subject match, or confirmed cherry-pick). Safe to `git worktree remove --force` and `git branch -D worktree-agent-<name>`.

1. agent-955-genai-sdk-migration-finish-088f9f
2. agent-962-register-agentsh-curl-timeou-7bf0b3
3. agent-971-worktree-stale-hooks-fix-34becb
4. agent-972-diagnose-claude-chat-shallow-eb095c
5. agent-973-step-efficiency-cap-behavior-cf23d2
6. agent-a983626a
7. agent-apply-timing-instrumentation-and-d6ce13
8. agent-atlassian-wave-3b-2-way-frontend-b40561
9. agent-diagnose-adhd-mode-cadence-enfor-34d58b
10. agent-diagnose-backend-death-after-com-e51c49
11. agent-diagnose-dead-process-detection-892d5e
12. agent-diagnose-in-app-chat-skipping-os-1b1206
13. agent-diagnose-monitor-as-read-misuse-9a7178
14. agent-diagnose-random-feature-is-live-7a3862
15. agent-diagnose-reaper-deleting-worktre-9c7829
16. agent-diagnose-stale-your-feature-is-l-7258db
17. agent-e3-org-settings-home-784248
18. agent-e4-team-adoption-rollup-fa3ba9
19. agent-e7-admin-customized-starter-pack-cd214b
20. agent-eager-taco-wave-2-retrofit-denyi-67b419
21. agent-f3-quicklook-full-modal-7c7202
22. agent-finish-970-buildstate-wiring-b86384
23. agent-fix-gemini-connection-dropped-mi-dea20c
24. agent-fix-gray-bg-on-personal-label-in-0da393
25. agent-fix-needle-974-ac91ad
26. agent-fix-needle-975-a112a2
27. agent-fix-needle-976-007ac1
28. agent-fix-needle-976-retry-ca39da
29. agent-fix-smoke-test-failures-from-thi-96534d
30. agent-fix-tests-acc9e1
31. agent-implement-946
32. agent-inbox-page-sidebar-wiring-a19715
33. agent-integration-tests-for-windows-co-20c1ac
34. agent-merge-sequence-to-main-push-for-7807f2
35. agent-migrate-googlegenerativeai-goog-7d232a
36. agent-needle-956-python-311-venv-upgra-2af5f4
37. agent-p2-my-ai-setup-page-f8f175
38. agent-p6-undo-agent-action-1ba14f
39. agent-rename-plansspecs-in-ui-copy-829b70
40. agent-saa-calendar-aware-tasks-2-9d1919
41. agent-wave-1-hook-fail-open-fixes-7a27be
42. agent-write-e2eintegration-tests-for-w-b515dd

#### Branches to delete (`git branch -D <name>`)

43. nr-enterprise-rebase-try
44. nr-enterprise-rebase-v3
45. nr-enterprise-rebase-v3-inline

---

### INVESTIGATE (10 items) — owner review required before action

For each: owner should run `git -C .claude/worktrees/<name> diff` and `git -C .claude/worktrees/<name> diff --cached` to review the uncommitted work, then decide: cherry-pick to main, open a needle, or drop.

1. **agent-a09eed52** — dirty Activity.tsx (42-line additions) not on main; committed observability work is absorbed
2. **agent-add-exportwipe-data-button-to-se-55843e** — wipe-data button in Settings (54 insertions) not on main
3. **agent-agentfile-mcp-declarations-audit-eab141** — MCP directives for 8 agentfiles + parser update (130 insertions) not on main
4. **agent-deep-diagnose-apiagents-stall-af-1b1096** — 23 insertions to agents.py router not on main
5. **agent-fix-silent-error-catches-in-appt-6d9353** — OnboardingWizard.tsx + app.ts changes (13 ins / 11 del) not on main
6. **agent-fix-tests-a26c8d** — deny.sh sourcing fix (1 commit) not on main
7. **agent-implement-956** — Python 3.9→3.11 dev-venv upgrade (1 commit) not on main
8. **agent-implement-964** — scaffold-commit-watcher hook (2 commits) not on main
9. **agent-needle-970-concurrent-comprehens-04751b** — dirty build_queue + Tasks.tsx (124 insertions) not committed; base →970 work is on main
10. **agent-spawn-isolation-wire** — new spawn-isolation service (new files, 10 dirty) not on main; non-standard branch format
