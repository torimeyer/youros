# Worktree triage v2 — 2026-05-13

Classification uses `git cherry main <branch>` (patch-id match), not subject text.

Total classified: 110
Skipped (live agents + main): 3

## ABSORBED (8, safe to delete: commits literally on main)

- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-build-agent-completion-push-hook-651d67` [branch `worktree-agent-build-agent-completion-push-hook-651d67`] — git log main..branch: empty
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-agents-test-cancel-all-f99075` [branch `worktree-agent-diagnose-agents-test-cancel-all-f99075`] — git log main..branch: empty
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-store-drift-52cb2a2e` [branch `worktree-agent-diagnose-store-drift-52cb2a2e`] — git log main..branch: empty
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-vite-wedge-in-both-repo-d45f8a` [branch `worktree-agent-diagnose-vite-wedge-in-both-repo-d45f8a`] — git log main..branch: empty
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-remove-prune-v2-eb1a1b` [branch `worktree-agent-remove-prune-v2-eb1a1b`] — git log main..branch: empty
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-ws-feed-for-agents-locks-1130-481eac` [branch `worktree-agent-ws-feed-for-agents-locks-1130-481eac`] — git log main..branch: empty
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-ws-feed-for-agents-pending-grant-79e4b3` [branch `worktree-agent-ws-feed-for-agents-pending-grant-79e4b3`] — git log main..branch: empty
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-ws-feed-for-sessions-page-1127-67093b` [branch `worktree-agent-ws-feed-for-sessions-page-1127-67093b`] — git log main..branch: empty

## DUPLICATE (57, safe to delete: cherry-picked elsewhere, no + lines)

- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1202-slow-call-asgi-m-8c326ac5` [branch `worktree-agent-1202-slow-call-asgi-m-8c326ac5`] — git cherry output:
  - `- 5de55d1195aa9c7338a1da49b1d3ad1056d40c46`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1214-gate-self-filter-b5d38d` [branch `worktree-agent-1214-gate-self-filter-b5d38d`] — git cherry output:
  - `- 104d0c2a73e3067994593cdffb354e57e2d304d4`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1215-mcp-env-parser-9a7724` [branch `worktree-agent-1215-mcp-env-parser-9a7724`] — git cherry output:
  - `- 02b624fad478ef3f6bd0592066e7d75c22ce45e1`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1216-locate-v6-0-0-so-4eb8e94b` [branch `worktree-agent-1216-locate-v6-0-0-so-4eb8e94b`] — git cherry output:
  - `- d9c21272720643d5c2bf14c1b5be427558c4dafd`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1217-mcp-transport-re-1a5708d7` [branch `worktree-agent-1217-mcp-transport-re-1a5708d7`] — git cherry output:
  - `- 9ea72e213ecb8ed6ce061f760da02cd71b0e763a`
  - `- c00e7d14d1cf5c60b4ecc8e07097d41e9d837bd7`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1219-backend-snapshot-beb78562` [branch `worktree-agent-1219-backend-snapshot-beb78562`] — git cherry output:
  - `- 73d36e23cb1f4164a0121ed4ccc671b3c2b00336`
  - `- eea5214143e09a77712a2035a204c1eaee4b7323`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1221-diagnose-session-22a3af94` [branch `worktree-agent-1221-diagnose-session-22a3af94`] — git cherry output:
  - `- 2692752cf2d83cefd79af58dac39ac02c792c449`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-add-current-model-to-write-kerne-28562d` [branch `worktree-agent-add-current-model-to-write-kerne-28562d`] — git cherry output:
  - `- fa8f121a1b23c5ba9b4dab7e67baf8c45bde489e`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-build-1120-plans-in-recent-docs-50fdbb` [branch `worktree-agent-build-1120-plans-in-recent-docs-50fdbb`] — git cherry output:
  - `- 240fb08dbca1ee3981ce096f7e91674f9eb8250e`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-build-1165-gems-ui-686c28` [branch `worktree-agent-build-1165-gems-ui-686c28`] — git cherry output:
  - `- 4e611e62d336a9bfade2e6298b2b2b676e8ff8d1`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-cherry-pick-1188-onto-main-2650e1` [branch `worktree-agent-cherry-pick-1188-onto-main-2650e1`] — git cherry output:
  - `- 5f93a535c9e33ed00d302bd6cc30ddceffbcd71c`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-clamp-1191-wave-text-395406` [branch `worktree-agent-clamp-1191-wave-text-395406`] — git cherry output:
  - `- 9e906869a89ce3d4725a0f537511edf66c4ee581`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-close-1100-nr-audit-324006` [branch `worktree-agent-close-1100-nr-audit-324006`] — git cherry output:
  - `- df2fdb10d8fc8d5554dccba162338182b4bf034f`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-close-orphan-spec-drafts-1147-d287d8` [branch `worktree-agent-close-orphan-spec-drafts-1147-d287d8`] — git cherry output:
  - `- ad084c4b57d6fa47983350cde73c29aaeee023cf`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-agents-recen-269dcb7a` [branch `worktree-agent-diagnose-agents-recen-269dcb7a`] — git cherry output:
  - `- e096b971617b10181cc0b1c79579d21a617d3803`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-backend-http-500-on-api-cfa194` [branch `worktree-agent-diagnose-backend-http-500-on-api-cfa194`] — git cherry output:
  - `- 26f2d968e6101eda9c46de1e1a6bdde0a9c05e68`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-close-task-b-5d6ca994` [branch `worktree-agent-diagnose-close-task-b-5d6ca994`] — git cherry output:
  - `- 18787ddb29909952aa956d270ced35ed7d3100e3`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-connection-dropped-inli-f615eb` [branch `worktree-agent-diagnose-connection-dropped-inli-f615eb`] — git cherry output:
  - `- 112294b23144285dee1973f94671578a682c4b43`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-loadavg-need-3bf889a2` [branch `worktree-agent-diagnose-loadavg-need-3bf889a2`] — git cherry output:
  - `- d6560a9e4ca054489f917296a052b7fa6693ee08`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-missing-builder-spawn-00385b` [branch `worktree-agent-diagnose-missing-builder-spawn-00385b`] — git cherry output:
  - `- bab2f5bed5ec4e4f7c07a2648d14bb503c175daa`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-my-gems-load-69642bc4` [branch `worktree-agent-diagnose-my-gems-load-69642bc4`] — git cherry output:
  - `- 7d13126c9262507cb04324c270bd02f9c60c99b0`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-oauth-redirect-to-8000-b1e274` [branch `worktree-agent-diagnose-oauth-redirect-to-8000-b1e274`] — git cherry output:
  - `- 05df835ecf008e9f7c3cfde07aa67a0279b8ded0`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-expose-mcp-tools-in-worktree-ses-27e998` [branch `worktree-agent-expose-mcp-tools-in-worktree-ses-27e998`] — git cherry output:
  - `- a8263a4157189d31c33502ba5029cad1557045b2`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-1190-dl-icon-align-3681a2` [branch `worktree-agent-fix-1190-dl-icon-align-3681a2`] — git cherry output:
  - `- 6e8228aeecf81764f74205b88573dd2f3c4121a6`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-1242-gem-test-leak-900563` [branch `worktree-agent-fix-1242-gem-test-leak-900563`] — git cherry output:
  - `- 2eb15a8f1e7788e2b7fbe75682d27e496381a4d7`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-cost-period-type-8b8957` [branch `worktree-agent-fix-cost-period-type-8b8957`] — git cherry output:
  - `- 7ef8a43eb92e28fb69efbc47f7edfc0f48de2968`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-fleet-active-gate-counts-sta-0701fa` [branch `worktree-agent-fix-fleet-active-gate-counts-sta-0701fa`] — git cherry output:
  - `- e62b55e56a7ee091d37219f974de2f55d577bde1`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-gemini-api-reachable-lie-b885b3` [branch `worktree-agent-fix-gemini-api-reachable-lie-b885b3`] — git cherry output:
  - `- aa999fbe88f9129187908ec8e310aab3e50cb6d7`
  - `- 0cdb687c9fbc67a82fc020a2a3defbc249dbed3f`
  - `- 4a2ea7b064caac288a2323ba789dcccf6571361b`
  - `- 46f14ce9e90ffc581820fbb8f536d0085c4d1c3a`
  - `- f39c9cb46d61241180a478bc218b0c7eda2b7b3d`
  - `- dffb7e3395a577169207a01e7295f69011fa3069`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-google-workspace-oauth-1186-9a0dea` [branch `worktree-agent-fix-google-workspace-oauth-1186-9a0dea`] — git cherry output:
  - `- db712d98bcb71a3f535a5f52f8dadcda64fa930c`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-needles-must-show-in-ui-e7250d` [branch `worktree-agent-fix-needles-must-show-in-ui-e7250d`] — git cherry output:
  - `- 8ff1bb9bf1ed8ba9f134c6f8c69a4157b2f28aa8`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-preexisting-test-8e46df8d` [branch `worktree-agent-fix-preexisting-test-8e46df8d`] — git cherry output:
  - `- 04b1373895524770860fbcf3665dc309d1db1968`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-subagent-ostk-mcp-propagatio-7ba164` [branch `worktree-agent-fix-subagent-ostk-mcp-propagatio-7ba164`] — git cherry output:
  - `- c2680bbf3a9dd1757edacc989f4de9b7c598ff28`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-vite-wedge-tight-scope-retry-549cbe` [branch `worktree-agent-fix-vite-wedge-tight-scope-retry-549cbe`] — git cherry output:
  - `- 601be6e5a018eac36a780922d19379648a73ebc9`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-gemini-phase-a-backend-1161-fc5984` [branch `worktree-agent-gemini-phase-a-backend-1161-fc5984`] — git cherry output:
  - `- 3e27ac2e1684debb62b841101df883d9d47b75cb`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-guided-tour-opt-in-1183-1184-bfc051` [branch `worktree-agent-guided-tour-opt-in-1183-1184-bfc051`] — git cherry output:
  - `- fd497e76fcf00202a030b089873443180e8c2bda`
  - `- 3012bfc1142720625dc226b639f180db0be70b76`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-implement-inline-task-cfd5a6a0` [branch `worktree-agent-implement-inline-task-cfd5a6a0`] — git cherry output:
  - `- 1bfb7edeadf21332a8e3f3ffecb2905062f6c281`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-implement-spawn-auto-cc06994b` [branch `worktree-agent-implement-spawn-auto-cc06994b`] — git cherry output:
  - `- dc991cf1ce7918c45774c8a6702db4a8b3eaf329`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-move-files-location-picker-to-on-622471` [branch `worktree-agent-move-files-location-picker-to-on-622471`] — git cherry output:
  - `- a33e663f7121cddee87520a058a36587a1552fdb`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-mygems-subtitle-paddi-665fdbee` [branch `worktree-agent-mygems-subtitle-paddi-665fdbee`] — git cherry output:
  - `- ba418c8723fddc800763837124c71b95a2a482b8`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-p99-latency-test-for-74468c9b` [branch `worktree-agent-p99-latency-test-for-74468c9b`] — git cherry output:
  - `- cf7d087f7b8a993fdd3d21642f036f3b5713f4c0`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-re-diagnose-vite-wedge-with-stat-1d1c60` [branch `worktree-agent-re-diagnose-vite-wedge-with-stat-1d1c60`] — git cherry output:
  - `- 09600e92b3ab3330ae7b7ce27765685d3d3c3696`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-real-time-in-progress-badge-1118-f73956` [branch `worktree-agent-real-time-in-progress-badge-1118-f73956`] — git cherry output:
  - `- 1a15fa03abb0f445b8b59ad2618a85b2b5d0f85a`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-remove-budget-5-subagent-cap-111-7c6968` [branch `worktree-agent-remove-budget-5-subagent-cap-111-7c6968`] — git cherry output:
  - `- 8b5993a8594f6b3ab9bb4aa500ba999532005ba1`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-remove-duplicate-crea-7cd87578` [branch `worktree-agent-remove-duplicate-crea-7cd87578`] — git cherry output:
  - `- 73f5b4a08fae5f5da38cd44bf25106790a4e07fd`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-respawn-tasks-test-fix-1160-b78575` [branch `worktree-agent-respawn-tasks-test-fix-1160-b78575`] — git cherry output:
  - `- c64fa307ebd80f9d68674f75f9eef06969570714`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-retry-1153-fleet-active-gate-3e23c3` [branch `worktree-agent-retry-1153-fleet-active-gate-3e23c3`] — git cherry output:
  - `- dfd5be705eb1dc70e95817ce94b774b48afb5d27`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-retry-status-lie-leak-09d12873` [branch `worktree-agent-retry-status-lie-leak-09d12873`] — git cherry output:
  - `- 6133f61dc6b9e0dd20dad47526bd334c5c3ceb72`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-surface-chat-error-ro-b907d5cd` [branch `worktree-agent-surface-chat-error-ro-b907d5cd`] — git cherry output:
  - `- 0bb7e8d30a042f3d5c10f79dd3d5795d6adf3bf3`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-swap-chatpanel-to-ws-0a9db691` [branch `worktree-agent-swap-chatpanel-to-ws-0a9db691`] — git cherry output:
  - `- 61dd7fc7aa5e2578926098c1b21c13cd727e8390`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-swap-topbar-to-ws-feed-706762` [branch `worktree-agent-swap-topbar-to-ws-feed-706762`] — git cherry output:
  - `- 320f3d6316691a14ffe884193f731c6ec2deca7c`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-tier-2-3-ostk-hook-opt-in-instal-f6de82` [branch `worktree-agent-tier-2-3-ostk-hook-opt-in-instal-f6de82`] — git cherry output:
  - `- c6a6209e686025ea2798b1f332014caf57629119`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-tier-2-4-ostk-doc-decompose-wrap-36ff86` [branch `worktree-agent-tier-2-4-ostk-doc-decompose-wrap-36ff86`] — git cherry output:
  - `- e1629439ff4c9280c58099983ad0fff1b2909ecd`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-tighten-chat-agent-prompt-no-ste-d2f8a0` [branch `worktree-agent-tighten-chat-agent-prompt-no-ste-d2f8a0`] — git cherry output:
  - `- 39747c6ae9ff55b0b541a44559a5e5386e9576eb`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-trim-stale-agents-jsonl-rows-in-e23a8d` [branch `worktree-agent-trim-stale-agents-jsonl-rows-in-e23a8d`] — git cherry output:
  - `- edcba8f7ef76b8546683cd01423f206c09e9b8fa`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-verify-1163-mcp-drop-4b30d6` [branch `worktree-agent-verify-1163-mcp-drop-4b30d6`] — git cherry output:
  - `- 2fed728a6c29eb208dd81e46b8861a761e474465`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-wire-gem-chat-to-gemi-30aba00c` [branch `worktree-agent-wire-gem-chat-to-gemi-30aba00c`] — git cherry output:
  - `- 7eaf65ecd8584c30e45c098bad38b2a508492353`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-ws-connection-dropped-diagnose-1-e1767a` [branch `worktree-agent-ws-connection-dropped-diagnose-1-e1767a`] — git cherry output:
  - `- 7c0344f6780b04c735dcd5daa301d1e4456a0c04`

## UNIQUE (45, has unmerged work)

- `/private/tmp/topbar-ws-1133` [branch `worktree-agent-build-1133-topbar-ws`] — 1 unique commits:
  + `+ 6a7c4d337410f9604a5213b568a077160e9c4dc1`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-1176-tier-1-1b-delete-reaper-b0ab38` [branch `worktree-agent-1176-tier-1-1b-delete-reaper-b0ab38`] — 1 unique commits:
  + `+ 8fee66608ed809578718001eb073ccc6be4a05b9`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-build-1134-chat-panel-21b1e90b` [branch `worktree-agent-build-1134-chat-panel-21b1e90b`] — 1 unique commits:
  + `+ 365b05783fdf053168b18232c4e34ff2505c8f5f`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-cache-status-clock-to-4fd6e3ce` [branch `worktree-agent-cache-status-clock-to-4fd6e3ce`] — 1 unique commits:
  + `+ b6eba6a594ecae507d7d7c43a6e89ab768af8140`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-chat-right-click-needle-1056-7a9389` [branch `worktree-agent-chat-right-click-needle-1056-7a9389`] — 1 unique commits:
  + `+ 2da11b0481c04ddac717d649853cfc6257fd0dd0`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-1167-spawn-test-flake-f97436` [branch `worktree-agent-diagnose-1167-spawn-test-flake-f97436`] — 1 unique commits:
  + `+ 85ed37964dee994c641b08ee5ef68a4bfb407218`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-1192-backend-wedge-take-d52b56` [branch `worktree-agent-diagnose-1192-backend-wedge-take-d52b56`] — 1 unique commits:
  + `+ d1781518ed7003057f18e46694e6c82797451ba8`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-backend-api-wedge-1178-c5ad0c` [branch `worktree-agent-diagnose-backend-api-wedge-1178-c5ad0c`] — 1 unique commits:
  + `+ 792563ac03132db417c084d721d5158b2768e7dd`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-backend-wedge-1192-9ba9c3` [branch `worktree-agent-diagnose-backend-wedge-1192-9ba9c3`] — 1 unique commits:
  + `+ 3ff4a551685f67ea3f01715de878b0e9a093c2ef`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-dual-state-p-bf096bd7` [branch `worktree-agent-diagnose-dual-state-p-bf096bd7`] — 1 unique commits:
  + `+ f7d3682bcbce23dc7bc7479ed6996c137ff61355`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-fix-api-agents-wedge-11-c0b856` [branch `worktree-agent-diagnose-fix-api-agents-wedge-11-c0b856`] — 2 unique commits:
  + `+ 64662dde21aae260af99e1a07287933d10aeae6a`
  + `+ 97bbabe9a3f078f6062849f38e46fb9f67562839`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-mcp-missing-in-subagent-8b9de6` [branch `worktree-agent-diagnose-mcp-missing-in-subagent-8b9de6`] — 1 unique commits:
  + `+ 7a2561b2a45e6070ebaff6569378838385707342`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-stale-specs-in-list-doc-8e1bfb` [branch `worktree-agent-diagnose-stale-specs-in-list-doc-8e1bfb`] — 1 unique commits:
  + `+ 4ddd9c06d6d8725d662d6946a97d1f772e00ff6a`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-status-stuck-running-bu-580af8` [branch `worktree-agent-diagnose-status-stuck-running-bu-580af8`] — 1 unique commits:
  + `+ 01250bd13d597aa051fdb49b53eb133f1692cc96`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-subagent-bail-pattern-1-9d48a0` [branch `worktree-agent-diagnose-subagent-bail-pattern-1-9d48a0`] — 1 unique commits:
  + `+ c5040c1c5830af177ef36e41eaaf0a63c9bc2fd7`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-verbose-brie-6ac9a90c` [branch `worktree-agent-diagnose-verbose-brie-6ac9a90c`] — 1 unique commits:
  + `+ 2cdc16af6fdf0fba81c8ee029bc2ce32548a4408`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-diagnose-verbose-brie-e311c0e6` [branch `worktree-agent-diagnose-verbose-brie-e311c0e6`] — 1 unique commits:
  + `+ 9db5257848e558d25816d670c968f3c5afadd457`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-false-alarm-pattern-d-fe22857a` [branch `worktree-agent-false-alarm-pattern-d-fe22857a`] — 2 unique commits:
  + `+ 607e7cd68726cc5c3498f1d51084051887c75062`
  + `+ 4d9ffce3b078af1f76d9c0a14718993259c7a535`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-1188-export-label-afe578` [branch `worktree-agent-fix-1188-export-label-afe578`] — 1 unique commits:
  + `+ 5a0622badc375df886134d52bcaeeb354a0c037b`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-1191-plan-waves-wall-of-text-3440a9` [branch `worktree-agent-fix-1191-plan-waves-wall-of-text-3440a9`] — 1 unique commits:
  + `+ 69421a2addf87bbe07c397cd723df8928409a8fd`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-budget-5-injection-in-spawn-4047d0` [branch `worktree-agent-fix-budget-5-injection-in-spawn-4047d0`] — 1 unique commits:
  + `+ 3154e09257e059e9b85c972c4d570855ee3a72da`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-loadavg-needle-count-46639c` [branch `worktree-agent-fix-loadavg-needle-count-46639c`] — 1 unique commits:
  + `+ 1028114b92694f00de8789c1b90155c43dd3013a`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-silent-kill-taking-down-uvic-fe2897` [branch `worktree-agent-fix-silent-kill-taking-down-uvic-fe2897`] — 1 unique commits:
  + `+ 946b059dcf8ca33f9cba3b60ceee844b19501e92`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-silent-on-backgro-23ed35ab` [branch `worktree-agent-fix-silent-on-backgro-23ed35ab`] — 1 unique commits:
  + `+ 87c3eec25f679b480cc4ead3cd2361db0d9ce418`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-subagent-ostk-mcp-not-loaded-411a0e` [branch `worktree-agent-fix-subagent-ostk-mcp-not-loaded-411a0e`] — 1 unique commits:
  + `+ 5b720a8f348deeeab4e5ab5ee591f60465e2ecd2`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fix-tasks-test-failures-1160-5c7f60` [branch `worktree-agent-fix-tasks-test-failures-1160-5c7f60`] — 1 unique commits:
  + `+ 608423cddf54fae64309ac503539d83f31dfb73e`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-fs-ops-worktree-leak-bdfff617` [branch `worktree-agent-fs-ops-worktree-leak-bdfff617`] — 1 unique commits:
  + `+ 92d822f83667ba898a42746ec116de43d50f5bed`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-gemini-phase-a-frontend-1162-d24194` [branch `worktree-agent-gemini-phase-a-frontend-1162-d24194`] — 1 unique commits:
  + `+ 2316ea508d7f1142488a866efcd9a804a8e2787c`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-investigate-bulk-cancel-reaper-3a95aa` [branch `worktree-agent-investigate-bulk-cancel-reaper-3a95aa`] — 1 unique commits:
  + `+ 733d0d3044a57cfc7a1e228f74a60881987765c9`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-phase-b-extension-gem-67ae2cd8` [branch `worktree-agent-phase-b-extension-gem-67ae2cd8`] — 3 unique commits:
  + `+ 3da0ab821508649d601f9f6e2594d9d1c012e356`
  + `+ 5d46f7e152ed8b0a62f33699f2715377e33f776e`
  + `+ 1c74673df473fda0c38c647e07deb496ba4f145d`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-py-spy-stack-dump-cap-bfa13000` [branch `worktree-agent-py-spy-stack-dump-cap-bfa13000`] — 1 unique commits:
  + `+ aa92e1e93f23be40f8784f85aa1ffbefe3f7ef6f`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-rag-for-gem-knowledge-899eb669` [branch `worktree-agent-rag-for-gem-knowledge-899eb669`] — 2 unique commits:
  + `+ 6c13a7ad1826e9318f21304cd933d8d63ed863fc`
  + `+ 9bd48f3dedbaf88d93a993a6535d8c1799d1ffc4`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-rebuild-gemini-frontend-1165-0650cd` [branch `worktree-agent-rebuild-gemini-frontend-1165-0650cd`] — 1 unique commits:
  + `+ a0686f2e04e620b18626aedd6c0d3b718647b223`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-retry-chat-right-click-needle-10-2bc2e2` [branch `worktree-agent-retry-chat-right-click-needle-10-2bc2e2`] — 1 unique commits:
  + `+ 4eabffe2e6c545fcd3ca857fff5fb86dee739de5`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-sidebar-spec-counts-ws-1125-e3e829` [branch `worktree-agent-sidebar-spec-counts-ws-1125-e3e829`] — 1 unique commits:
  + `+ 5ea91c4e9f81e219ae113f6bf0a12a2f56027776`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-sidebar-task-counts-ws-1124-7e2f02` [branch `worktree-agent-sidebar-task-counts-ws-1124-7e2f02`] — 1 unique commits:
  + `+ 80714b94be4399b170f8e17f0c5333bf6189e09a`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-status-lie-diagnose-1-767424ba` [branch `worktree-agent-status-lie-diagnose-1-767424ba`] — 2 unique commits:
  + `+ e34fd689ce9da4f2954ed92d58277a655900d167`
  + `+ eacae06b9042fdeb21af9a5b53dfa3d0a16860b6`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-streaming-chat-newlines-1121-547eb8` [branch `worktree-agent-streaming-chat-newlines-1121-547eb8`] — 1 unique commits:
  + `+ 22743abfc24ac0024cfafb7504f50c99fec556c4`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-subagent-ostk-mcp-retry-1148-ee81bc` [branch `worktree-agent-subagent-ostk-mcp-retry-1148-ee81bc`] — 2 unique commits:
  + `+ 2d445ef9bb2fa17961ba034b0095a870dc699d9f`
  + `+ 457b4bbb47457403aa201a40eefb67df5da2d51b`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-tier-1-1a-retry-kernel-jsonl-fle-d870ca` [branch `worktree-agent-tier-1-1a-retry-kernel-jsonl-fle-d870ca`] — 1 unique commits:
  + `+ 1867af7e09a8ee729e5d0153313b8e7fd8ca2b66`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-track-1-sidebar-statu-d2adf2cf` [branch `worktree-agent-track-1-sidebar-statu-d2adf2cf`] — 1 unique commits:
  + `+ e79c724f543c6d971cbec29fe979cfab57bebdca`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-verify-pclaude-s-i-wa-6c04a88f` [branch `worktree-agent-verify-pclaude-s-i-wa-6c04a88f`] — 1 unique commits:
  + `+ e40c0b7fbe5dc27f7f8fafe4d322fcae01a1c9fb`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-wave-3a-agent-1131-dashboard-dat-85468f` [branch `worktree-agent-wave-3a-agent-1131-dashboard-dat-85468f`] — 1 unique commits:
  + `+ d1e58203c2d53eefbb8198a5fdfb69c76bd20c44`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-wave-3a-agent-1132-topbar-notifi-3d79d3` [branch `worktree-agent-wave-3a-agent-1132-topbar-notifi-3d79d3`] — 1 unique commits:
  + `+ 22925590f0d39ee60f80e06ecaaac1324c1716ee`
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-ws-feed-for-sidebar-sessions-112-46b653` [branch `worktree-agent-ws-feed-for-sidebar-sessions-112-46b653`] — 1 unique commits:
  + `+ fd8ace3272222b5e1c990e754b8c6650ab2a2221`

## SKIPPED (3, live agents or main repo)

- `/Users/torimeyer/claude/torios` [branch `main`] — running agent or main
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-dedup-port-8000-liste-57475911` [branch `worktree-agent-dedup-port-8000-liste-57475911`] — running agent or main
- `/Users/torimeyer/claude/torios/.claude/worktrees/agent-re-triage-worktrees-o-bfcd1fa2` [branch `worktree-agent-re-triage-worktrees-o-bfcd1fa2`] — running agent or main
