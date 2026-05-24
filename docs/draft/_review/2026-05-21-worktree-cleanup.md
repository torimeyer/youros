# Worktree Cleanup Pass 1 — 2026-05-21

**Agent**: worktree-cleanup-pass-1-536094  
**Source of truth**: `/tmp/triage-results.tsv` + triage agent report  
**Scope**: absorbed, scaffold-only, redundant DELETED. unique-work CLASSIFIED (read-only).

---

## Summary

| action | count |
|--------|-------|
| branches deleted (safe classes) | 19 |
| absorbed — already missing pre-run | 42 |
| absorbed — reclassified (has unique commit) | 1 |
| partial-overlap — deferred to pass 2 | 10 |
| unique-work classified | 179 |
| **safety tags created** | **61** |

### Deleted branches by class

| class | deleted | method |
|-------|---------|--------|
| scaffold-only | 7 | rm -rf wt-dir + git worktree prune |
| redundant | 12 | 1 via refs/heads, 11 via worktree prune |
| absorbed | 0 | all 42 were already missing before this run |
| **total** | **19** | — |

Safety tags recoverable for 90 days under `refs/tags/archive/cleanup-2026-05-21/<branch>`.

---

## Deleted branch list

### scaffold-only (7 deleted)

| branch | age |
|--------|-----|
| `worktree-agent-diagnose-1459-ostk-ca-3dca25ac` | 4d |
| `worktree-agent-diagnose-pytest-pollu-47d80bb1` | 3d |
| `worktree-agent-fix-1459-splash-hook-dc2b4d75` | 4d |
| `worktree-agent-fs-ops-worktree-leak-bdfff617` | 3d |
| `worktree-agent-p99-latency-test-for-74468c9b` | 5d |
| `worktree-agent-py-spy-stack-dump-cap-bfa13000` | 5d |
| `worktree-agent-worktree-triage-242-b-ad346cd2` | 0d |

### redundant (12 deleted)

| branch | age |
|--------|-----|
| `worktree-agent-1379-api-agents-speed-47ae5f` | 8d |
| `worktree-agent-audit-1435-vite-wedge-8fe22f` | 4d |
| `worktree-agent-dashboard-widget-clea-9a0192eb` | 4d |
| `worktree-agent-diagnose-markdown-not-bdb234cc` | 8d |
| `worktree-agent-diagnose-team-page-en-bc7f2734` | 5d |
| `worktree-agent-fix-1403-saa-bdd-inva-a3a88418` | 6d |
| `worktree-agent-fix-1407-mailbox-flake-30fa42` | 6d |
| `worktree-agent-fix-1430-spec-counts-97fc99ef` | 4d |
| `worktree-agent-implement-1416-sync-s-3e4cef20` | 6d |
| `worktree-agent-phase-2-agents-py-pub-1052d23f` | 7d |
| `worktree-agent-plan-1433-team-mode-27ee10` | 4d |
| `worktree-agent-revert-people-message-b74ab54f` | 7d |

---

## Absorbed branches — pre-cleaned (42 already missing)

All 42 absorbed branches from the triage report were absent from `git for-each-ref refs/heads/` at run time. They were cleaned up before pass 1, likely by prior manual pruning or `git worktree prune` runs. The one absorbed branch that remained (`worktree-agent-diagnose-monitor-inte-9e063cd0`) has 1 unique commit and was reclassified — not deleted.

---

## Unique-work classification (179 branches, read-only)

No unique-work branches were deleted. Classification only.

### open-needle-keep (0)

None. The 5 currently open needles (→1465, →1525, →1541, →1563, →1568) do not have corresponding worktrees in the triage dataset. Their work is either in the current main branch or in active worktrees not yet surfaced by the reaper.

### closed-needle-stale (92)

Branches whose triage-extracted needle ID matches a closed needle. These are candidates for pass 2 deletion after manual spot-check.

> **Note**: Some entries with short IDs (e.g., →067, →096, →164, →219) may be hash fragments extracted from branch name suffixes, not real needle references. Confirm before deleting.

| branch | needle | age | unique_commits |
|--------|--------|-----|----------------|
| `worktree-agent-rag-fix-agent-gems-in-067f3705` | →067 | 8d | 1 |
| `worktree-agent-diagnose-dual-state-p-bf096bd7` | →096 | 9d | 1 |
| `worktree-agent-fix-subagent-ostk-mcp-propagatio-7ba164` | →164 | 11d | 1 |
| `worktree-agent-render-markdown-in-ge-c9f219d2` | →219 | 8d | 1 |
| `worktree-agent-diagnose-needle-count-5fbce347` | →347 | 8d | 1 |
| `worktree-agent-a368de4f243686d9f` | →368 | 8d | 3 |
| `worktree-agent-fix-subagent-ostk-mcp-not-loaded-411a0e` | →411 | 9d | 1 |
| `worktree-agent-diagnose-status-stuck-running-bu-580af8` | →580 | 9d | 1 |
| `worktree-agent-fix-threaded-reply-sc-7e584db5` | →584 | 8d | 1 |
| `worktree-agent-diagnose-connection-dropped-inli-f615eb` | →615 | 9d | 1 |
| `worktree-agent-settingstsx-coherence-targeted-aeb642` | →642 | 12d | 1 |
| `worktree-agent-mygems-subtitle-paddi-665fdbee` | →665 | 8d | 1 |
| `worktree-agent-swap-chatpanel-to-ws-0a9db691` | →691 | 9d | 1 |
| `worktree-agent-fix-fleet-active-gate-counts-sta-0701fa` | →0701 | 9d | 1 |
| `worktree-agent-diagnose-fix-api-agents-wedge-11-c0b856` | →856 | 9d | 2 |
| `worktree-agent-tier-1-1a-retry-kernel-jsonl-fle-d870ca` | →870 | 9d | 1 |
| `worktree-agent-triage-remaining-work-90c880a7` | →880 | 7d | 1 |
| `worktree-agent-fix-gemini-api-reachable-lie-b885b3` | →885 | 11d | 6 |
| `worktree-agent-rag-for-gem-knowledge-899eb669` | →899 | 8d | 2 |
| `worktree-agent-surface-chat-error-ro-b907d5cd` | →907 | 9d | 1 |
| `worktree-agent-diagnose-close-task-b-5d6ca994` | →994 | 9d | 1 |
| `worktree-agent-expose-mcp-tools-in-worktree-ses-27e998` | →998 | 9d | 1 |
| `worktree-agent-verify-gemini-revert-998b4397` | →998 | 0d | 1 |
| `worktree-agent-chat-right-click-needle-1056-7a9389` | →1056 | 9d | 1 |
| `worktree-agent-close-1100-nr-audit-324006` | →1100 | 11d | 1 |
| `worktree-agent-real-time-in-progress-badge-1118-f73956` | →1118 | 11d | 1 |
| `worktree-agent-build-1120-plans-in-recent-docs-50fdbb` | →1120 | 11d | 1 |
| `worktree-agent-streaming-chat-newlines-1121-547eb8` | →1121 | 11d | 1 |
| `worktree-agent-sidebar-task-counts-ws-1124-7e2f02` | →1124 | 11d | 1 |
| `worktree-agent-sidebar-spec-counts-ws-1125-e3e829` | →1125 | 11d | 1 |
| `worktree-agent-wave-3a-agent-1131-dashboard-dat-85468f` | →1131 | 10d | 1 |
| `worktree-agent-wave-3a-agent-1132-topbar-notifi-3d79d3` | →1132 | 10d | 1 |
| `worktree-agent-build-1133-topbar-ws` | →1133 | 9d | 1 |
| `worktree-agent-build-1134-chat-panel-21b1e90b` | →1134 | 9d | 1 |
| `worktree-agent-close-orphan-spec-drafts-1147-d287d8` | →1147 | 9d | 1 |
| `worktree-agent-subagent-ostk-mcp-retry-1148-ee81bc` | →1148 | 9d | 2 |
| `worktree-agent-retry-1153-fleet-active-gate-3e23c3` | →1153 | 9d | 1 |
| `worktree-agent-fix-tasks-test-failures-1160-5c7f60` | →1160 | 9d | 1 |
| `worktree-agent-respawn-tasks-test-fix-1160-b78575` | →1160 | 9d | 1 |
| `worktree-agent-gemini-phase-a-backend-1161-fc5984` | →1161 | 9d | 1 |
| `worktree-agent-gemini-phase-a-frontend-1162-d24194` | →1162 | 9d | 2 |
| `worktree-agent-verify-1163-mcp-drop-4b30d6` | →1163 | 9d | 1 |
| `worktree-agent-build-1165-gems-ui-686c28` | →1165 | 9d | 1 |
| `worktree-agent-rebuild-gemini-frontend-1165-0650cd` | →1165 | 9d | 1 |
| `worktree-agent-diagnose-1167-spawn-test-flake-f97436` | →1167 | 9d | 1 |
| `worktree-agent-1176-tier-1-1b-delete-reaper-b0ab38` | →1176 | 9d | 1 |
| `worktree-agent-diagnose-backend-api-wedge-1178-c5ad0c` | →1178 | 9d | 3 |
| `worktree-agent-guided-tour-opt-in-1183-1184-bfc051` | →1183 | 9d | 2 |
| `worktree-agent-fix-google-workspace-oauth-1186-9a0dea` | →1186 | 9d | 1 |
| `worktree-agent-cherry-pick-1188-onto-main-2650e1` | →1188 | 9d | 1 |
| `worktree-agent-fix-1188-export-label-afe578` | →1188 | 9d | 1 |
| `worktree-agent-fix-1190-dl-icon-align-3681a2` | →1190 | 9d | 1 |
| `worktree-agent-clamp-1191-wave-text-395406` | →1191 | 9d | 1 |
| `worktree-agent-fix-1191-plan-waves-wall-of-text-3440a9` | →1191 | 9d | 1 |
| `worktree-agent-diagnose-1192-backend-wedge-take-d52b56` | →1192 | 9d | 1 |
| `worktree-agent-diagnose-backend-wedge-1192-9ba9c3` | →1192 | 9d | 2 |
| `worktree-agent-1202-slow-call-asgi-m-8c326ac5` | →1202 | 9d | 1 |
| `worktree-agent-1214-gate-self-filter-b5d38d` | →1214 | 8d | 1 |
| `worktree-agent-1215-mcp-env-parser-9a7724` | →1215 | 8d | 1 |
| `worktree-agent-1216-locate-v6-0-0-so-4eb8e94b` | →1216 | 8d | 1 |
| `worktree-agent-1217-mcp-transport-re-1a5708d7` | →1217 | 8d | 2 |
| `worktree-agent-1219-backend-snapshot-beb78562` | →1219 | 8d | 2 |
| `worktree-agent-1221-diagnose-session-22a3af94` | →1221 | 8d | 1 |
| `worktree-agent-fix-1242-gem-test-leak-900563` | →1242 | 8d | 1 |
| `worktree-agent-usage-panel-1299-bb51f2` | →1299 | 7d | 2 |
| `worktree-agent-diagnose-1301-silent-d2bbfd65` | →1301 | 8d | 2 |
| `worktree-agent-imessage-contacts-1310-c4e892` | →1310 | 7d | 2 |
| `worktree-agent-diagnose-cwd-leak-1311-67965d` | →1311 | 7d | 2 |
| `worktree-agent-ostk-bail-pack-1313-d3f400` | →1313 | 7d | 2 |
| `worktree-agent-fix-test-isolation-1323-d17d36` | →1323 | 7d | 1 |
| `worktree-agent-1335-retro-fix-all-is-f2d2a332` | →1335 | 7d | 2 |
| `worktree-agent-1342-orphan-finished-ac95f0d7` | →1342 | 7d | 1 |
| `worktree-agent-1343-pre-tool-guard-f-3d418382` | →1343 | 7d | 1 |
| `worktree-agent-1344-premature-close-6fc64692` | →1344 | 7d | 2 |
| `worktree-agent-diagnose-1346-prematu-07317924` | →1346 | 7d | 2 |
| `worktree-agent-1347-wave-4-refresh-r-b49a7ba2` | →1347 | 7d | 2 |
| `worktree-agent-1347-wave-5-atlassian-0b10421a` | →1347 | 7d | 2 |
| `worktree-agent-fix-1348-watcher-main-c3dd24f3` | →1348 | 7d | 2 |
| `worktree-agent-ostk-handoff-wrapper-01358f5f` | →01358 | 7d | 2 |
| `worktree-agent-fix-1408-monitor-regi-43582ce0` | →1408 | 3d | 1 |
| `worktree-agent-fix-1422-test-regress-2aea0da2` | →1422 | 4d | 2 |
| `worktree-agent-diagnose-fix-1429-31ce51` | →1429 | 4d | 1 |
| `worktree-agent-fix-1429-broken-button-bbc3d3` | →1429 | 3d | 1 |
| `worktree-agent-build-1458-ai-backend-15ca4dea` | →1458 | 3d | 5 |
| `worktree-agent-build-1472-upstream-o-7edf69c6` | →1472 | 3d | 1 |
| `worktree-agent-diagnose-1474-retry-r-dc741b6d` | →1474 | 3d | 2 |
| `worktree-agent-1496-merge-gemini-specs-e12a43` | →1496 | 1d | 1 |
| `worktree-agent-diagnose-1502-delete-3a662279` | →1502 | 1d | 1 |
| `worktree-agent-diagnose-1504-waves-u-9c7a820b` | →1504 | 1d | 2 |
| `worktree-agent-1538-user-memory-f2-f-b95b0ee6` | →1538 | 1d | 2 |
| `worktree-agent-1539-pattern-watcher-v1-5c245e` | →1539 | 1d | 2 |
| `worktree-agent-saa-messages-bugs-1577-01b09c` | →1577 | 0d | 2 |

### no-needle-orphan (87)

Branches with no extractable needle ID (`—`) or where the extracted number appears to be a hex hash fragment (`~NNN`). These have no traceable ticket.

- **38** branches: no numeric sequence in branch name
- **49** branches: numeric fragment present but not a real needle ID (e.g., `~5782`, `~28562`, `~96733`)

| branch | id-fragment | age | unique_commits | note |
|--------|-------------|-----|----------------|------|
| `worktree-agent-a5f36cb3db5782c29` | ~5782 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-add-current-model-to-write-kerne-28562d` | ~28562 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-add-grants-ws-bus-on-2bce04db` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-add-pdf-docx-support-3e5da0de` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-agent-template-action-7c0d7dc0` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-atlassian-search-jql-69b710e3` | ~710 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-audit-auto-closed-nee-e27d3ebd` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-audit-frontend-pollin-a6c5d52e` | — | 8d | 3 | no needle in branch name |
| `worktree-agent-audit-needles-specs-f-8a5590ee` | ~5590 | 1d | 3 | hash fragment, not a needle |
| `worktree-agent-audit-wave-3-features-fb22e4e5` | — | 0d | 2 | no needle in branch name |
| `worktree-agent-bridge-ttl-stale-rows-ba8fcef3` | — | 8d | 2 | no needle in branch name |
| `worktree-agent-cache-status-clock-to-4fd6e3ce` | — | 8d | 2 | no needle in branch name |
| `worktree-agent-cherry-pick-delete-al-5e8a0c18` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-dedup-port-8000-liste-57475911` | ~8000 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-deprecate-auditor-cod-0c96733d` | ~96733 | 4d | 4 | hash fragment, not a needle |
| `worktree-agent-deprecate-groups-for-5db60e17` | — | 7d | 1 | no needle in branch name |
| `worktree-agent-deprecate-onboarding-05e2caa7` | — | 3d | 1 | no needle in branch name |
| `worktree-agent-diagnose-0-byte-chat-d0de2c43` | — | 7d | 1 | no needle in branch name |
| `worktree-agent-diagnose-ac-generatio-fe499b38` | ~499 | 3d | 2 | hash fragment, not a needle |
| `worktree-agent-diagnose-agents-recen-269dcb7a` | ~269 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-and-fix-agen-83139f5c` | ~83139 | 7d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-api-agents-s-1584c001` | ~1584 | 3d | 3 | hash fragment, not a needle |
| `worktree-agent-diagnose-backend-http-500-on-api-cfa194` | ~500 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-loadavg-need-3bf889a2` | ~889 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-mcp-missing-in-subagent-8b9de6` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-diagnose-missing-builder-spawn-00385b` | ~00385 | 11d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-my-gems-load-69642bc4` | ~69642 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-oauth-redirect-to-8000-b1e274` | ~8000 | 10d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-silent-agent-8354b4c4` | ~8354 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-diagnose-ssl-handshak-97aef94a` | — | 1d | 1 | no needle in branch name |
| `worktree-agent-diagnose-stale-specs-in-list-doc-8e1bfb` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-diagnose-subagent-bail-pattern-1-9d48a0` | — | 11d | 1 | no needle in branch name |
| `worktree-agent-diagnose-tool-call-in-25588253` | ~25588 | 5d | 2 | hash fragment, not a needle |
| `worktree-agent-diagnose-verbose-brie-6ac9a90c` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-diagnose-verbose-brie-e311c0e6` | ~311 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-false-alarm-pattern-d-fe22857a` | ~22857 | 8d | 2 | hash fragment, not a needle |
| `worktree-agent-fix-5-e2e-test-failur-5c2f8967` | ~8967 | 7d | 1 | hash fragment, not a needle |
| `worktree-agent-fix-boot-accuracy-mod-6d4af43c` | — | 6d | 1 | no needle in branch name |
| `worktree-agent-fix-budget-5-injection-in-spawn-4047d0` | ~4047 | 10d | 1 | hash fragment, not a needle |
| `worktree-agent-fix-cost-period-type-8b8957` | ~8957 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-fix-loadavg-needle-count-46639c` | ~46639 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-fix-needles-must-show-in-ui-e7250d` | ~7250 | 10d | 1 | hash fragment, not a needle |
| `worktree-agent-fix-preexisting-test-8e46df8d` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-fix-silent-kill-taking-down-uvic-fe2897` | ~2897 | 10d | 2 | hash fragment, not a needle |
| `worktree-agent-fix-silent-on-backgro-23ed35ab` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-fix-vite-wedge-tight-scope-retry-549cbe` | ~549 | 10d | 1 | hash fragment, not a needle |
| `worktree-agent-imessage-unread-sync-fix` | — | 7d | 1 | no needle in branch name |
| `worktree-agent-imessage-wave-1-fix-u-6d5a2770` | ~2770 | 7d | 2 | hash fragment, not a needle |
| `worktree-agent-implement-inline-task-cfd5a6a0` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-implement-spawn-auto-cc06994b` | ~06994 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-investigate-bulk-cancel-reaper-3a95aa` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-kernel-loadavg-cached-8d398487` | ~39848 | 8d | 3 | hash fragment, not a needle |
| `worktree-agent-migrate-secrets-to-os-4bf390be` | ~390 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-move-files-location-picker-to-on-622471` | ~62247 | 11d | 1 | hash fragment, not a needle |
| `worktree-agent-ostk-hook-install-pil-94817139` | ~94817 | 8d | 2 | hash fragment, not a needle |
| `worktree-agent-ostk-vfs-mount-files-0b8c4adf` | — | 7d | 2 | no needle in branch name |
| `worktree-agent-overnight-full-code-r-82172ee6` | ~82172 | 8d | 2 | hash fragment, not a needle |
| `worktree-agent-parameterize-worktree-1575760b` | ~15757 | 8d | 2 | hash fragment, not a needle |
| `worktree-agent-phase-5-primitives-re-0d4c9799` | ~9799 | 4d | 2 | hash fragment, not a needle |
| `worktree-agent-phase-b-extension-gem-67ae2cd8` | — | 8d | 4 | no needle in branch name |
| `worktree-agent-plans-vs-specs-vs-tas-f13ccb8c` | — | 3d | 1 | no needle in branch name |
| `worktree-agent-prioritize-work-confl-eac60224` | ~60224 | 4d | 1 | hash fragment, not a needle |
| `worktree-agent-re-diagnose-vite-wedge-with-stat-1d1c60` | — | 10d | 1 | no needle in branch name |
| `worktree-agent-re-triage-worktrees-o-bfcd1fa2` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-remove-budget-5-subagent-cap-111-7c6968` | ~111 | 11d | 1 | hash fragment, not a needle |
| `worktree-agent-remove-duplicate-crea-7cd87578` | ~87578 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-remove-hooks-spec-fro-c07963ed` | ~07963 | 3d | 7 | hash fragment, not a needle |
| `worktree-agent-replace-reap-script-1-8250dabe` | ~8250 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-research-broader-spec-aeb6b7a6` | — | 4d | 1 | no needle in branch name |
| `worktree-agent-retry-chat-right-click-needle-10-2bc2e2` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-retry-status-lie-leak-09d12873` | ~12873 | 8d | 1 | hash fragment, not a needle |
| `worktree-agent-review-draft-ready-sp-13cd5dd8` | — | 4d | 1 | no needle in branch name |
| `worktree-agent-status-lie-diagnose-1-767424ba` | ~76742 | 8d | 2 | hash fragment, not a needle |
| `worktree-agent-swap-topbar-to-ws-feed-706762` | ~70676 | 9d | 1 | hash fragment, not a needle |
| `worktree-agent-tier-2-3-ostk-hook-opt-in-instal-f6de82` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-tier-2-4-ostk-doc-decompose-wrap-36ff86` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-tighten-chat-agent-prompt-no-ste-d2f8a0` | — | 10d | 1 | no needle in branch name |
| `worktree-agent-track-1-sidebar-statu-d2adf2cf` | — | 8d | 2 | no needle in branch name |
| `worktree-agent-transcript-resolver-1-6c276858` | ~27685 | 7d | 2 | hash fragment, not a needle |
| `worktree-agent-trim-stale-agents-jsonl-rows-in-e23a8d` | — | 9d | 1 | no needle in branch name |
| `worktree-agent-validate-52-agentfiles-8777e7` | ~8777 | 8d | 2 | hash fragment, not a needle |
| `worktree-agent-verify-card-compass-w-6935d285` | ~6935 | 8d | 2 | hash fragment, not a needle |
| `worktree-agent-verify-pclaude-s-i-wa-6c04a88f` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-wire-api-agents-to-os-7d1758cd` | ~1758 | 7d | 2 | hash fragment, not a needle |
| `worktree-agent-wire-gem-chat-to-gemi-30aba00c` | — | 8d | 1 | no needle in branch name |
| `worktree-agent-ws-connection-dropped-diagnose-1-e1767a` | ~1767 | 11d | 1 | hash fragment, not a needle |
| `worktree-agent-ws-feed-for-sidebar-sessions-112-46b653` | ~112 | 10d | 1 | hash fragment, not a needle |

---

## Deferred: partial-overlap (10)

Ten partial-overlap branches were out of scope for pass 1. They have some commits not in main but also share commits with main. Requires cherry-pick analysis before any deletion decision.

---

## Next steps (pass 2)

1. Delete `closed-needle-stale` branches — 92 branches tied to closed needles. Spot-check the short-ID entries first.
2. Delete `no-needle-orphan` branches — 87 branches with no ticket link. Review the `~NNN` ones manually.
3. Decide on partial-overlap 10 branches (cherry-pick or keep).
4. Run `scripts/worktree-reaper.sh --apply` to remove absorbed physical directories.
