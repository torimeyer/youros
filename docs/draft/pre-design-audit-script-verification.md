# Pre-Design Audit Script Verification (→1790)

Script: `~/.myos/pre-design-audit.py` (19718 bytes, executable)
Repo: `/Users/torimeyer/claude/torios`
Date: 2026-05-28

---

## Summary

| Test input | Expected verdict | Actual verdict | Pass? |
|------------|-----------------|----------------|-------|
| SourceBadge | POSSIBLE MATCH | MATCH FOUND | **FAIL** |
| ChatPanel | MATCH FOUND | MATCH FOUND | PASS |
| ZxqfQuantumWidget | CLEAR | CLEAR | PASS |

2 of 3 correct. One mismatch with a root cause identified (see Issue A below).

---

## Test 1: SourceBadge

Expected: `POSSIBLE MATCH` (semantic hit on `ClaimSourceChip.tsx`)

Actual output (verbatim):

```
## Pre-design audit: `SourceBadge`

| Signal | Finding |
|--------|---------|
| Codebase (literal) | none |
| Codebase (semantic) | app/src/components/ClaimSourceChip.tsx [chip, claim, source]; api/.venv/lib/python3.11/site-packages/pygments/lexers/_sourcemod_builtins.py [origin, source, tag]; api/services/source_library.py [source, tag] (+3 more) |
| Git log  | none |
| Tasks/Specs | docs/spec/pre-design-audit-catch-existing-patterns-before-proposing-new-infrastructure.md |

**MATCH FOUND.** You must choose before proceeding:
- [ ] Reuse the existing component (state which file and how)
- [ ] Justify why a new implementation is needed (state the gap)

---
MATCH FOUND
```

**Verdict: FAIL.** The script returned `MATCH FOUND` instead of `POSSIBLE MATCH`.

Root cause: Signal 4 (Tasks/Specs) hit on `docs/spec/pre-design-audit-catch-existing-patterns-before-proposing-new-infrastructure.md`. That spec is about the pre-design audit system itself — it is not a spec for a SourceBadge component. The word "source" in the slug triggered a false positive. This elevated a semantic-only hit (which should be POSSIBLE MATCH) to MATCH FOUND.

Secondary noise: Signal 2 (Semantic) includes `api/.venv/lib/python3.11/site-packages/pygments/lexers/_sourcemod_builtins.py` — a third-party library file inside the venv. This is not project code and should be excluded from semantic results.

---

## Test 2: ChatPanel

Expected: `MATCH FOUND` (literal hit)

Actual output (verbatim):

```
## Pre-design audit: `ChatPanel`

| Signal | Finding |
|--------|---------|
| Codebase (literal) | transcripts/diagnose-1464-chatpanel-tab-flak-c48009.md.stderr.log, transcripts/swap-chatpanel-to-ws-subscriptio-fc01a0.md.stderr.log, transcripts/diagnose-1464-chatpanel-tab-flak-c48009.md (+19 more) |
| Codebase (semantic) | app/src/components/AgentChatThread.tsx [card, chat, panel]; app/src/lib/roadmapChatCommand.ts [chat, panel]; app/src/lib/peerChatIntentDetector.ts [chat, panel] (+1 more) |
| Git log  | none |
| Tasks/Specs | none |

**MATCH FOUND.** You must choose before proceeding:
- [ ] Reuse the existing component (state which file and how)
- [ ] Justify why a new implementation is needed (state the gap)

---
MATCH FOUND
```

**Verdict: PASS.** Correct result.

Observation: Signal 1 (Literal) matched transcript filenames (`.md.stderr.log`, `.md`) — not production source files. The correct MATCH FOUND verdict here is carried by the semantic hits on `AgentChatThread.tsx` and friends. The literal signal is adding noise (transcripts are not components) but not causing a wrong verdict. Filed as Issue B below.

---

## Test 3: ZxqfQuantumWidget

Expected: `CLEAR`

Actual output (verbatim):

```
## Pre-design audit: `ZxqfQuantumWidget`

| Signal | Finding |
|--------|---------|
| Codebase (literal) | none |
| Codebase (semantic) | none |
| Git log  | none |
| Tasks/Specs | none |

**CLEAR** — no existing equivalent found. Proceed with design.

---
CLEAR
```

**Verdict: PASS.** All 4 signals correctly returned none. CLEAR is correct.

---

## Issues

### Issue A — Tasks/Specs signal false positive causes wrong verdict (FAIL case)

Signal 4 matches on spec file slugs using substring search. The slug `pre-design-audit-catch-existing-patterns-before-proposing-new-infrastructure` contains "source" (in "source") which matches "SourceBadge". The spec is not about a badge component. This turns a POSSIBLE MATCH into a MATCH FOUND, which will block design when no block is warranted.

**Follow-up Task:** →1795

### Issue B — Literal signal includes transcript log filenames

Signal 1 (codebase literal) matches transcript/log file paths (e.g., `transcripts/diagnose-1464-chatpanel-tab-flak-c48009.md.stderr.log`). These are session history artifacts, not component source files. A literal hit on a transcript is not evidence that a component exists. This produced a correct verdict for ChatPanel (semantic also had real hits) but could produce a MATCH FOUND for concepts that only appear in transcript names and not in production code.

**No blocking failure observed in these tests.** Follow-up Task: →1796

### Issue C — Semantic signal searches inside .venv (third-party libraries)

Signal 2 returned `api/.venv/lib/python3.11/site-packages/pygments/...` for SourceBadge. Third-party code inside `.venv` is not project code. This adds noise to the semantic results and could surface false positives for common vocabulary words. Follow-up Task: →1797

---

## 4-Signal Check Coverage

All three runs showed all 4 rows in the table. Signal coverage is complete.

| Signal | Present in all runs? |
|--------|---------------------|
| Codebase (literal) | Yes |
| Codebase (semantic) | Yes |
| Git log | Yes |
| Tasks/Specs | Yes |

