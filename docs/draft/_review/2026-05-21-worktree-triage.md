# Worktree branch triage — 2026-05-21

Total branches scanned: 251
Total classified: 251

## Summary by class

- **redundant**: 12 branches — content already in main via a different commit SHA (safe to delete)
- **partial-overlap**: 10 branches — some commits landed in main, but at least one did not (needs human review)
- **unique-work**: 179 branches — no commits match main content (real abandoned or in-flight work)
- **scaffold-only**: 7 branches — only scaffold/WIP commits, no real work (safe to delete)
- **absorbed**: 43 branches — zero unique commits vs main (already fully merged)

**19 branches can be deleted now** (redundant + scaffold-only).
**189 branches need a look** (partial-overlap + unique-work).
**43 branches are already fully absorbed** into main.

## Redundant (safe to delete)

All unique commits on these branches have content-equivalent commits in main. The work landed; the branch is a duplicate.

| Branch | Needle | Age (days) | Lines +/- | Notes |
|--------|--------|-----------|-----------|-------|
| `worktree-agent-phase-2-agents-py-pub-1052d23f` | →1052 | 4 | +377/-0 | →phase-2 GREEN: agents.py publishes to Time on register/heartbeat/comp |
| `worktree-agent-plan-1433-team-mode-27ee10` | →1433 | 3 | +372/-0 | →1433 plan: team mode — multi-user, shared workspace, roles, billing,  |
| `worktree-agent-fix-1403-saa-bdd-inva-a3a88418` | →1403 | 3 | +368/-0 | feat(saa_brief): add generate_saa_brief with BDD invariant for bug-fix |
| `worktree-agent-dashboard-widget-clea-9a0192eb` | →0192 | 4 | +145/-208 | →1415 finish: remove ostk_files widget, move adventure X + day summary |
| `worktree-agent-audit-1435-vite-wedge-8fe22f` | →1435 | 3 | +221/-0 | →1435 audit: document vite-wedge regression pattern across 5 agents |
| `worktree-agent-implement-1416-sync-s-3e4cef20` | →1416 | 4 | +111/-53 | fix(api): offload sync subprocess.run calls in upgrade_check + provide |
| `worktree-agent-fix-1430-spec-counts-97fc99ef` | →1430 | 3 | +37/-19 | fix(tests): isolate test_spec_counts_excludes_plan_transcripts via lis |
| `worktree-agent-diagnose-markdown-not-bdb234cc` | →234 | 4 | +50/-4 | fix(chat): render markdown in pre-tool text when tool call is pending |
| `worktree-agent-1379-api-agents-speed-47ae5f` | →1379 | 6 | +20/-15 | fix →1379 remove kernel_ps from /api/agents hot path |
| `worktree-agent-fix-1407-mailbox-flake-30fa42` | →1407 | 3 | +4/-20 | fix(tests): remove importlib.reload that caused mailbox flake →1407 |
| `worktree-agent-diagnose-team-page-en-bc7f2734` | →2734 | 6 | +15/-7 | fix(→1356): unroute /team pages and add regression test |
| `worktree-agent-revert-people-message-b74ab54f` | - | 6 | +3/-3 | rename sidebar People back to Messages |

## Partial overlap (review)

Some commits are already in main; others are not. The unmatched commits may contain extra tests, alternate implementations, or in-progress work.

| Branch | Needle | Lines +/- | Matched | Unmatched | First unmatched commit |
|--------|--------|-----------|---------|-----------|------------------------|
| `worktree-agent-1531-b2-template-cont-24c40a7e` | →1531 | +535/-7 | 2 | 1 | feat(specs): B2 template implementations (→1531) — 5 .agent  |
| `worktree-agent-theme-b-mvp-1438-retry-81ab70` | →1438 | +515/-0 | 1 | 1 | →1438 scaffold |
| `worktree-agent-fix-completion-watche-149bed32` | →149 | +459/-48 | 1 | 6 | →1449 scaffold: completion-watcher singleton fix |
| `worktree-agent-saa-messages-combine-c7103f9c` | →7103 | +192/-163 | 1 | 1 | scaffold(→1582): messages combine search and send fields pla |
| `worktree-agent-phase-3-chat-tool-cal-bd23e24f` | - | +346/-1 | 1 | 1 | →phase-3 GREEN: chat tool calls publish start/finish to Time |
| `worktree-agent-1345-e2e-settings-pol-da529a69` | →1345 | +185/-5 | 1 | 1 | chore(→1345): scaffold e2e settings isolation |
| `worktree-agent-saa-torichat-all-mode-f8e52bd7` | - | +182/-5 | 1 | 1 | scaffold(→1579): torichat all-mode reply plan |
| `worktree-agent-saa-tasks-intermitten-b36e6226` | →6226 | +91/-2 | 1 | 1 | scaffold(→1581): tasks intermittent error plan |
| `worktree-agent-diagnose-fix-1431-vit-94399d33` | →1431 | +33/-13 | 1 | 1 | →1431 scaffold |
| `worktree-agent-1338-http-https-probe-22f8c807` | →1338 | +17/-9 | 1 | 1 | chore(→1338): scaffold probe-script https migration |

## Unique work (review) — showing top 30 of 179 by lines changed

None of these commits match any content in main. Either the work was abandoned mid-stream or it was never merged. Check the needle state before deleting.

| Branch | Needle | Age (days) | Lines +/- | Commits | Top commit |
|--------|--------|-----------|-----------|---------|------------|
| `worktree-agent-audit-frontend-pollin-a6c5d52e` | - | 8 | +1164/-337 | 3 | test(→1286): regression tests for AbortController/backoff on all  |
| `worktree-agent-rebuild-gemini-frontend-1165-0650cd` | →1165 | 9 | +1043/-0 | 1 | feat(gems): scaffold MyGems page + GemImportModal (→1165) |
| `worktree-agent-gemini-phase-a-frontend-1162-d24194` | →1162 | 9 | +1040/-0 | 2 | fix(gems): green vitest for MyGems + GemImportModal (→1162) |
| `worktree-agent-build-1165-gems-ui-686c28` | →1165 | 9 | +1032/-0 | 1 | feat(→1165): My Gems page + GemImportModal (Phase A frontend) |
| `worktree-agent-phase-5-primitives-re-0d4c9799` | →9799 | 4 | +1023/-0 | 2 | →primitives-registry GREEN: registry + router + linter + 5 contra |
| `worktree-agent-wave-3a-agent-1132-topbar-notifi-3d79d3` | →1132 | 10 | +544/-384 | 1 | feat(notifications): real-time notifications via WebSocket (→1132 |
| `worktree-agent-build-1133-topbar-ws` | →1133 | 9 | +428/-476 | 1 | feat(→1133): replace TopBar notifications poll with WS feed |
| `worktree-agent-ostk-vfs-mount-files-0b8c4adf` | - | 7 | +874/-0 | 2 | feat(→1314): Files page surfaces .ostk/ decisions, needles, audit |
| `worktree-agent-phase-b-extension-gem-67ae2cd8` | - | 8 | +874/-0 | 4 | feat(gems): show captured gemini.google.com conversations on MyGe |
| `worktree-agent-render-markdown-in-ge-c9f219d2` | →219 | 8 | +812/-6 | 1 | fix(→1254): render markdown in gem chat assistant messages |
| `worktree-agent-build-1458-ai-backend-15ca4dea` | →1458 | 3 | +682/-133 | 5 | feat(→1458): UI relabel + test updates for AI backend preference |
| `worktree-agent-fix-gemini-api-reachable-lie-b885b3` | →885 | 11 | +673/-28 | 6 | fix(gemini): surface real api_reachable + permission errors in st |
| `worktree-agent-1538-user-memory-f2-f-b95b0ee6` | →1538 | 1 | +635/-62 | 2 | feat(memory): forget trigger + undo toast (→1538) |
| `worktree-agent-1219-backend-snapshot-beb78562` | →1219 | 8 | +343/-341 | 2 | feat(→1219): cache /api/agents snapshot every 500ms + fire WS del |
| `worktree-agent-fix-1348-watcher-main-c3dd24f3` | →1348 | 7 | +639/-16 | 2 | fix(→1348): Bug A restore transcript_bytes gate in scaffold_commi |
| `worktree-agent-add-current-model-to-write-kerne-28562d` | →28562 | 9 | +628/-0 | 1 | fix(policy): allow MCP writes to .ostk/current_model under write- |
| `worktree-agent-usage-panel-1299-bb51f2` | →1299 | 7 | +619/-1 | 2 | feat(→1299): surface Claude Code /usage and Gemini equivalent in  |
| `worktree-agent-imessage-contacts-1310-c4e892` | →1310 | 7 | +615/-0 | 2 | feat(→1310): retrieve iMessage contacts from macOS AddressBook |
| `worktree-agent-settingstsx-coherence-targeted-aeb642` | →642 | 12 | +249/-296 | 1 | fix(settings): coherence overhaul — unified toggles, surface noti |
| `worktree-agent-diagnose-needle-count-5fbce347` | →347 | 8 | +530/-3 | 1 | fix(→1253): reconcile ostk needle store with /api/tasks store acr |
| `worktree-agent-remove-hooks-spec-fro-c07963ed` | →07963 | 3 | +234/-299 | 7 | policy: move hooks docs to user scope, add memory rule (→1450) |
| `worktree-agent-deprecate-onboarding-05e2caa7` | - | 3 | +2/-525 | 1 | chore(onboarding): remove deprecated /onboarding/dream endpoint,  |
| `worktree-agent-tier-2-3-ostk-hook-opt-in-instal-f6de82` | - | 9 | +521/-0 | 1 | feat(hooks): add opt-in installer for ostk lifecycle hooks (Tier  |
| `worktree-agent-swap-topbar-to-ws-feed-706762` | →70676 | 9 | +141/-371 | 1 | feat(→1133): replace TopBar notifications poll with WS feed |
| `worktree-agent-1176-tier-1-1b-delete-reaper-b0ab38` | →1176 | 9 | +0/-499 | 1 | feat(cleanup): delete homegrown reaper in favour of kernel-fleet  |
| `worktree-agent-diagnose-1346-prematu-07317924` | →1346 | 7 | +479/-1 | 2 | fix(→1346): block premature needle-close when scaffold-only + dir |
| `worktree-agent-ws-feed-for-sidebar-sessions-112-46b653` | →112 | 10 | +455/-25 | 1 | feat(sidebar): show sessions in real time via WS (→1128) |
| `worktree-agent-1344-premature-close-6fc64692` | →1344 | 7 | +464/-4 | 2 | fix(→1344): fix scaffold_commit_watcher — SPAWNED_AT from API, co |
| `worktree-agent-wave-3a-agent-1131-dashboard-dat-85468f` | →1131 | 10 | +464/-2 | 1 | feat(dashboard): real-time data feed via WebSocket (→1131) |
| `worktree-agent-diagnose-ssl-handshak-97aef94a` | - | 1 | +440/-6 | 1 | fix(api): sync file I/O in 500ms snapshot loop blocks asyncio eve |

*... and 149 more unique-work branches. Full list in `/tmp/triage-results.tsv`.*

## Scaffold-only (safe to delete)

These branches contain only scaffold or WIP commits — no real implementation landed.

| Branch | Needle | Age (days) | Notes |
|--------|--------|-----------|-------|
| `worktree-agent-fs-ops-worktree-leak-bdfff617` | →617 | 8 | scaffold/WIP only |
| `worktree-agent-p99-latency-test-for-74468c9b` | →74468 | 8 | scaffold/WIP only |
| `worktree-agent-py-spy-stack-dump-cap-bfa13000` | →13000 | 8 | scaffold/WIP only |
| `worktree-agent-diagnose-1459-ostk-ca-3dca25ac` | →1459 | 3 | scaffold/WIP only |
| `worktree-agent-fix-1459-splash-hook-dc2b4d75` | →1459 | 3 | scaffold/WIP only |
| `worktree-agent-diagnose-pytest-pollu-47d80bb1` | - | 0 | scaffold/WIP only |
| `worktree-agent-worktree-triage-242-b-ad346cd2` | →242 | 0 | scaffold/WIP only |

## Already absorbed (no action needed)

These 43 branches have zero unique commits vs main. The worktree-reaper should already have flagged these; they can be cleaned up in a normal reaper run.

## Recommendation

- **19 branches can be deleted now with no loss** (12 redundant + 7 scaffold-only).
- **10 branches need a closer look** (partial-overlap — unmatched commits may be tests or alternate code).
- **179 branches have work not in main** — most are abandoned agent runs; review before deleting.
- **43 branches are already absorbed** — safe to clean up with `scripts/worktree-reaper.sh --apply`.

### Top 5 unique-work branches to investigate first (most lines changed)

1. `worktree-agent-audit-frontend-pollin-a6c5d52e` — -, +1164/-337 lines, 8 days old
   > test(→1286): regression tests for AbortController/backoff on all 6 fixed hooks
1. `worktree-agent-rebuild-gemini-frontend-1165-0650cd` — →1165, +1043/-0 lines, 9 days old
   > feat(gems): scaffold MyGems page + GemImportModal (→1165)
1. `worktree-agent-gemini-phase-a-frontend-1162-d24194` — →1162, +1040/-0 lines, 9 days old
   > fix(gems): green vitest for MyGems + GemImportModal (→1162)
1. `worktree-agent-build-1165-gems-ui-686c28` — →1165, +1032/-0 lines, 9 days old
   > feat(→1165): My Gems page + GemImportModal (Phase A frontend)
1. `worktree-agent-phase-5-primitives-re-0d4c9799` — →9799, +1023/-0 lines, 4 days old
   > →primitives-registry GREEN: registry + router + linter + 5 contract docs (20/20 

### Top 3 partial-overlap branches to check

1. `worktree-agent-1531-b2-template-cont-24c40a7e` — →1531, 1 unmatched commit(s)
   > feat(specs): B2 template implementations (→1531) — 5 .agent templates, wizard pr
1. `worktree-agent-theme-b-mvp-1438-retry-81ab70` — →1438, 1 unmatched commit(s)
   > →1438 scaffold
1. `worktree-agent-fix-completion-watche-149bed32` — →149, 6 unmatched commit(s)
   > →1449 scaffold: completion-watcher singleton fix

---
*Generated by worktree-triage agent on 2026-05-21. Source data: `/tmp/triage-results.tsv`.*
