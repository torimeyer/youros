# Orphan Worktree Triage — 2026-05-20

**Run by:** worktree-triage-agent  
**Total unique worktrees found:** 221  
**Deeply triaged:** 14 (7 high-value + 7 secondary)  
**Cherry-picked:** 2  
**Tasks filed:** 3 (→1540, →1541, →1542)

---

## Cherry-picks landed (commit on this branch)

| Commit | Worktree | Task | What it does |
|--------|----------|--------|--------------|
| `8802abc` | agent-1338-http-https-probe-22f8c807 | →1338 | Migrate http probe callsites to https: enterprise.py, extension/background.js, extension/options.js, extension/manifest.json, start.sh, READMEs |
| `6e1c428` | agent-1345-e2e-settings-pol-da529a69 | →1345 | Isolate e2e settings via YOUROS_HOME + fix restore-before-clear bug in e2e_smoke.sh. 6/6 tests pass. |

---

## High-value candidates (7) — detailed classification

| Worktree | Task | Files | Classification | Reason |
|----------|--------|-------|----------------|--------|
| agent-atlassian-site-split-345bd077 | →1448 | schemas.py, atlassian.py, Settings.tsx + tests | **conflict** (→1540) | 41 merge conflict markers. atlassian.py changed by →1452 on main; Settings.tsx changed by F4 memory provenance. Needs manual resolution. |
| agent-1219-backend-snapshot-beb78562 | →1219 | agents.py, main.py | **superseded** | Feature fully incorporated: agents.py line 56 has `→1219` comment, 500ms snapshotter loop and WS delta already live. |
| agent-1338-http-https-probe-22f8c807 | →1338 | enterprise.py, extension/*, start.sh | **cherry-picked** ✓ | 0 conflicts. enterprise.py and extension/background.js still had http:// defaults. Applied as 8802abc. |
| agent-1344-premature-close-6fc64692 | →1344 | scaffold_commit_watcher.sh | **superseded** | SPAWNED_AT from API fix incorporated (lines 45-101 of watcher). Further fixes →1346 and →1348 landed on top. |
| agent-1345-e2e-settings-pol-da529a69 | →1345 | settings_store.py, e2e_smoke.sh | **cherry-picked** ✓ | 0 conflicts. YOUROS_HOME not present in main. 6 tests pass. Applied as 6e1c428. |
| agent-1496-merge-gemini-specs-e12a43 | →1496 | (none — gitignored) | **superseded** | Commit modifies only docs/spec/ which is gitignored. No git-tracked changes. |
| agent-add-pdf-docx-support-3e5da0de | →1284 | gems.py, gem_knowledge.py | **superseded** | Already in main as `1e59e4d feat(gems): accept .pdf and .docx for knowledge upload (→1284)`. |

---

## Secondary batch (7) — classification

| Worktree | Task | Classification | Reason |
|----------|--------|----------------|--------|
| agent-fix-completion-watche-149bed32 | →1449 | **superseded** | Same feature in main as `32a8a35 fix(hooks): make agent-completion-watcher a genuine cross-worktree singleton (→1449)` (different hash, same work). |
| agent-remove-hooks-spec-fro-c07963ed | →1450 | **stale** | Only actual commit touches `docs/draft/hooks-review-2026-05-15.md`. Branch has 41 conflicts from older merged work. Docs content persists in filesystem. No code change needed. |
| agent-wave-3-eta-pill-syste-0801d59e | →1444 | **conflict** (→1542) | ETA pill + system-prompt time visibility not found in main. Absorbed in atlassian-site-split which itself conflicts. Needs separate resolution pass. |
| agent-diagnose-jira-adf-ren-4ab2d025 | →1443 | **conflict** (→1542) | Jira ADF rendering not found in main. Same situation as →1444 — lives inside atlassian-site-split branch. Bundled into Task →1542. |
| agent-diagnose-api-agents-s-1584c001 | →1445 | **stale** | CC session glob offload to thread (40 conflict markers). Main solved event-loop starvation differently: `6b8de01 fix(agents): unblock event loop on large audit.jsonl`, `42f2e08 fix(backend): move agent state fsync off event loop`. Approach superseded. |
| agent-build-1458-ai-backend-15ca4dea | →1458 | **conflict** (→1541) | `get_ai_client()` abstraction across 7 routers — only 2 minor conflicts (api_key block + test docstring). Worth a cherry-pick pass after main routing stabilizes. |
| agent-phase-b-extension-gem-67ae2cd8 | →1235 | **superseded** | Same feature in main under different hashes: `bf623d4` (gems page), `2e4289c` (extension), `ac6865b` (capture backend). |

---

## Remaining 207 worktrees — bulk assessment

The remaining 207 unique worktrees were not individually inspected. Based on naming patterns:

| Category | Count (est.) | Likely status |
|----------|-------------|---------------|
| `diagnose-*` | ~40 | Superseded — diagnostic work, read-only or incorporated |
| `fix-NNNN-*` | ~35 | Mix — cross-check each against main's git log for the Task number |
| `cherry-pick-*` | ~10 | Likely superseded — the cherry-pick was the point |
| `build-NNNN-*` | ~15 | Mix — check if Task was closed |
| `wave-*`, `phase-*`, `track-*` | ~20 | Mix — check if wave was completed |
| `worktree-agent-[hash]` (unnamed) | ~5 | Unknown — inspect manually |
| Other | ~82 | Mix |

A follow-up triage pass should focus on the `fix-*` and `build-*` categories with >2 unique files.
Use: `MYOS_ACTIVE_AGENTS='' scripts/worktree-reaper.sh 2>&1 | awk '$2=="unique" && $3>2'`

---

## Follow-up Tasks

| Task | Priority | Description |
|--------|----------|-------------|
| →1540 | P1 | Atlassian site split (→1448): resolve 41 conflicts in atlassian.py + Settings.tsx |
| →1541 | P2 | AI backend abstraction (→1458): 2 minor conflicts, cherry-pick when routing stabilizes |
| →1542 | P2 | Wave-3 features (→1443 ADF, →1444 ETA pill): not in main, bundled in atlassian-site-split |
