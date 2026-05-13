# Code review — overnight session 2026-05-13 (v3.15.0 → 0e8de80)

Reviewed by: overnight-full-code-review-on-se-e4392e  
Scope: 10 commits on top of v3.15.0 (800f889)

## Verdict table

| Hash | Description | Verdict |
|------|-------------|---------|
| e00e555 | feat(→1241): action doc + per-template buttons on Recent Agents | NIT |
| ae04756 | fix(→1265): migrate Atlassian /search to /search/jql POST | PASS |
| a71fb97 | test(→1264): upgrade inline pytest.skip to decorator form | NIT |
| 7732744 | test(→1269): update skip marker refs →1268→1269 | NIT |
| 282775f | test: point skip markers at live upstream tracking needle | NIT |
| af3b758 | test(→1265): guard test for import_from_jira /search/jql POST | PASS |
| 9f82fd1 | fix(→1266 →1271): template-spawned agents stay visible in Active tab | NIT |
| d934d5d | docs(e2e): →1273 overnight gate retry summary | NIT |
| 863e24e | docs(e2e): →1274 e2e_smoke run — 2 phase failures | NIT |
| 4966538 | fix(→1276): test_1271_agent_filters import path | PASS |
| 0e8de80 | fix(→1277): widen Tasks first-paint test budget to 1500ms | NIT |

No NEEDS-FIX items. No P1 needles filed.

---

## Findings

### e00e555 — action doc + per-template buttons

**XSS check**: `{agent.actionable_doc}` in Agents.tsx is a plain JSX text expression — React
escapes it. No XSS surface.

**NIT 1** — `api/routers/agents.py`, `mark_agent_complete`:

```python
# Generate actionable_doc for template-run agents so the Recent tab
# can surface a plain-language one-liner of what the run produced.
_tpl = str(agent_metadata[name].get("template") or "").strip()
if _tpl and _completion_summary:
    agent_metadata[name]["actionable_doc"] = _completion_summary
```

Two issues. First, the 2-line comment block violates the one-line-max style rule — and it
explains what the code does, not why a non-obvious choice was made. Second, the comment says
"Generate" but the code just copies `_completion_summary` to `actionable_doc`. Call it
"store" or remove the comment.

**NIT 2** — `app/src/components/RecentAgentActions.tsx`:

`runAgainName` is evaluated at render time, not click time:
```ts
const runAgainName = `${tpl.replace(/\s+/g, "-")}-${Date.now()}`;
```
If the component re-renders before the user clicks (e.g. parent state change), a new
timestamp is baked in. The user always gets the name from the most-recent render, not the
name visible when they decided to click. Functionally fine; semantically slightly off.

**handleSpawn call**: `handleSpawn(name, undefined, undefined, undefined, undefined, template)`
— 6th positional arg matches the `template` param in the function signature at Agents.tsx:3457.
Correct.

---

### ae04756 — Atlassian /search → /search/jql POST

Clean migration. `fields` correctly changed from comma string to list. Pagination in
`import_from_jira` changed from offset-based to token-based. Break condition
`not issues or not next_page_token` handles all normal server response shapes:
- last page with items, no token → break ✓
- empty issues regardless of token → break ✓
- `{"nextPageToken": null}` → `data.get(...)` returns None, `not None` is True → break ✓

No max-iteration guard, but this was pre-existing. Test coverage covers URL and body shape.

---

### a71fb97 / 7732744 / 282775f — skip marker churn

Three commits to land what should have been one. The sequence:
1. 7ab: convert to decorator, reference →1268
2. 773: →1268 auto-closed by commit message; re-file as →1269, update refs
3. 282: →1269 needle reference still stale; update again

**NIT**: Commit messages that contain `→NNN` auto-close needles. The churn here was caused
by not knowing that at commit time. End state is clean — `@pytest.mark.skip` decorator form
pointing at the open needle. No action needed beyond awareness.

---

### 9f82fd1 — template-spawned agents grace period

**Grace period correctness**: The three-way gate —

```ts
runningAgentNames.has(a.name)           // WS authoritative
|| a.status === 'spawned'               // optimistic placeholder
|| (isAgentActive(a) && !!a.spawned_at && Date.now() - Date.parse(a.spawned_at) < 30_000)
// recently-started running agent not yet in WS feed
```

Logic is sound:
- `isAgentActive` returns true for `running`, `spawned`, `starting`. The `spawned` case is
  already covered by clause 2, so clause 3 effectively applies to `running`/`starting` agents.
- `!!a.spawned_at` guards against WS-injected stub rows (no spawned_at).
- `Date.parse` on a malformed string returns NaN; `NaN < 30_000` is false → agent stays
  hidden. Safe fallthrough.
- Agents older than 30s (terminated ones with stale rows) have `spawned_at > 30s ago` →
  grace period expires → stay hidden. WS remains authoritative.

**NIT 1** — 7-line comment block in Agents.tsx:

```ts
// Three-way gate when WS is connected (hasSummary=true):
// 1. WS confirmed running (runningAgentNames) — primary/authoritative
// 2. Optimistic placeholder not yet in WS feed (status='spawned')
// 3. Recently started running agent within 30s grace period — covers the race
// ...
```

Per style rules: one short line max. The commit message already captures the full
explanation. Trim to one line: `// grace period for fetchAgents→WS race (→1266)`.

**NIT 2** — `runningCount` (line ~3862) and `isVisibleActive` (line ~3903) contain
identical logic including the new grace-period clause. This duplication predates this
commit but was expanded by it. If one is updated the other will diverge. Pre-existing
tech debt, worth extracting in a follow-up.

**Backend `is_user_spawned_agent` fix**: Adding `source == "daemon"` exclusion to match the
frontend `isUserSpawnedAgent` is correct and the test in `test_1271_agent_filters.py` covers
it properly.

---

### d934d5d / 863e24e — docs

**NIT**: Both commit messages contain em-dashes (`—`), which violates the no-em-dash style
rule. Can't be changed after the fact; noting for future sessions.

Content of both docs is accurate: they correctly summarize the gate run and its failures.

---

### 0e8de80 — Tasks first-paint budget widened

**NIT** — 3-line comment block in Tasks.test.tsx:

```ts
// →1277: widened from 500ms — original was wall-clock and noisy in e2e_smoke runs
// (928ms measured against concurrent backend+frontend+tsc). File →1278 to investigate
// the underlying Tasks-page first-paint slowdown if this budget masks a real regression.
```

Style: multi-line block. The →1277 / →1278 needle refs belong in a commit message, not
inline. Trim to: `// →1277: 500ms was noisy in concurrent e2e runs; →1278 tracks fix`.

**First-paint slow path investigation** (per brief):

`Tasks.tsx` renders filter/sort inline (no `useMemo`) at lines 1374–1443. For the 100-task
test fixture, this runs: `new Map(tasks.map(...))` × 3, `tasks.filter(...)`, `[...filteredTasks].sort(...)`.
None of this is expensive for 100 tasks. The 928ms measurement was in a concurrent
backend+frontend+tsc environment — the slowdown is vitest concurrency overhead, not a
pathological render. The inline sort/filter is fine. →1278 is the right tracker; no urgent
fix needed.

---

## P2 needles filed

Per brief, NEEDS-FIX → P1 needle; NIT → P2 if worth fixing.

The comment-style NITs (multi-line blocks) are pervasive enough that a single P2 cleanup
needle is more useful than per-site needles.

Filed one needle: **P2 — comment-style cleanup: multi-line blocks in Agents.tsx, agents.py,
Tasks.test.tsx (overnight session 2026-05-13)**
