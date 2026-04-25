# Stash provenance ledger — 2026-04-24

Full patches archived at `/tmp/stash-archive-2026-04-24/stash-{0..10}.patch`. Keep 7 days.

## Listing

Raw `git stash list` captured at `/tmp/stash-archive-2026-04-24/LIST.txt`:

```
stash@{0}:  On main: temp-for-baseline
stash@{1}:  On main: triage-2026-04-23: frontend misc (App, Agents, Tasks, Dashboard, websocket, markdown, api-lib, admin, etc) — needs provenance
stash@{2}:  On main: triage-2026-04-23: standalone test additions — needs provenance
stash@{3}:  On main: triage-2026-04-23: auth-tier shifts, stray feature flags, chat reroute — needs provenance
stash@{4}:  On main: triage-2026-04-23: FCP pdf plugin WIP — needs provenance
stash@{5}:  On main: triage-2026-04-23: Drive/Files page deletion — contradicts 6d985e2, needs provenance
stash@{6}:  On main: triage-2026-04-23: Gmail composer polish — needs provenance
stash@{7}:  On main: triage-2026-04-23: iMessage WIP — needs provenance
stash@{8}:  On main: triage-2026-04-23: Calendar/FCP settings — needs provenance
stash@{9}:  WIP on main: e3c00dc feat: add in-memory rate limiter for spawn + chat endpoints
stash@{10}: WIP on main: d76cf30 feat: build recent specs widget for dashboard (→659)
```

## Per-stash

### stash@{0} — temp-for-baseline
- Stat: watchdog test (`scripts/backend_watchdog.sh`), `api/main.py`, +135/-12 across 3 files
- Scratch baseline work. Probably an artifact of a test-run experiment.
- **Verdict: drop** after confirming; archive at `/tmp/stash-archive-2026-04-24/stash-0.patch`.

### stash@{1} — triage-2026-04-23: frontend misc
- Stat: 14+ files incl. `app/src/pages/Workflows.tsx`, `admin/Policies.tsx`. +42/-ish
- Broad cross-cutting frontend work with no clear provenance. Author history unknown.
- **Verdict: park-as-needle**. File "P3 review parked frontend-misc stash from 2026-04-23". Then drop.

### stash@{2} — standalone test additions
- Stat: 1 file, `scripts/dev-backend.sh`, +5 insertions (adding `--reload-exclude 'routers/chat.py'` etc)
- Dev-backend reload-exclude additions. Probably a polish.
- **Verdict: apply-now** (low risk, tiny patch). If conflicts, park-as-needle.

### stash@{3} — auth-tier shifts, stray flags, chat reroute
- Stat: unknown; has `ostk`-related auth/flag changes. Needs read.
- **Verdict: ask-user**. May touch sensitive auth paths. Do not drop without review.

### stash@{4} — FCP pdf plugin WIP
- Stat: FCP-pdf focused. Plugin WIP work.
- **Verdict: park-as-needle**. File "P3 finish FCP pdf plugin WIP, parked 2026-04-23". Then drop.

### stash@{5} — Drive/Files page deletion, contradicts 6d985e2
- Stat: restores pieces deleted by 6d985e2. Contradicts main's direction.
- **Verdict: drop**. v3.6.0 F1-F7 will rebuild Drive/Files anyway, so re-adding the deleted versions is actively harmful. Archive preserves the patch in case a specific UI detail is needed later.

### stash@{6} — Gmail composer polish
- Stat: `GmailReplyComposer.tsx` Enter-to-send keybinding + error/success field rewiring.
- **Verdict: apply-now** (small, isolated Gmail polish). If conflicts, park-as-needle.

### stash@{7} — iMessage WIP
- Stat: iMessage integration WIP.
- **Verdict: park-as-needle**. File "P3 iMessage integration WIP, parked 2026-04-23". Then drop.

### stash@{8} — Calendar/FCP settings
- Stat: Calendar auth/status with Promise.all(fetchEvents + authStatus) refactor.
- **Verdict: apply-now** (calendar polish, small). If conflicts, park-as-needle.

### stash@{9} — rate-limiter WIP (on e3c00dc)
- Stat: in-memory rate limiter for spawn + chat endpoints.
- Main has `api/services/rate_limit.py` + `api/tests/test_rate_limit.py` already. Likely superseded.
- **Verdict: verify-then-drop**. `diff` against main's rate_limit.py. If content matches, drop. If not, park-as-needle.

### stash@{10} — specs widget WIP (on d76cf30, →659)
- Stat: dashboard specs widget for needle →659. Main has `api/routers/specs.py` tests for `test_specs.py`. Likely superseded.
- **Verdict: verify-then-drop**. Check →659 status; if closed or widget is on main, drop. If not, park.

## Recommended batch actions

| Stash | Verdict | Ready to act |
|---|---|---|
| 0 | drop | yes (scratch) |
| 2 | apply | yes (small) |
| 5 | drop | yes (harmful) |
| 6 | apply | yes (small) |
| 8 | apply | yes (small) |
| 1 | park as needle, then drop | needs needle |
| 4 | park as needle, then drop | needs needle |
| 7 | park as needle, then drop | needs needle |
| 3 | ASK USER | sensitive, needs human read |
| 9 | verify, then drop or park | needs diff against main |
| 10 | verify, then drop or park | needs →659 status check |

Drops require explicit user confirmation per torios destructive-ops rule.

## Final triage summary — 2026-04-25 (agent 919)

All 10 remaining stashes resolved. Decide entries in `.ostk/decisions.jsonl`.

| Stash (current idx) | Label | Verdict | Detail |
|---|---|---|---|
| stash@{0} | temp-for-baseline | **dropped** | decide: drop-stash-0-content. Documents router disable superseded — main has no documents import. |
| stash@{1} | frontend misc (App.tsx + 44 files) | **parked →926** | decide: drop-stash-1-content. archive stash-1.patch |
| stash@{2} | standalone test additions (dev-backend.sh) | **noop** | Already on main — git stash apply returned "Already up to date". |
| stash@{3} | auth-tier shifts label / actual: dev-backend.sh dup | **dropped** | decide: drop-stash-3-content. Exact duplicate of stash@{2}. |
| stash@{4} | FCP pdf label / actual: auth+claude_code_provider+tests | **parked →927** | decide: drop-stash-4-content. 8 files 807 ins. archive stash-4.patch |
| stash@{5} | Gmail composer label / actual: empty | **dropped** | decide: drop-stash-5-content. Empty stash, 0 bytes. |
| stash@{6} | iMessage label / actual: Gmail search-preview+composer | **parked →928** | decide: drop-stash-6-content. archive stash-7.patch |
| stash@{7} | Calendar label / actual: iMessage WIP | **parked →929** | decide: drop-stash-7-content. 5 files 1314 ins. archive stash-8.patch |
| stash@{8} | rate-limiter label / actual: PRIVACY.md + 46 files | **parked →930** | decide: drop-stash-8-content. 3019 ins/-3528 del. archive stash-9.patch |
| stash@{9} | specs widget label / actual: agents+calendar+files+chat fixes | **applied** | 6 files: agents.py (dup-spawn fix), calendar.py (ttl), files.py (PDF LRU cache), ChatPanel.tsx (dead-backend timer), Agents.tsx (user_spawned_only), Agents.test.tsx. Calendar.tsx conflict resolved keeping upstream (speculative-fetch logic superior). |

**Drops:** 3 (stash@{0}, @{3}, @{5})
**Parks:** 5 (→926, →927, →928, →929, →930)
**Applied:** 1 (stash@{9}, commit below)
**Noop:** 1 (stash@{2} already on main)

Note: archive patch indices are off-by-one for stashes @{6}–@{9} due to how the archive was created. Archive file stash-N.patch maps: @{6}→stash-7, @{7}→stash-8, @{8}→stash-9, @{9}→stash-10.
